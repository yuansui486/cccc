from fastapi.testclient import TestClient

from cccc_desensitization.sidecar import app


def test_health_does_not_require_model_initialization() -> None:
    with TestClient(app) as client:
        response = client.get("/internal/healthz")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_business_routes_require_internal_token(monkeypatch) -> None:
    monkeypatch.setenv("CCCC_DESENSITIZATION_TOKEN", "TOKEN")
    with TestClient(app) as client:
        response = client.get("/api/v1/desensitization/models/status")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_sidecar_token"


def test_business_routes_reject_missing_internal_token(monkeypatch) -> None:
    monkeypatch.delenv("CCCC_DESENSITIZATION_TOKEN", raising=False)
    with TestClient(app) as client:
        response = client.get("/api/v1/desensitization/models/status")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_sidecar_token"
