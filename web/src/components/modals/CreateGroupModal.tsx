import { useTranslation } from "react-i18next";
import { useId } from "react";
import type { DoneHubTeamPreset } from "../../services/doneHub";
import { DirItem } from "../../types";
import { TemplatePreviewDetails } from "../TemplatePreviewDetails";
import type { TemplatePreviewDetailsProps } from "../TemplatePreviewDetails";
import { useModalA11y } from "../../hooks/useModalA11y";
import { FolderIcon, PlusIcon } from "../Icons";
import { Button } from "../ui/button";
import { Input } from "../ui/input";
import { Surface } from "../ui/surface";

const TEAM_PRESET_ICON_URLS = [
  "team-presets/1_team.png",
  "team-presets/2_产品调研.png",
  "team-presets/3_代码编写.png",
  "team-presets/4_个人助理.png",
  "team-presets/5_人事招聘.png",
  "team-presets/6_私域销售.png",
];

function getTeamPresetIconUrl(index: number): string {
  const relativePath = TEAM_PRESET_ICON_URLS[index % TEAM_PRESET_ICON_URLS.length];
  return `${import.meta.env.BASE_URL}${relativePath}`;
}

export interface CreateGroupModalProps {
  isOpen: boolean;
  busy: string;

  dirItems: DirItem[];
  currentDir: string;
  parentDir: string | null;
  showDirBrowser: boolean;
  teamPresets: DoneHubTeamPreset[];
  teamPresetsBusy: boolean;
  teamPresetsError: string;
  selectedTeamPresetSlug: string;

  createGroupPath: string;
  setCreateGroupPath: (path: string) => void;
  createGroupName: string;
  setCreateGroupName: (name: string) => void;
  createGroupTemplateFile: File | null;
  templatePreview: TemplatePreviewDetailsProps["template"] | null;
  templateError: string;
  templateBusy: boolean;
  onSelectTemplate: (file: File | null) => void;
  onSelectTeamPreset: (preset: DoneHubTeamPreset) => void;

  dirBrowseError?: string;
  onFetchDirContents: (path: string) => void;
  onCreateGroup: () => void;
  onClose: () => void;
  onCancelAndReset: () => void;
}

export function CreateGroupModal({
  isOpen,
  busy,
  dirItems,
  currentDir,
  parentDir,
  showDirBrowser,
  teamPresets,
  teamPresetsBusy,
  teamPresetsError,
  selectedTeamPresetSlug,
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
  onSelectTeamPreset,
  onFetchDirContents,
  onCreateGroup,
  onClose,
  onCancelAndReset,
}: CreateGroupModalProps) {
  const { t } = useTranslation("modals");
  const { modalRef } = useModalA11y(isOpen, onClose);
  const blueprintInputId = useId();
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
        <div className="px-6 py-4 border-b safe-area-inset-top border-[var(--glass-border-subtle)] glass-header">
          <div id="create-group-title" className="text-lg font-semibold text-[var(--color-text-primary)]">
            {t("createGroup.title")}
          </div>
          <div className="text-sm mt-1 text-[var(--color-text-muted)]">{t("createGroup.subtitle")}</div>
        </div>
        <div className="p-6 space-y-5 overflow-y-auto min-h-0 flex-1 bg-[linear-gradient(180deg,rgb(250,250,249),rgb(244,244,243))] dark:bg-[linear-gradient(180deg,rgba(24,24,26,0.98),rgba(14,14,16,1))]">
          <div>
            <label className="block text-xs font-medium mb-2 text-[var(--color-text-muted)]">{t("createGroup.quickSelect")}</label>
            <div className="grid grid-cols-1 gap-2">
              {teamPresetsBusy ? (
                <div className="rounded-xl px-3 py-3 text-sm glass-card text-[var(--color-text-muted)]">
                  {t("createGroup.presetsLoading")}
                </div>
              ) : teamPresetsError ? (
                <div className="rounded-xl px-3 py-3 text-sm border border-rose-500/30 bg-rose-500/10 text-rose-600 dark:text-rose-400">
                  {teamPresetsError}
                </div>
              ) : teamPresets.length > 0 ? (
                teamPresets.slice(0, 8).map((preset, index) => {
                  const slug = String(preset.slug || preset.id || "").trim();
                  const title = String(preset.name || preset.title || slug).trim();
                  const summary = preset.config_summary && typeof preset.config_summary === "object" ? preset.config_summary : {};
                  const configTitle = String(summary.title || "").trim();
                  const iconUrl = getTeamPresetIconUrl(index);
                  return (
                  <button
                    key={slug || title}
                    type="button"
                    className={`flex items-center gap-3 px-3 py-2.5 rounded-xl transition-colors text-left min-h-[64px] glass-card ${
                      selectedTeamPresetSlug === slug ? "border-[var(--glass-accent-border)] bg-[var(--glass-accent-bg)] shadow-[var(--glass-accent-shadow)]" : ""
                    }`}
                    onClick={() => onSelectTeamPreset(preset)}
                  >
                    <span className="flex h-11 w-11 shrink-0 items-center justify-center overflow-hidden rounded-xl border border-[var(--glass-accent-border)] bg-[var(--glass-accent-bg)]">
                      <img
                        src={iconUrl}
                        alt=""
                        aria-hidden="true"
                        className="h-full w-full object-cover"
                        loading="lazy"
                      />
                    </span>
                    <div className="min-w-0">
                      <div className="text-sm font-medium truncate text-[var(--color-text-secondary)]">{title}</div>
                      <div className="text-[10px] truncate text-[var(--color-text-muted)]">
                        {configTitle || preset.description_short || preset.description || t("createGroup.presetNoDescription")}
                      </div>
                    </div>
                  </button>
                  );
                })
              ) : (
                <div className="rounded-xl px-3 py-3 text-sm glass-card text-[var(--color-text-muted)]">
                  {t("createGroup.presetsEmpty")}
                </div>
              )}
            </div>
          </div>
          <div>
            <label className="block text-xs font-medium mb-2 text-[var(--color-text-muted)]">{t("createGroup.projectDirectory")}</label>
            <div className="flex gap-2">
              <Input
                className="flex-1 font-mono"
                value={createGroupPath}
                onChange={(e) => setCreateGroupPath(e.target.value)}
                placeholder={t("createGroup.pathPlaceholder")}
                autoFocus
              />
              <Button
                variant="secondary"
                onClick={() => onFetchDirContents(createGroupPath || "~")}
              >
                {t("createGroup.findWindowsPath")}
              </Button>
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
                      }}
                    >
                      <span className="text-[var(--color-text-muted)]"><FolderIcon size={16} /></span>
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
                          onFetchDirContents(item.path);
                        }}
                      >
                        <span className="text-[var(--color-text-secondary)]"><FolderIcon size={16} /></span>
                        <span className="text-sm text-[var(--color-text-secondary)]">{item.name}</span>
                      </button>
                    ))}
                </>
              )}
            </div>
          )}
          <div>
            <label className="block text-xs font-medium mb-2 text-[var(--color-text-muted)]">{t("createGroup.groupName")}</label>
            <Input
              value={createGroupName}
              onChange={(e) => setCreateGroupName(e.target.value)}
              placeholder={t("createGroup.groupNamePlaceholder")}
            />
          </div>

          <div>
              <label className="block text-xs font-medium mb-2 text-[var(--color-text-muted)]">
                {t("createGroup.blueprintLabel")}
              </label>
            <Surface
              radius="md"
              className="border-black/8 bg-[linear-gradient(180deg,rgba(255,255,255,0.995),rgba(250,248,245,0.96))] px-3 py-2.5 shadow-[0_18px_44px_-34px_rgba(15,23,42,0.18)] dark:border-white/10 dark:bg-[linear-gradient(180deg,rgba(24,26,31,0.9),rgba(13,14,18,0.98))]"
            >
              <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
                <input
                  id={blueprintInputId}
                  key={createGroupTemplateFile ? createGroupTemplateFile.name : "none"}
                  type="file"
                  accept=".yaml,.yml,.json"
                  className="hidden"
                  disabled={templateBusy || busy === "create"}
                  onChange={(e) => {
                    const f = e.target.files && e.target.files.length > 0 ? e.target.files[0] : null;
                    onSelectTemplate(f);
                  }}
                />
                <label
                  htmlFor={blueprintInputId}
                  className={`inline-flex min-h-[36px] cursor-pointer items-center justify-center rounded-xl px-3 py-1.5 text-sm font-medium transition-colors ${
                    templateBusy || busy === "create"
                      ? "pointer-events-none opacity-50 border border-[var(--glass-border-subtle)] bg-[var(--glass-panel-bg)] text-[var(--color-text-muted)]"
                      : "glass-btn-accent text-[var(--color-accent-primary)]"
                  }`}
                >
                  <PlusIcon className="mr-2 h-4 w-4" />
                  {t("common:chooseFile", "选择文件")}
                </label>
                <div className="min-w-0 flex-1 text-sm text-[var(--color-text-secondary)]">
                  <div className="truncate">
                    {createGroupTemplateFile ? createGroupTemplateFile.name : t("common:noFileChosen", "未选择文件")}
                  </div>
                  <div className="text-[11px] leading-tight text-[var(--color-text-muted)]">{t("common:fileTypeYamlJson", "支持 YAML / YML / JSON")}</div>
                </div>
                {createGroupTemplateFile && (
                  <Button
                    type="button"
                    className="sm:ml-auto"
                    size="sm"
                    variant="secondary"
                    disabled={templateBusy || busy === "create"}
                    onClick={() => onSelectTemplate(null)}
                  >
                    {t("common:reset")}
                  </Button>
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
                    hideDetails={true}
                    wrap={false}
                  />
                </div>
              )}
            </Surface>
          </div>
        </div>
        <div className="px-6 py-4 border-t border-[var(--glass-border-subtle)] glass-header">
          <div className="flex gap-3">
            <Button
              className="flex-1"
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
            </Button>
            <Button
              variant="secondary"
              onClick={onCancelAndReset}
            >
              {t("common:cancel")}
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
