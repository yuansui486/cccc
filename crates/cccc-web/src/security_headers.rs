use axum::extract::{Request, State};
use axum::http::{HeaderValue, header};
use axum::middleware::Next;
use axum::response::{IntoResponse, Response};

use crate::AppState;

const HSTS_VALUE: &str = "max-age=31536000";

pub(crate) fn desktop_binding_enabled(
    requested: Option<&str>,
    address: std::net::SocketAddr,
) -> bool {
    requested == Some("1")
        && address.ip() == std::net::IpAddr::V4(std::net::Ipv4Addr::LOCALHOST)
        && address.port() == 19001
}

fn frame_policy(ancestor: Option<&str>, desktop: bool) -> String {
    let mut policy = String::from("frame-ancestors 'self'");
    if let Some(origin) = ancestor {
        policy.push(' ');
        policy.push_str(origin);
    }
    if desktop {
        policy.push_str(" http://tauri.localhost");
        #[cfg(debug_assertions)]
        policy.push_str(" http://localhost:1420");
    }
    policy
}

pub async fn apply(State(state): State<AppState>, request: Request, next: Next) -> Response {
    let secure = crate::request_origin::is_https(&state, request.headers());
    let (ancestor, desktop, mut response) =
        match crate::connect_frames::frame_ancestor(&state, &request) {
            Ok(ancestor) => (ancestor, state.desktop_embedding, next.run(request).await),
            Err(error) => (None, false, error.into_response()),
        };
    let headers = response.headers_mut();
    // Multiple CSP policies are enforced together. Preserve stricter resource
    // policies (such as sandboxed workspace SVG) while adding frame authority.
    headers.append(
        header::HeaderName::from_static("content-security-policy"),
        HeaderValue::from_str(&frame_policy(ancestor.as_deref(), desktop))
            .ok()
            .unwrap_or_else(|| HeaderValue::from_static("frame-ancestors 'self'")),
    );
    if ancestor.is_none() && !desktop {
        headers.insert(
            header::HeaderName::from_static("x-frame-options"),
            HeaderValue::from_static("SAMEORIGIN"),
        );
    } else {
        headers.remove(header::HeaderName::from_static("x-frame-options"));
    }
    headers.insert(
        header::HeaderName::from_static("x-content-type-options"),
        HeaderValue::from_static("nosniff"),
    );
    headers.insert(
        header::REFERRER_POLICY,
        HeaderValue::from_static("no-referrer"),
    );
    headers.insert(
        header::HeaderName::from_static("permissions-policy"),
        HeaderValue::from_static("camera=(self), microphone=(self), geolocation=()"),
    );
    if secure {
        headers.insert(
            header::STRICT_TRANSPORT_SECURITY,
            HeaderValue::from_static(HSTS_VALUE),
        );
    }
    response
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::body::Body;
    use axum::http::StatusCode;
    use tower::ServiceExt;

    #[test]
    fn desktop_opt_in_requires_exact_actual_loopback_binding() {
        for address in [
            "0.0.0.0:19001",
            "127.0.0.1:8848",
            "127.0.0.2:19001",
            "[::1]:19001",
        ] {
            assert!(!desktop_binding_enabled(
                Some("1"),
                address.parse().expect("address")
            ));
        }
        let address = "127.0.0.1:19001".parse().expect("address");
        assert!(desktop_binding_enabled(Some("1"), address));
        for requested in [None, Some("0"), Some("true"), Some("1 ")] {
            assert!(!desktop_binding_enabled(requested, address));
        }
    }

    #[test]
    fn desktop_policy_keeps_verified_connect_authority_and_fixed_origins() {
        assert_eq!(frame_policy(None, false), "frame-ancestors 'self'");
        let policy = frame_policy(Some("https://verified.example"), true);
        assert!(
            policy.starts_with(
                "frame-ancestors 'self' https://verified.example http://tauri.localhost"
            )
        );
        assert_eq!(
            policy.contains("http://localhost:1420"),
            cfg!(debug_assertions)
        );
        assert!(!policy.contains('*'));
    }

    async fn fixture(desktop: bool) -> (tempfile::TempDir, AppState) {
        let temp = tempfile::tempdir().expect("tempdir");
        let home = cccc_core::HomeLayout::from_path(temp.path().join("home")).expect("home");
        home.initialize().expect("initialize");
        let (_, _, _, mut state) = crate::app_with_shutdown(
            home,
            tokio::sync::broadcast::channel(1).0,
            crate::WebMode::Normal,
            None,
            crate::LiveBinding {
                host: "127.0.0.1".into(),
                port: 19001,
            },
            "test".into(),
        );
        assert!(
            !state.desktop_embedding,
            "constructing a router never opts in"
        );
        state.desktop_embedding = desktop;
        (temp, state)
    }

    #[tokio::test]
    async fn desktop_headers_preserve_sandbox_and_remove_conflicting_frame_options() {
        for desktop in [false, true] {
            let (_temp, state) = fixture(desktop).await;
            let app = axum::Router::new()
                .route(
                    "/",
                    axum::routing::get(|| async {
                        [
                            ("content-security-policy", "sandbox; default-src 'none'"),
                            ("x-frame-options", "SAMEORIGIN"),
                        ]
                    }),
                )
                .layer(axum::middleware::from_fn_with_state(state, apply));
            let response = app
                .oneshot(Request::get("/").body(Body::empty()).expect("request"))
                .await
                .expect("response");
            let policies: Vec<_> = response
                .headers()
                .get_all("content-security-policy")
                .iter()
                .map(|value| value.to_str().expect("CSP"))
                .collect();
            assert!(policies.contains(&"sandbox; default-src 'none'"));
            assert!(policies.contains(&frame_policy(None, desktop).as_str()));
            assert_eq!(response.headers().contains_key("x-frame-options"), !desktop);
            assert_eq!(
                response.headers()["permissions-policy"],
                "camera=(self), microphone=(self), geolocation=()"
            );
        }
    }

    #[tokio::test]
    async fn desktop_embedding_never_bypasses_connect_proof_validation() {
        let (_temp, state) = fixture(true).await;
        for path in [
            "/ui/connect",
            "/ui/connect?proof=invalid",
            "/api/v1/groups/test?connect_frame=unknown",
        ] {
            let response = crate::router_for_state(state.clone())
                .oneshot(Request::get(path).body(Body::empty()).expect("request"))
                .await
                .expect("response");
            assert!(matches!(
                response.status(),
                StatusCode::FORBIDDEN | StatusCode::BAD_REQUEST
            ));
            assert_eq!(
                response.headers()["content-security-policy"],
                "frame-ancestors 'self'"
            );
            assert_eq!(response.headers()["x-frame-options"], "SAMEORIGIN");
        }
    }
}
