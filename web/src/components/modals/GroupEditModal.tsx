import { useTranslation } from "react-i18next";
import { useCopyFeedback } from "../../hooks/useCopyFeedback";
import { useModalA11y } from "../../hooks/useModalA11y";
import { useIMEComposition } from "../../hooks/useIMEComposition";
import { Button } from "../ui/button";
import { Input } from "../ui/input";
import { Surface } from "../ui/surface";
import { Textarea } from "../ui/textarea";

export interface GroupEditModalProps {
  isOpen: boolean;
  busy: string;
  groupId: string;
  ccccHome: string;
  projectRoot: string;
  title: string;
  topic: string;
  onChangeTitle: (title: string) => void;
  onChangeTopic: (topic: string) => void;
  onSave: () => void;
  onCancel: () => void;
  onDelete: () => void;
}

export function GroupEditModal({
  isOpen,
  busy,
  groupId,
  ccccHome,
  projectRoot,
  title,
  topic,
  onChangeTitle,
  onChangeTopic,
  onSave,
  onCancel,
  onDelete,
}: GroupEditModalProps) {
  const { t } = useTranslation("modals");
  const copyWithFeedback = useCopyFeedback();
  const { modalRef } = useModalA11y(isOpen, onCancel);
  const imeTitle = useIMEComposition({ value: title, onChange: onChangeTitle });
  const imeTopic = useIMEComposition({ value: topic, onChange: onChangeTopic });
  if (!isOpen) return null;

  const homeRoot = String(ccccHome || "").trim();
  const gid = String(groupId || "").trim();
  const groupDataDir = homeRoot && gid ? `${homeRoot}/groups/${gid}` : "";
  const groupConfigFile = groupDataDir ? `${groupDataDir}/group.yaml` : "";
  const groupLedgerFile = groupDataDir ? `${groupDataDir}/ledger.jsonl` : "";
  const metadataRows = [
    {
      label: t("groupEdit.groupId"),
      value: groupId || "—",
      copyValue: groupId,
      title: t("groupEdit.copyGroupId"),
    },
    {
      label: t("groupEdit.projectRoot"),
      value: projectRoot || t("groupEdit.noScopeAttached"),
      copyValue: projectRoot,
      title: t("groupEdit.copyProjectRoot"),
    },
    {
      label: t("groupEdit.groupDataDirectory"),
      value: groupDataDir || "—",
      copyValue: groupDataDir,
      title: t("groupEdit.copyDataDir"),
    },
    {
      label: t("groupEdit.groupConfigFile"),
      value: groupConfigFile || "—",
      copyValue: groupConfigFile,
      title: t("groupEdit.copyConfigFile"),
    },
    {
      label: t("groupEdit.groupLedgerFile"),
      value: groupLedgerFile || "—",
      copyValue: groupLedgerFile,
      title: t("groupEdit.copyLedgerFile"),
    },
  ];

  return (
    <div
      className="fixed inset-0 backdrop-blur-sm flex items-stretch sm:items-start justify-center p-0 sm:p-6 z-50 animate-fade-in glass-overlay"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onCancel();
      }}
      role="dialog"
      aria-modal="true"
      aria-labelledby="group-edit-title"
    >
      <div
        ref={modalRef}
        className="w-full h-full sm:h-auto sm:max-w-2xl sm:mt-12 sm:max-h-[calc(100dvh-6rem)] shadow-2xl animate-scale-in flex flex-col overflow-hidden rounded-none sm:rounded-2xl glass-modal"
      >
        <div className="border-b px-6 py-4 safe-area-inset-top border-[var(--glass-border-subtle)] sm:px-7 glass-header">
          <div id="group-edit-title" className="text-xl font-semibold text-[var(--color-text-primary)]">
            {t("groupEdit.title")}
          </div>
        </div>
        <div className="scrollbar-hide flex-1 overflow-y-auto bg-[linear-gradient(180deg,rgb(250,250,249),rgb(244,244,243))] px-6 pb-6 pt-4 dark:bg-[linear-gradient(180deg,rgba(24,24,26,0.98),rgba(14,14,16,1))] sm:px-7 sm:pb-7 sm:pt-5">
          <div className="space-y-5">
          <div>
            <label className="mb-2 block text-xs font-medium uppercase tracking-[0.08em] text-[var(--color-text-muted)]">
              {t("groupEdit.nameLabel")}
            </label>
            <Input
              className="py-3 text-base min-h-[52px]"
              value={imeTitle.value}
              onChange={imeTitle.onChange}
              onCompositionStart={imeTitle.onCompositionStart}
              onCompositionEnd={imeTitle.onCompositionEnd}
              placeholder={t("groupEdit.groupNamePlaceholder")}
            />
          </div>
          <div>
            <label className="mb-2 block text-xs font-medium uppercase tracking-[0.08em] text-[var(--color-text-muted)]">
              {t("groupEdit.descriptionLabel")}
            </label>
            <Textarea
              className="min-h-[92px] resize-none px-4 py-3 text-base leading-6"
              value={imeTopic.value}
              onChange={imeTopic.onChange}
              onCompositionStart={imeTopic.onCompositionStart}
              onCompositionEnd={imeTopic.onCompositionEnd}
              placeholder={t("groupEdit.descriptionPlaceholder")}
            />
          </div>
          <Surface className="overflow-hidden border-black/10 bg-[linear-gradient(180deg,rgba(255,255,255,0.995),rgba(248,248,247,0.96))] shadow-[0_24px_60px_-42px_rgba(15,23,42,0.18)] dark:border-white/10 dark:bg-[linear-gradient(180deg,rgba(24,24,26,0.92),rgba(14,14,16,0.98))]" padding="none">
            <div className="border-b border-black/10 px-5 py-4 sm:px-6 bg-black/[0.025] dark:border-white/10 dark:bg-white/[0.04]">
              <div className="text-sm font-semibold text-[var(--color-text-primary)]">
                {t("groupEdit.projectRoot")}
              </div>
              <div className="mt-1 text-xs text-[var(--color-text-muted)]">
                {t("groupEdit.groupDataDirectory")} / {t("groupEdit.groupConfigFile")} / {t("groupEdit.groupLedgerFile")}
              </div>
            </div>
            <div className="divide-y divide-[var(--glass-border-subtle)]">
              {metadataRows.map((row) => (
                <div
                  key={row.label}
                  className="grid grid-cols-1 gap-3 px-5 py-3 sm:grid-cols-[140px_minmax(0,1fr)_auto] sm:items-center sm:gap-4 sm:px-6"
                >
                  <div className="text-[11px] font-medium uppercase tracking-[0.08em] text-[var(--color-text-muted)]">
                    {row.label}
                  </div>
                  <div className="min-w-0 font-mono text-[13px] leading-6 text-[var(--color-text-primary)] sm:truncate">
                    {row.value}
                  </div>
                  <Button
                    className="self-start sm:self-auto"
                    size="sm"
                    variant="outline"
                    onClick={async () => {
                      const ok = await copyWithFeedback(row.copyValue, {
                        successMessage: t("common:copied"),
                        errorMessage: t("common:copyFailed"),
                      });
                      if (!ok) return;
                    }}
                    disabled={!row.copyValue}
                    title={row.title}
                    type="button"
                  >
                    {t("common:copy")}
                  </Button>
                </div>
              ))}
            </div>
          </Surface>
          <div className="flex flex-wrap items-center gap-3 pt-2">
            <Button
              className="min-w-[180px] flex-1 border-blue-600 bg-blue-600 text-white shadow-[var(--glass-accent-shadow)] hover:border-blue-700 hover:bg-blue-700 dark:border-blue-400 dark:bg-blue-500 dark:hover:border-blue-300 dark:hover:bg-blue-400"
              size="lg"
              onClick={onSave}
              disabled={!title.trim() || busy === "group-update"}
            >
              {t("common:save")}
            </Button>
            <Button
              size="lg"
              variant="outline"
              className="border-black/10 bg-white/90 text-[rgb(35,36,37)] shadow-[0_10px_24px_-22px_rgba(15,23,42,0.22)] hover:bg-[rgb(245,245,245)] dark:border-white/12 dark:bg-white/[0.06] dark:text-white dark:hover:bg-white/[0.1]"
              onClick={onCancel}
            >
              {t("common:cancel")}
            </Button>
            <Button
              size="lg"
              variant="destructive"
              className="ml-auto border-rose-500/30 bg-rose-500/12 text-rose-600 hover:bg-rose-500/18 dark:text-rose-300"
              onClick={() => {
                onCancel();
                onDelete();
              }}
              disabled={busy === "group-delete"}
              title={t("groupEdit.deleteTitle")}
            >
              {t("common:delete")}
            </Button>
          </div>
          </div>
        </div>
      </div>
    </div>
  );
}
