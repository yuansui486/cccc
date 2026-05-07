import { useState } from "react";
import { useTranslation } from "react-i18next";
import * as api from "../../../services/api";
import {
  labelClass,
  primaryButtonClass,
  secondaryButtonClass,
  settingsWorkspaceActionBarClass,
  settingsWorkspaceBodyClass,
  settingsWorkspaceHeaderClass,
  settingsWorkspacePanelClass,
  settingsWorkspaceShellClass,
  settingsWorkspaceSoftPanelClass,
} from "./types";
import { TemplatePreviewDetails } from "../../TemplatePreviewDetails";
import type { TemplatePreviewDetailsProps } from "../../TemplatePreviewDetails";

interface BlueprintTabProps {
  isDark: boolean;
  groupId?: string;
  groupTitle?: string;
}

type TemplatePreviewResult = {
  template?: TemplatePreviewDetailsProps["template"];
  diff?: NonNullable<TemplatePreviewDetailsProps["diff"]>;
};

function downloadTextFile(filename: string, text: string) {
  const blob = new Blob([text], { type: "text/yaml;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

export function BlueprintTab({ isDark, groupId, groupTitle }: BlueprintTabProps) {
  const { t } = useTranslation("settings");
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<TemplatePreviewResult | null>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [exportInfo, setExportInfo] = useState("");

  const canUse = !!groupId;

  const loadPreview = async (f: File) => {
    if (!groupId) return;
    setBusy(true);
    setErr("");
    setPreview(null);
    try {
      const resp = await api.previewGroupTemplate(groupId, f);
      if (!resp.ok) {
        setErr(resp.error?.message || t("blueprint.failedToPreview"));
        return;
      }
      setPreview(resp.result as TemplatePreviewResult);
    } catch {
      setErr(t("blueprint.failedToPreview"));
    } finally {
      setBusy(false);
    }
  };

  const handleExport = async () => {
    if (!groupId) return;
    setBusy(true);
    setErr("");
    setExportInfo("");
    try {
      const resp = await api.exportGroupTemplate(groupId);
      if (!resp.ok) {
        setErr(resp.error?.message || t("blueprint.failedToExport"));
        return;
      }
      const filename = resp.result?.filename || `cccc-group-template--${groupTitle || groupId}.yaml`;
      downloadTextFile(filename, String(resp.result?.template || ""));
      setExportInfo(t("blueprint.downloaded"));
      window.setTimeout(() => setExportInfo(""), 1200);
    } catch {
      setErr(t("blueprint.failedToExport"));
    } finally {
      setBusy(false);
    }
  };

  const handleImportReplace = async () => {
    if (!groupId || !file) return;
    const ok = window.confirm(t("blueprint.replaceConfirm", { filename: file.name }));
    if (!ok) return;

    setBusy(true);
    setErr("");
    try {
      const resp = await api.importGroupTemplateReplace(groupId, file);
      if (!resp.ok) {
        setErr(resp.error?.message || t("blueprint.failedToImport"));
        return;
      }
      setFile(null);
      setPreview(null);
      setExportInfo(t("blueprint.applied"));
      window.setTimeout(() => setExportInfo(""), 1200);
    } catch {
      setErr(t("blueprint.failedToImport"));
    } finally {
      setBusy(false);
    }
  };

  if (!canUse) {
    return (
      <div className="text-sm text-[var(--color-text-secondary)]">
        {t("blueprint.openFromGroup")}
      </div>
    );
  }

  const diff = preview?.diff;
  const tpl = preview?.template;
  const filePickerId = "blueprint-import-file-input";
  const canResetImportState = !!file || !!preview || !!err;

  return (
    <div className="space-y-4">
      {err && <div className="text-sm text-red-600 dark:text-rose-300">{err}</div>}

      <div className={settingsWorkspaceShellClass(isDark)}>
        <div className={settingsWorkspaceHeaderClass(isDark)}>
          <div className="min-w-0">
            <div className="text-sm font-semibold text-[var(--color-text-primary)]">{t("blueprint.exportTitle")}</div>
            <div className="mt-1 text-xs text-[var(--color-text-tertiary)]">
              {t("blueprint.exportDescription")}
            </div>
          </div>
        </div>

        <div className={settingsWorkspaceActionBarClass(isDark)}>
          <button className={primaryButtonClass(busy)} onClick={handleExport} disabled={busy}>
            {t("blueprint.exportBlueprint")}
          </button>
          {exportInfo && <div className="text-xs text-emerald-700 dark:text-emerald-300">{exportInfo}</div>}
        </div>
      </div>

      <div className={settingsWorkspaceShellClass(isDark)}>
        <div className={settingsWorkspaceHeaderClass(isDark)}>
          <div className="min-w-0">
            <div className="text-sm font-semibold text-[var(--color-text-primary)]">{t("blueprint.importTitle")}</div>
            <div className="mt-1 text-xs text-[var(--color-text-tertiary)]">
              {t("blueprint.importDescription")}
            </div>
          </div>
        </div>

        <div className={settingsWorkspaceBodyClass}>
          <div className={settingsWorkspaceSoftPanelClass(isDark)}>
            <label className={labelClass(isDark)}>{t("blueprint.blueprintFile")}</label>
            <input
              key={file ? file.name : "none"}
              id={filePickerId}
              type="file"
              accept=".yaml,.yml,.json"
              className="sr-only"
              disabled={busy}
              onChange={(e) => {
                const f = e.target.files && e.target.files.length > 0 ? e.target.files[0] : null;
                setFile(f);
                setPreview(null);
                setErr("");
                if (f) void loadPreview(f);
              }}
            />
            <div className="mt-2 flex flex-wrap items-center gap-3">
              <label
                htmlFor={filePickerId}
                className={`${secondaryButtonClass()} ${busy ? "pointer-events-none opacity-50" : "cursor-pointer"}`}
                aria-disabled={busy}
              >
                {t("common:chooseFile", "Choose File")}
              </label>
              <div className={`min-w-0 text-sm ${file ? "text-[var(--color-text-primary)]" : "text-[var(--color-text-muted)]"}`}>
                <span className="block truncate">{file?.name || t("common:noFileChosen", "No file chosen")}</span>
              </div>
            </div>
            {busy && <div className="mt-3 text-xs text-[var(--color-text-muted)]">{t("blueprint.working")}</div>}
          </div>

          {tpl && diff && (
            <div className={settingsWorkspacePanelClass(isDark)}>
              <TemplatePreviewDetails isDark={isDark} template={tpl} diff={diff} wrap={false} />
            </div>
          )}
        </div>

        <div className={settingsWorkspaceActionBarClass(isDark)}>
          <button
            className={primaryButtonClass(busy)}
            onClick={handleImportReplace}
            disabled={busy || !file || !preview}
            title={!preview ? t("blueprint.pickFileFirst") : ""}
          >
            {t("blueprint.applyReplace")}
          </button>
          {canResetImportState ? (
            <button
              type="button"
              className={secondaryButtonClass()}
              disabled={busy}
              onClick={() => {
                setFile(null);
                setPreview(null);
                setErr("");
              }}
            >
              {t("common:reset")}
            </button>
          ) : null}
        </div>
      </div>
    </div>
  );
}
