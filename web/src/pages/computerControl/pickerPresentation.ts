import type { ElementPickerSession } from "../../services/api/computerControl";

export function pickerCapabilitySummary(session: ElementPickerSession): string {
  const code = String(session.diagnostics?.code || "");
  if (code === "native_uia_starting" || session.native_available === undefined) {
    return "正在检查原生元素读取和桌面高亮能力";
  }
  if (session.native_available && session.overlay_available) {
    return "原生元素读取和桌面高亮均可用";
  }
  if (session.native_available) {
    return "元素读取可用，但桌面高亮框不可用";
  }
  const message = String(session.diagnostics?.message || session.warning || "Windows 原生元素读取不可用");
  return `${message}；仍可使用“读取快照”`;
}

export function formatPickerDiagnostics(session: ElementPickerSession): string {
  return JSON.stringify({
    session_id: session.session_id,
    status: session.status,
    native_available: Boolean(session.native_available),
    overlay_available: Boolean(session.overlay_available),
    warning: session.warning || "",
    diagnostics: session.diagnostics || {},
  }, null, 2);
}
