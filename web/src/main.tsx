import React, { lazy, Suspense } from "react";
import ReactDOM from "react-dom/client";
import { AuthGate } from "./components/AuthGate";
import { isCapabilityCenterPath } from "./components/capabilities/capabilityCenterRoute";
import "./i18n";
import "./index.css";
import { useBrandingStore } from "./stores";
import { applyBrandingToDocument, DEFAULT_WEB_BRANDING } from "./utils/branding";
import { applyTextScale, getStoredTextScale } from "./utils/textScale";

const App = lazy(() => import("./App"));
const CapabilityCenterStandaloneApp = lazy(() =>
  import("./components/capabilities/CapabilityCenterStandaloneApp").then((module) => ({ default: module.CapabilityCenterStandaloneApp }))
);
const VideoEditorStandaloneApp = lazy(() =>
  import("./pages/remotionEditor/VideoEditorStandaloneApp").then((module) => ({ default: module.VideoEditorStandaloneApp }))
);

// v0.4: We intentionally do NOT use Service Workers.
// Reason: SW caching frequently causes "stale UI" bugs in an ops/admin console.
//
// Best-effort cleanup: unregister any legacy SWs scoped to `/ui/` so clients
// recover automatically after upgrades.
if ("serviceWorker" in navigator && typeof navigator.serviceWorker.getRegistrations === "function") {
  void navigator.serviceWorker.getRegistrations().then((registrations) => {
    for (const r of registrations) {
      try {
        const scope = String(r.scope || "");
        // Only touch our own scope to avoid impacting unrelated SWs on the same origin.
        if (scope.includes("/ui/")) {
          void r.unregister();
        }
      } catch {
        // ignore
      }
    }
  });
}

applyBrandingToDocument(DEFAULT_WEB_BRANDING);
applyTextScale(getStoredTextScale());
void useBrandingStore.getState().refreshBranding();
const isCapabilityCenterPage = isCapabilityCenterPath(window.location.pathname);
const isVideoEditorPage = window.location.pathname === "/video-editor" || window.location.pathname.startsWith("/video-editor/");

ReactDOM.createRoot(document.getElementById("root")!).render(
  <AuthGate>
    <Suspense fallback={null}>
      {isVideoEditorPage ? <VideoEditorStandaloneApp /> : isCapabilityCenterPage ? <CapabilityCenterStandaloneApp /> : <App />}
    </Suspense>
  </AuthGate>,
);
