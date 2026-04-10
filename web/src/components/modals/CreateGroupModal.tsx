import { useTranslation } from "react-i18next";
import { DirItem, DirSuggestion } from "../../types";
import { TemplatePreviewDetails } from "../TemplatePreviewDetails";
import type { TemplatePreviewDetailsProps } from "../TemplatePreviewDetails";
import { useModalA11y } from "../../hooks/useModalA11y";

export interface CreateGroupModalProps {
  isOpen: boolean;
  busy: string;

  dirSuggestions: DirSuggestion[];
  dirItems: DirItem[];
  currentDir: string;
  parentDir: string | null;
  showDirBrowser: boolean;

  createGroupPath: string;
  setCreateGroupPath: (path: string) => void;
  createGroupName: string;
  setCreateGroupName: (name: string) => void;
  createGroupTemplateFile: File | null;
  templatePreview: TemplatePreviewDetailsProps["template"] | null;
  templateError: string;
  templateBusy: boolean;
  onSelectTemplate: (file: File | null) => void;

  dirBrowseError?: string;
  onFetchDirContents: (path: string) => void;
  onCreateGroup: () => void;
  onClose: () => void;
  onCancelAndReset: () => void;
}

export function CreateGroupModal({
  isOpen,
  busy,
  dirSuggestions,
  dirItems,
  currentDir,
  parentDir,
  showDirBrowser,
  createGroupPath,
  setCreateGroupPath,
  createGroupName,
  setCreateGroupName,
  createGroupTemplateFile,
  templatePreview,
  templateError,
  templateBusy,
  dirBrowseError,
  onSelectTemplate,
  onFetchDirContents,
  onCreateGroup,
  onClose,
  onCancelAndReset,
}: CreateGroupModalProps) {
  const { t } = useTranslation("modals");
  const { modalRef } = useModalA11y(isOpen, onClose);
  if (!isOpen) return null;

  return (
    <div
      className="fixed inset-0 backdrop-blur-sm flex items-stretch sm:items-start justify-center p-0 sm:p-6 z-50 animate-fade-in glass-overlay"
      onPointerDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
      role="dialog"
      aria-modal="true"
      aria-labelledby="create-group-title"
    >
      <div
        ref={modalRef}
        className="w-full h-full sm:h-auto sm:max-w-lg sm:mt-16 shadow-2xl animate-scale-in overflow-hidden flex flex-col sm:max-h-[calc(100vh-8rem)] rounded-none sm:rounded-2xl glass-modal"
      >
        <div className="px-6 py-4 border-b safe-area-inset-top border-[var(--glass-border-subtle)]">
          <div id="create-group-title" className="text-lg font-semibold text-[var(--color-text-primary)]">
            {t("createGroup.title")}
          </div>
          <div className="text-sm mt-1 text-[var(--color-text-muted)]">{t("createGroup.subtitle")}</div>
        </div>
        <div className="p-6 space-y-5 overflow-y-auto min-h-0 flex-1">
          {dirSuggestions.length > 0 && !createGroupPath && (
            <div>
              <label className="block text-xs font-medium mb-2 text-[var(--color-text-muted)]">{t("createGroup.quickSelect")}</label>
              <div className="grid grid-cols-2 gap-2">
                {dirSuggestions.slice(0, 6).map((s) => (
                  <button
                    key={s.path}
                    className="flex items-center gap-2 px-3 py-2 rounded-xl transition-colors text-left min-h-[56px] glass-card"
                    onClick={() => {
                      setCreateGroupPath(s.path);
                      setCreateGroupName(s.path.split("/").filter(Boolean).pop() || "");
                      onFetchDirContents(s.path);
                    }}
                  >
                    <span className="text-lg">{s.icon}</span>
                    <div className="min-w-0">
                      <div className="text-sm font-medium truncate text-[var(--color-text-secondary)]">{s.name}</div>
                      <div className="text-[10px] truncate text-[var(--color-text-muted)]">{s.path}</div>
                    </div>
                  </button>
                ))}
              </div>
            </div>
          )}
          <div>
            <label className="block text-xs font-medium mb-2 text-[var(--color-text-muted)]">{t("createGroup.projectDirectory")}</label>
            <div className="flex gap-2">
              <input
                className="flex-1 rounded-xl px-4 py-2.5 text-sm font-mono min-h-[44px] transition-colors glass-input text-[var(--color-text-primary)]"
                value={createGroupPath}
                onChange={(e) => {
                  setCreateGroupPath(e.target.value);
                  const dirName = e.target.value.split("/").filter(Boolean).pop() || "";
                  if (!createGroupName || createGroupName === currentDir.split("/").filter(Boolean).pop()) {
                    setCreateGroupName(dirName);
                  }
                }}
                placeholder={t("createGroup.pathPlaceholder")}
                autoFocus
              />
              <button
                className="px-4 py-2 rounded-xl text-sm font-medium transition-colors min-h-[44px] glass-btn text-[var(--color-text-secondary)]"
                onClick={() => onFetchDirContents(createGroupPath || "~")}
              >
                {t("createGroup.browse")}
              </button>
            </div>
            <div className="mt-1 text-[11px] text-[var(--color-text-muted)]">
              {t("createGroup.pathAutoCreateHint")}
            </div>
          </div>
          {showDirBrowser && (
            <div className={`rounded-xl max-h-48 overflow-auto ${dirBrowseError ? "border border-rose-500/30 bg-rose-500/10" : "glass-panel"}`}>
              {dirBrowseError ? (
                <div className="px-3 py-3 text-sm text-rose-600 dark:text-rose-400">{dirBrowseError}</div>
              ) : (
                <>
                  {currentDir && (
                    <div className="px-3 py-1.5 border-b text-xs font-mono truncate border-[var(--glass-border-subtle)] bg-[var(--glass-tab-bg)] text-[var(--color-text-muted)]">
                      {currentDir}
                    </div>
                  )}
                  {parentDir && (
                    <button
                      className="w-full flex items-center gap-2 px-3 py-2 text-left border-b min-h-[44px] hover:bg-[var(--glass-tab-bg-hover)] border-[var(--glass-border-subtle)]"
                      onClick={() => {
                        onFetchDirContents(parentDir);
                        setCreateGroupPath(parentDir);
                        setCreateGroupName(parentDir.split("/").filter(Boolean).pop() || "");
                      }}
                    >
                      <span className="text-[var(--color-text-muted)]">📁</span>
                      <span className="text-sm text-[var(--color-text-muted)]">..</span>
                    </button>
                  )}
                  {dirItems.filter((d) => d.is_dir).length === 0 && (
                    <div className="px-3 py-4 text-center text-sm text-[var(--color-text-muted)]">{t("createGroup.noSubdirectories")}</div>
                  )}
                  {dirItems
                    .filter((d) => d.is_dir)
                    .map((item) => (
                      <button
                        key={item.path}
                        className="w-full flex items-center gap-2 px-3 py-2 text-left min-h-[44px] hover:bg-[var(--glass-tab-bg-hover)]"
                        onClick={() => {
                          setCreateGroupPath(item.path);
                          setCreateGroupName(item.name);
                          onFetchDirContents(item.path);
                        }}
                      >
                        <span className="text-blue-500">📁</span>
                        <span className="text-sm text-[var(--color-text-secondary)]">{item.name}</span>
                      </button>
                    ))}
                </>
              )}
            </div>
          )}
          <div>
            <label className="block text-xs font-medium mb-2 text-[var(--color-text-muted)]">{t("createGroup.groupName")}</label>
            <input
              className="w-full rounded-xl px-4 py-2.5 text-sm min-h-[44px] transition-colors glass-input text-[var(--color-text-primary)]"
              value={createGroupName}
              onChange={(e) => setCreateGroupName(e.target.value)}
              placeholder={t("createGroup.groupNamePlaceholder")}
            />
          </div>

          <div>
              <label className="block text-xs font-medium mb-2 text-[var(--color-text-muted)]">
                {t("createGroup.blueprintLabel")}
              </label>
            <div className="rounded-xl px-4 py-3 glass-panel">
              <div className="flex items-center gap-3">
                <input
                  key={createGroupTemplateFile ? createGroupTemplateFile.name : "none"}
                  type="file"
                  accept=".yaml,.yml,.json"
                  className="text-sm text-[var(--color-text-secondary)]"
                  disabled={templateBusy || busy === "create"}
                  onChange={(e) => {
                    const f = e.target.files && e.target.files.length > 0 ? e.target.files[0] : null;
                    onSelectTemplate(f);
                  }}
                />
                {createGroupTemplateFile && (
                  <button
                    type="button"
                    className="ml-auto px-3 py-2 rounded-lg text-sm min-h-[40px] transition-colors glass-btn text-[var(--color-text-secondary)]"
                    disabled={templateBusy || busy === "create"}
                    onClick={() => onSelectTemplate(null)}
                  >
                    {t("common:reset")}
                  </button>
                )}
              </div>
              {templateBusy && (
                <div className="mt-2 text-xs text-[var(--color-text-muted)]">{t("createGroup.loadingBlueprint")}</div>
              )}
              {!templateBusy && templateError && (
                <div className="mt-2 text-xs text-rose-600 dark:text-rose-400">{templateError}</div>
              )}
              {!templateBusy && createGroupTemplateFile && !!templatePreview && (
                <div className="mt-3">
                  <TemplatePreviewDetails
                    template={templatePreview}
                    detailsOpenByDefault={true}
                    wrap={false}
                  />
                </div>
              )}
              <div className="mt-2 text-[11px] text-[var(--color-text-muted)]">
                {t("createGroup.blueprintHint")}
              </div>
            </div>
          </div>
        </div>
        <div className="px-6 py-4 border-t border-[var(--glass-border-subtle)]">
          <div className="flex gap-3">
            <button
              className="flex-1 rounded-xl bg-blue-600 hover:bg-blue-500 text-white px-4 py-2.5 text-sm font-semibold shadow-lg disabled:opacity-50 transition-all min-h-[44px]"
              onClick={onCreateGroup}
              disabled={
                !createGroupPath.trim() ||
                busy === "create" ||
                templateBusy ||
                (!!createGroupTemplateFile && !templatePreview) ||
                (!!createGroupTemplateFile && !!templateError)
              }
            >
              {busy === "create" ? t("createGroup.creating") : createGroupTemplateFile ? t("createGroup.createFromBlueprint") : t("createGroup.createGroup")}
            </button>
            <button
              className="px-4 py-2.5 rounded-xl text-sm font-medium transition-colors min-h-[44px] glass-btn text-[var(--color-text-secondary)]"
              onClick={onCancelAndReset}
            >
              {t("common:cancel")}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
