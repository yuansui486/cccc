import type { GroupPromptInfo } from "../../../services/api";
import { classNames } from "../../../utils/classNames";
import type { ContextTranslator } from "../model";
import type { ContextModalUi } from "../ui";

interface DesktopPetViewProps {
  tr: ContextTranslator;
  ui: ContextModalUi;
  onUpdateSettings?: (settings: Record<string, unknown>) => Promise<boolean | void>;
  desktopPetEnabled: boolean;
  viewBusy: boolean;
  petHelpPrompt: GroupPromptInfo | null;
  petPersonaBusy: boolean;
  petPersonaError: string;
  petPersonaNotice: string;
  petPersonaDraft: string;
  hasPetPersonaUnsaved: boolean;
  onToggleDesktopPet: (enabled: boolean) => void;
  onLoadPetPersona: (force?: boolean) => Promise<GroupPromptInfo | null>;
  onSavePetPersona: () => void;
  onDiscardPetPersona: () => void;
  onPetPersonaChange: (value: string) => void;
}

export function DesktopPetView({
  tr,
  ui,
  onUpdateSettings,
  desktopPetEnabled,
  viewBusy,
  petHelpPrompt,
  petPersonaBusy,
  petPersonaError,
  petPersonaNotice,
  petPersonaDraft,
  hasPetPersonaUnsaved,
  onToggleDesktopPet,
  onLoadPetPersona,
  onSavePetPersona,
  onDiscardPetPersona,
  onPetPersonaChange,
}: DesktopPetViewProps) {
  if (!onUpdateSettings) {
    return (
      <section className={classNames(ui.surfaceClass, "p-4")}>
        <div className={classNames("text-sm", ui.mutedTextClass)}>
          {tr("context.desktopPetUnavailable", "Web Pet settings are unavailable in this context.")}
        </div>
      </section>
    );
  }

  return (
    <section className={classNames(ui.surfaceClass, "flex min-h-0 flex-1 flex-col p-4")}>
      <div className="flex min-h-0 flex-1 flex-col gap-4">
        <div>
          <div className={classNames("flex items-center gap-2 text-lg font-semibold", "text-[var(--color-text-primary)]")}>
            {tr("context.desktopPetTitle", "Web Pet")}
            <span className="rounded-md bg-cyan-500/15 px-2 py-0.5 text-xs font-semibold leading-none text-cyan-400">
              {tr("context.betaBadge", "Beta")}
            </span>
          </div>
          <div className={classNames("mt-1 text-sm", ui.subtleTextClass)}>
            {tr("context.desktopPetHint", "Show a floating web pet in the corner that reflects this team's status.")}
          </div>
        </div>

        <div className={classNames("flex flex-col gap-4 rounded-2xl border p-4", "glass-panel")}>
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <div className={classNames("text-sm font-medium", "text-[var(--color-text-primary)]")}>
                {tr("context.desktopPetSwitchLabel", "Enable Web Pet")}
              </div>
              <div className={classNames("mt-1 text-xs", ui.mutedTextClass)}>
                {tr("context.desktopPetTabHint", "Each enabled group shows its own Web Pet in the web UI.")}
              </div>
            </div>
            <button
              type="button"
              role="switch"
              aria-checked={desktopPetEnabled}
              aria-label={tr("context.desktopPetSwitchLabel", "Enable Web Pet")}
              onClick={() => onToggleDesktopPet(!desktopPetEnabled)}
              disabled={viewBusy}
              className={ui.switchTrackClass(desktopPetEnabled)}
            >
              <span className={ui.switchThumbClass(desktopPetEnabled)} />
            </button>
          </div>
        </div>

        <div className={classNames("flex min-h-0 flex-1 flex-col gap-4 rounded-2xl border p-4", "glass-panel")}>
          <div className="flex flex-col gap-2">
            <div className={classNames("text-sm font-medium", "text-[var(--color-text-primary)]")}>
              {tr("context.petPersonaTitle", "Pet Persona")}
            </div>
            <div className={classNames("text-xs", ui.mutedTextClass)}>
              {tr("context.petPersonaHint", "Stored in this group's CCCC_HELP.md as a `## @pet` block. When empty, the editor starts from the default seed and you can customize the pet's stable coordination style here.")}
            </div>
            {petHelpPrompt?.path ? (
              <div className={classNames("break-all text-[11px] leading-5 font-mono", ui.mutedTextClass)}>
                {petHelpPrompt.path}
              </div>
            ) : null}
          </div>

          {!petHelpPrompt && petPersonaBusy ? (
            <div className={classNames("rounded-xl border border-dashed px-3 py-4 text-sm", "glass-card text-[var(--color-text-muted)]")}>
              {tr("context.loadingPetPersona", "Loading pet persona…")}
            </div>
          ) : null}

          {!petHelpPrompt && !petPersonaBusy && petPersonaError ? (
            <div className="flex flex-col gap-3">
              <div className={classNames("rounded-xl border px-3 py-2 text-sm", "border-rose-500/30 bg-rose-500/15 text-rose-600 dark:text-rose-400")}>
                {petPersonaError}
              </div>
              <div>
                <button type="button" onClick={() => void onLoadPetPersona(true)} className={ui.buttonSecondaryClass}>
                  {tr("context.retryLoadPetPersona", "Retry")}
                </button>
              </div>
            </div>
          ) : null}

          {petHelpPrompt ? (
            <div className="flex min-h-0 flex-1 flex-col gap-4">
              {petPersonaError ? (
                <div className={classNames("rounded-xl border px-3 py-2 text-sm", "border-rose-500/30 bg-rose-500/15 text-rose-600 dark:text-rose-400")}>
                  {petPersonaError}
                </div>
              ) : null}

              {petPersonaNotice ? (
                <div className={classNames("rounded-xl border px-3 py-2 text-sm", "border-emerald-500/30 bg-emerald-500/10 text-emerald-600 dark:text-emerald-300")}>
                  {petPersonaNotice}
                </div>
              ) : null}

              <div className="min-h-0 flex-1">
                <textarea
                  className={classNames(
                    ui.textareaClass,
                    "h-full min-h-[18rem] resize-none font-mono text-[12px] leading-6 sm:min-h-[24rem]"
                  )}
                  value={petPersonaDraft}
                  onChange={(event) => onPetPersonaChange(event.target.value)}
                  placeholder={tr("context.petPersonaPlaceholder", "Describe the Web Pet as a low-noise coordination helper. Focus on routing style, allowed actions, and what it must not do.")}
                  spellCheck={false}
                  disabled={petPersonaBusy}
                />
              </div>

              <div className="flex flex-wrap items-center gap-2 pt-1">
                <button
                  type="button"
                  onClick={onSavePetPersona}
                  disabled={petPersonaBusy || !hasPetPersonaUnsaved}
                  className={classNames(ui.buttonPrimaryClass, "w-full sm:w-auto")}
                >
                  {petPersonaBusy ? tr("context.saving", "Saving…") : tr("context.savePetPersona", "Save pet persona")}
                </button>
                <button
                  type="button"
                  onClick={onDiscardPetPersona}
                  disabled={petPersonaBusy || !hasPetPersonaUnsaved}
                  className={classNames(ui.buttonSecondaryClass, "w-full sm:w-auto")}
                >
                  {tr("context.discardPetPersonaChanges", "Discard changes")}
                </button>
              </div>
            </div>
          ) : null}
        </div>
      </div>
    </section>
  );
}
