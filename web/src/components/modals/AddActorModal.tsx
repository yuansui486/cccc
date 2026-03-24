import {
  ActorProfile,
  RuntimeInfo,
  SupportedRuntime,
  SUPPORTED_RUNTIMES,
  RUNTIME_INFO,
} from "../../types";
import { useTranslation } from "react-i18next";
import { BASIC_MCP_CONFIG_SNIPPET } from "../../utils/mcpConfigSnippets";
import { classNames } from "../../utils/classNames";
import { useModalA11y } from "../../hooks/useModalA11y";
import { CapabilityPicker } from "../CapabilityPicker";
import { RolePresetPicker } from "../RolePresetPicker";
import { formatCapabilityIdInput, parseCapabilityIdInput } from "../../utils/capabilityAutoload";
import { actorProfileIdentityKey } from "../../utils/actorProfiles";

export interface AddActorModalProps {
  isOpen: boolean;
  isDark: boolean;
  busy: string;
  hasForeman: boolean;
  runtimes: RuntimeInfo[];

  suggestedActorId: string;
  newActorId: string;
  setNewActorId: (id: string) => void;

  newActorRole: "peer" | "foreman";
  setNewActorRole: (role: "peer" | "foreman") => void;

  newActorUseProfile: boolean;
  setNewActorUseProfile: (v: boolean) => void;
  newActorProfileId: string;
  setNewActorProfileId: (id: string) => void;
  actorProfiles: ActorProfile[];
  actorProfilesBusy: boolean;

  newActorRuntime: SupportedRuntime;
  setNewActorRuntime: (runtime: SupportedRuntime) => void;

  newActorCommand: string;
  setNewActorCommand: (cmd: string) => void;
  newActorUseDefaultCommand: boolean;
  setNewActorUseDefaultCommand: (v: boolean) => void;

  newActorSecretsSetText: string;
  setNewActorSecretsSetText: (v: string) => void;
  newActorCapabilityAutoloadText: string;
  setNewActorCapabilityAutoloadText: (v: string) => void;
  newActorRoleNotes: string;
  setNewActorRoleNotes: (v: string) => void;

  showAdvancedActor: boolean;
  setShowAdvancedActor: (show: boolean) => void;

  addActorError: string;
  setAddActorError: (msg: string) => void;

  canAddActor: boolean;
  addActorDisabledReason: string;

  onAddActor: () => void;
  onSaveAsProfile: () => void;
  onClose: () => void;
  onCancelAndReset: () => void;
}

function commandPreview(command: string[] | undefined): string {
  const cmd = Array.isArray(command) ? command.filter((item) => typeof item === "string" && item.trim()) : [];
  return cmd.join(" ");
}

function profileScopeLabel(profile: ActorProfile, t: (key: string, options?: Record<string, unknown>) => string): string {
  if (String(profile.scope || "global").trim() === "user") {
    return t("profileScopeOwnedBy", { owner: String(profile.owner_id || "").trim() || "?" });
  }
  return t("profileScopeGlobal");
}

function modeButtonClass(selected: boolean): string {
  return [
    "px-3 py-2.5 rounded-xl border text-sm min-h-[44px] font-medium transition-colors",
    selected
      ? "bg-blue-600 text-white border-blue-600"
      : "border-[var(--glass-border-subtle)] bg-[var(--glass-panel-bg)] text-[var(--color-text-secondary)] hover:bg-[var(--glass-tab-bg-hover)]",
  ].join(" ");
}

export function AddActorModal({
  isOpen,
  isDark,
  busy,
  hasForeman,
  runtimes,
  suggestedActorId,
  newActorId,
  setNewActorId,
  newActorRole,
  setNewActorRole,
  newActorUseProfile,
  setNewActorUseProfile,
  newActorProfileId,
  setNewActorProfileId,
  actorProfiles,
  actorProfilesBusy,
  newActorRuntime,
  setNewActorRuntime,
  newActorCommand,
  setNewActorCommand,
  newActorUseDefaultCommand,
  setNewActorUseDefaultCommand,
  newActorSecretsSetText,
  setNewActorSecretsSetText,
  newActorCapabilityAutoloadText,
  setNewActorCapabilityAutoloadText,
  newActorRoleNotes,
  setNewActorRoleNotes,
  showAdvancedActor,
  setShowAdvancedActor,
  addActorError,
  setAddActorError,
  canAddActor,
  addActorDisabledReason,
  onAddActor,
  onSaveAsProfile,
  onClose,
  onCancelAndReset,
}: AddActorModalProps) {
  const { t } = useTranslation("actors");
  const { modalRef } = useModalA11y(isOpen, onClose);
  if (!isOpen) return null;

  const runtimeInfo = runtimes.find((r) => r.name === newActorRuntime);
  const runtimeAvailable = runtimeInfo?.available ?? false;
  const defaultCommand = runtimeInfo?.recommended_command || "";
  const selectedProfile = actorProfiles.find((item) => actorProfileIdentityKey(item) === String(newActorProfileId || "").trim());
  const selectedProfileRuntime = String(selectedProfile?.runtime || "").trim() as SupportedRuntime;
  const selectedProfileCommand = commandPreview(selectedProfile?.command);
  const showRuntimeSetup = !newActorUseProfile && newActorRuntime === "custom";
  const showCommandEditor = !newActorUseProfile && (newActorRuntime === "custom" || !newActorUseDefaultCommand);

  const sectionCardClass = "rounded-2xl p-4 sm:p-5 glass-panel";
  const sectionTitleClass = "text-sm font-semibold text-[var(--color-text-primary)]";
  const sectionHintClass = "mt-1 text-xs text-[var(--color-text-muted)]";
  const collapsibleSummaryClass =
    "flex cursor-pointer list-none items-start justify-between gap-3 [&::-webkit-details-marker]:hidden";
  const collapsibleLabelClass = "text-xs font-medium text-[var(--color-text-secondary)]";
  const collapsibleChevronClass =
    "text-sm transition-transform group-open:rotate-180 text-[var(--color-text-tertiary)]";
  const nestedCardClass = "rounded-xl border p-3 border-[var(--glass-border-subtle)] bg-[var(--glass-bg)]";

  return (
    <div
      className="fixed inset-0 backdrop-blur-sm flex items-stretch sm:items-start justify-center p-0 sm:p-6 z-50 animate-fade-in glass-overlay"
      onPointerDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
      role="dialog"
      aria-modal="true"
      aria-labelledby="add-actor-title"
    >
      <div
        ref={modalRef}
        className="w-full h-full sm:h-auto sm:max-w-2xl sm:mt-10 sm:max-h-[calc(100vh-5rem)] border border-[var(--glass-border-subtle)] shadow-2xl animate-scale-in rounded-none sm:rounded-2xl glass-modal flex flex-col overflow-hidden text-[var(--color-text-primary)]"
      >
        <div className="px-6 py-4 border-b safe-area-inset-top border-[var(--glass-border-subtle)] glass-header flex-shrink-0">
          <div id="add-actor-title" className="text-lg font-semibold text-[var(--color-text-primary)]">
            {t("addAiAgent")}
          </div>
          <div className="text-sm mt-1 text-[var(--color-text-muted)]">{t("addActorSubtitle")}</div>
        </div>

        <div className="flex-1 min-h-0 overflow-y-auto p-4 sm:p-6 safe-area-bottom-compact">
          <div className="mx-auto max-w-2xl space-y-4">
            <section className={sectionCardClass}>
              <div className={sectionTitleClass}>{t("sectionBasics")}</div>
              <div className={sectionHintClass}>{t("addSectionBasicsHint")}</div>

              <div className="mt-4 space-y-4">
                <div>
                  <label className="block text-xs font-medium mb-2 text-[var(--color-text-muted)]">
                    {t("agentName")} <span className="text-[var(--color-text-muted)]">{t("unicodeSupport")}</span>
                  </label>
                  <input
                    className="w-full rounded-xl border px-4 py-2.5 text-sm min-h-[44px] transition-colors glass-input text-[var(--color-text-primary)] placeholder-[var(--color-text-muted)]"
                    value={newActorId}
                    onChange={(e) => setNewActorId(e.target.value)}
                    placeholder={suggestedActorId}
                  />
                  <div className="text-[10px] mt-1.5 text-[var(--color-text-muted)]">
                    {t("leaveEmptyToUse")}{" "}
                    <code className="px-1 rounded bg-[var(--glass-tab-bg)] text-[var(--color-text-secondary)]">
                      {suggestedActorId}
                    </code>
                  </div>
                </div>

                <div>
                  <label className="block text-xs font-medium mb-2 text-[var(--color-text-muted)]">{t("role")}</label>
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                    <button
                      type="button"
                      className={classNames(
                        "px-4 py-2.5 rounded-xl border text-sm font-medium transition-all min-h-[44px]",
                        newActorRole === "foreman"
                          ? "bg-amber-500/20 border-amber-500 text-amber-700 dark:text-amber-300"
                          : hasForeman
                            ? "border-[var(--glass-border-subtle)] bg-[var(--glass-panel-bg)] text-[var(--color-text-muted)] cursor-not-allowed"
                            : "border-[var(--glass-border-subtle)] bg-[var(--glass-panel-bg)] text-[var(--color-text-secondary)] hover:bg-[var(--glass-tab-bg-hover)]"
                      )}
                      onClick={() => {
                        if (!hasForeman) setNewActorRole("foreman");
                      }}
                      disabled={hasForeman}
                    >
                      {t("foremanRole")} {hasForeman && t("foremanExists")}
                    </button>
                    <button
                      type="button"
                      className={classNames(
                        "px-4 py-2.5 rounded-xl border text-sm font-medium transition-all min-h-[44px]",
                        newActorRole === "peer"
                          ? "bg-blue-500/20 border-blue-500 text-blue-700 dark:text-blue-300"
                          : !hasForeman
                            ? "border-[var(--glass-border-subtle)] bg-[var(--glass-panel-bg)] text-[var(--color-text-muted)] cursor-not-allowed"
                            : "border-[var(--glass-border-subtle)] bg-[var(--glass-panel-bg)] text-[var(--color-text-secondary)] hover:bg-[var(--glass-tab-bg-hover)]"
                      )}
                      onClick={() => {
                        if (hasForeman) setNewActorRole("peer");
                      }}
                      disabled={!hasForeman}
                    >
                      {t("peerRole")} {!hasForeman && t("needForemanFirst")}
                    </button>
                  </div>
                  <div className="text-[10px] mt-1.5 text-[var(--color-text-muted)]">
                    {hasForeman ? t("foremanLeads") : t("firstAgentForeman")}
                  </div>
                </div>

                <RolePresetPicker
                  draftValue={newActorRoleNotes}
                  onChangeDraft={setNewActorRoleNotes}
                  disabled={busy === "actor-add"}
                />

                <div>
                  <label className="block text-xs font-medium mb-2 text-[var(--color-text-muted)]">{t("roleNotes")}</label>
                  <textarea
                    className="w-full rounded-xl border px-3 py-2 text-sm min-h-[144px] transition-colors glass-input text-[var(--color-text-primary)] placeholder-[var(--color-text-muted)]"
                    value={newActorRoleNotes}
                    onChange={(e) => setNewActorRoleNotes(e.target.value)}
                    placeholder={t("roleNotesPlaceholder")}
                    spellCheck={false}
                  />
                  <div className="text-[10px] mt-1.5 text-[var(--color-text-muted)]">{t("newActorRoleNotesHint")}</div>
                </div>
              </div>
            </section>

            <section className={sectionCardClass}>
              <div className={sectionTitleClass}>{t("sectionRuntime")}</div>
              <div className={sectionHintClass}>{t("sectionRuntimeHint")}</div>

              <div className="mt-4">
                <label className="block text-xs font-medium mb-2 text-[var(--color-text-muted)]">{t("creationMode")}</label>
                <div className="grid grid-cols-2 gap-2">
                  <button type="button" className={modeButtonClass(!newActorUseProfile)} onClick={() => setNewActorUseProfile(false)}>
                    {t("customAgent")}
                  </button>
                  <button type="button" className={modeButtonClass(newActorUseProfile)} onClick={() => setNewActorUseProfile(true)}>
                    {t("fromActorProfile")}
                  </button>
                </div>
              </div>

              <div className="mt-4 space-y-4">
                {newActorUseProfile ? (
                  <>
                    <div>
                      <label className="block text-xs font-medium mb-2 text-[var(--color-text-muted)]">{t("actorProfile")}</label>
                      <select
                        className="w-full rounded-xl border px-4 py-2.5 text-sm min-h-[44px] transition-colors glass-input text-[var(--color-text-primary)]"
                        value={newActorProfileId}
                        onChange={(e) => setNewActorProfileId(e.target.value)}
                        disabled={actorProfilesBusy}
                      >
                        <option value="">{actorProfilesBusy ? t("loadingProfiles") : t("selectActorProfile")}</option>
                        {actorProfiles.map((profile) => (
                          <option key={actorProfileIdentityKey(profile)} value={actorProfileIdentityKey(profile)}>
                            {(profile.name || profile.id) + " · " + profileScopeLabel(profile, t)}
                          </option>
                        ))}
                      </select>
                    </div>

                    {selectedProfile ? (
                      <div className="rounded-xl border px-3 py-3 border-[var(--glass-border-subtle)] bg-[var(--glass-bg)] text-[var(--color-text-secondary)]">
                        <div className="text-sm font-medium text-[var(--color-text-primary)]">
                          {selectedProfile.name || selectedProfile.id}
                        </div>
                        <div className="mt-1 text-xs">
                          {profileScopeLabel(selectedProfile, t)}
                        </div>
                        <div className="mt-1 text-xs">
                          {RUNTIME_INFO[selectedProfileRuntime]?.label || selectedProfile.runtime}
                        </div>
                        {selectedProfileCommand ? (
                          <div className="mt-2 font-mono text-[11px] break-all text-[var(--color-text-tertiary)]">
                            {selectedProfileCommand}
                          </div>
                        ) : null}
                      </div>
                    ) : null}
                  </>
                ) : (
                  <>
                    <div>
                      <label className="block text-xs font-medium mb-2 text-[var(--color-text-muted)]">{t("aiRuntime")}</label>
                      <select
                        className="w-full rounded-xl border px-4 py-2.5 text-sm min-h-[44px] transition-colors glass-input text-[var(--color-text-primary)]"
                        value={newActorRuntime}
                        onChange={(e) => {
                          const next = e.target.value as SupportedRuntime;
                          setNewActorRuntime(next);
                          setNewActorCommand("");
                          setNewActorUseDefaultCommand(next !== "custom");
                        }}
                      >
                        {SUPPORTED_RUNTIMES.map((rt) => {
                          const info = RUNTIME_INFO[rt];
                          const rtInfo = runtimes.find((r) => r.name === rt);
                          const available = rtInfo?.available ?? false;
                          const selectable = available || rt === "custom";
                          return (
                            <option key={rt} value={rt} disabled={!selectable}>
                              {info?.label || rt}
                              {!available && rt !== "custom" ? ` ${t("notInstalled")}` : ""}
                            </option>
                          );
                        })}
                      </select>
                      {RUNTIME_INFO[newActorRuntime]?.desc ? (
                        <div className="text-[10px] mt-1.5 text-[var(--color-text-muted)]">
                          {RUNTIME_INFO[newActorRuntime].desc}
                        </div>
                      ) : null}
                    </div>

                    {newActorRuntime !== "custom" ? (
                      <label className="flex items-center gap-2 text-xs text-[var(--color-text-secondary)]">
                        <input
                          type="checkbox"
                          checked={newActorUseDefaultCommand}
                          onChange={(e) => {
                            const checked = e.target.checked;
                            setNewActorUseDefaultCommand(checked);
                            if (checked) setNewActorCommand("");
                          }}
                        />
                        {t("useRuntimeDefaultCommand")}
                      </label>
                    ) : null}

                    {showCommandEditor ? (
                      <div>
                        <label className="block text-xs font-medium mb-2 text-[var(--color-text-muted)]">
                          {t("commandOverrideOptional")}
                        </label>
                        <input
                          className="w-full rounded-xl border px-4 py-2.5 text-sm font-mono min-h-[44px] transition-colors glass-input text-[var(--color-text-primary)] placeholder-[var(--color-text-muted)]"
                          value={newActorCommand}
                          onChange={(e) => setNewActorCommand(e.target.value)}
                          placeholder={defaultCommand || t("enterCommand")}
                        />
                      </div>
                    ) : null}

                    {defaultCommand.trim() ? (
                      <div className="text-[10px] text-[var(--color-text-muted)]">
                        {newActorUseDefaultCommand && newActorRuntime !== "custom"
                          ? t("usingRuntimeDefaultCommand")
                          : t("default")}{" "}
                        <code className="px-1 rounded bg-[var(--glass-tab-bg)] text-[var(--color-text-secondary)]">
                          {defaultCommand}
                        </code>
                      </div>
                    ) : null}

                    {newActorRuntime === "custom" || !runtimeAvailable ? (
                      <div className="rounded-xl border px-3 py-2 text-[11px] border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-300">
                        <div className="font-medium">{t("manualMcpRequired")}</div>
                        <div className="mt-1">{t("customCommandHint").replace(/<1>|<\/1>/g, "")}</div>
                      </div>
                    ) : null}
                  </>
                )}
              </div>
            </section>

            {!newActorUseProfile ? (
              <details
                className={`group ${sectionCardClass}`}
                open={showAdvancedActor}
                onToggle={(e) => setShowAdvancedActor((e.currentTarget as HTMLDetailsElement).open)}
              >
                <summary className={collapsibleSummaryClass}>
                  <div>
                    <div className={sectionTitleClass}>{t("sectionAdvanced")}</div>
                    <div className={sectionHintClass}>{t("sectionAdvancedHint")}</div>
                  </div>
                  <span aria-hidden="true" className={collapsibleChevronClass}>
                    ⌄
                  </span>
                </summary>

                <div className="mt-4 space-y-4 border-t border-[var(--glass-border-subtle)] pt-4">
                  {showRuntimeSetup ? (
                    <details className={`group ${nestedCardClass}`}>
                      <summary className={collapsibleSummaryClass}>
                        <div>
                          <div className={collapsibleLabelClass}>{t("runtimeSetupSection")}</div>
                          <div className={sectionHintClass}>{t("runtimeSetupSectionHint")}</div>
                        </div>
                        <span aria-hidden="true" className={collapsibleChevronClass}>
                          ⌄
                        </span>
                      </summary>

                      <div className="mt-4 rounded-xl border px-3 py-2 text-[11px] border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-300">
                        <div className="font-medium">{t("manualMcpRequired")}</div>
                        {newActorRuntime === "custom" ? (
                          <>
                            <div className="mt-1">{t("customCommandHint").replace(/<1>|<\/1>/g, "")}</div>
                            <div className="mt-1">
                              {t("configureMcpStdio")}{" "}
                              <code className="px-1 rounded bg-amber-500/15">cccc</code> {t("thatRuns")}{" "}
                              <code className="px-1 rounded bg-amber-500/15">cccc mcp</code>.
                            </div>
                          </>
                        ) : null}

                        {newActorRuntime === "custom" ? (
                          <pre className="mt-1.5 p-2 rounded overflow-x-auto whitespace-pre bg-amber-500/10 text-amber-800 dark:text-amber-200">
                            <code>{BASIC_MCP_CONFIG_SNIPPET}</code>
                          </pre>
                        ) : null}

                        <div className="mt-1 text-[10px] text-amber-700/80 dark:text-amber-300/80">
                          {t("restartAfterConfig")}
                        </div>
                      </div>
                    </details>
                  ) : null}

                  <details className={`group ${nestedCardClass}`}>
                    <summary className={collapsibleSummaryClass}>
                      <div>
                        <div className={collapsibleLabelClass}>{t("capabilitiesSection")}</div>
                        <div className={sectionHintClass}>{t("capabilitiesSectionHint")}</div>
                      </div>
                      <span aria-hidden="true" className={collapsibleChevronClass}>
                        ⌄
                      </span>
                    </summary>

                    <div className="mt-4">
                      <CapabilityPicker
                        isDark={isDark}
                        value={parseCapabilityIdInput(newActorCapabilityAutoloadText)}
                        onChange={(next) => setNewActorCapabilityAutoloadText(formatCapabilityIdInput(next))}
                        disabled={busy === "actor-add"}
                        label={t("autoloadCapabilities")}
                        hint={t("autoloadCapabilitiesHint")}
                      />
                    </div>
                  </details>

                  <details className={`group ${nestedCardClass}`}>
                    <summary className={collapsibleSummaryClass}>
                      <div>
                        <div className={collapsibleLabelClass}>{t("secretsSection")}</div>
                        <div className={sectionHintClass}>{t("secretsSectionHint")}</div>
                      </div>
                      <span aria-hidden="true" className={collapsibleChevronClass}>
                        ⌄
                      </span>
                    </summary>

                    <div className="mt-4">
                      <label className="block text-xs font-medium mb-2 text-[var(--color-text-muted)]">
                        {t("secretsWriteOnly")}
                      </label>
                      <textarea
                        className="w-full rounded-xl border px-3 py-2 text-sm font-mono min-h-[112px] transition-colors glass-input text-[var(--color-text-primary)] placeholder-[var(--color-text-muted)]"
                        value={newActorSecretsSetText}
                        onChange={(e) => setNewActorSecretsSetText(e.target.value)}
                        placeholder={'export OPENAI_API_KEY="...";\nexport ANTHROPIC_API_KEY="...";'}
                        spellCheck={false}
                      />
                      <div className="text-[10px] mt-1.5 text-[var(--color-text-muted)]">
                        {t("secretsStoredLocally").replace(/<1>|<\/1>/g, "")}
                      </div>
                      <div className="text-[10px] mt-1 text-[var(--color-text-muted)]">
                        {t("secretsFormat").replace(/<1>|<\/1>|<2>|<\/2>/g, "")}
                      </div>
                    </div>
                  </details>

                  <details className={`group ${nestedCardClass}`}>
                    <summary className={collapsibleSummaryClass}>
                      <div>
                        <div className={collapsibleLabelClass}>{t("profileToolsSection")}</div>
                        <div className={sectionHintClass}>{t("profileToolsSectionHint")}</div>
                      </div>
                      <span aria-hidden="true" className={collapsibleChevronClass}>
                        ⌄
                      </span>
                    </summary>

                    <div className="mt-4 flex flex-wrap gap-3">
                      <button
                        type="button"
                        className="px-4 py-2.5 rounded-xl text-sm font-medium transition-colors min-h-[44px] border border-[var(--glass-border-subtle)] bg-[var(--glass-panel-bg)] text-[var(--color-text-secondary)] hover:bg-[var(--glass-tab-bg-hover)]"
                        onClick={onSaveAsProfile}
                        disabled={busy === "actor-profile-save" || busy === "actor-add"}
                      >
                        {busy === "actor-profile-save" ? t("savingProfile") : t("addToActorProfiles")}
                      </button>
                    </div>
                  </details>
                </div>
              </details>
            ) : null}
          </div>
        </div>

        <div className="border-t px-4 py-3 sm:px-6 sm:py-4 safe-area-inset-bottom border-[var(--glass-border-subtle)] glass-header flex-shrink-0">
          {addActorError ? (
            <div
              className="mb-3 rounded-xl border px-3 py-2 text-xs border-rose-500/30 bg-rose-500/10 text-rose-700 dark:text-rose-300"
              role="alert"
            >
              <div className="flex items-start justify-between gap-3">
                <span>{addActorError}</span>
                <button
                  type="button"
                  className="text-rose-700 dark:text-rose-300 hover:opacity-80"
                  onClick={() => setAddActorError("")}
                  aria-label={t("common:close")}
                >
                  ×
                </button>
              </div>
            </div>
          ) : null}

          <div className="flex flex-col-reverse sm:flex-row gap-3">
            <button
              type="button"
              className="px-4 py-2.5 rounded-xl text-sm font-medium transition-colors min-h-[44px] border border-[var(--glass-border-subtle)] bg-[var(--glass-panel-bg)] text-[var(--color-text-secondary)] hover:bg-[var(--glass-tab-bg-hover)]"
              onClick={onCancelAndReset}
            >
              {t("common:cancel")}
            </button>

            <div className="flex-1 min-w-0">
              <button
                type="button"
                className="w-full rounded-xl bg-blue-600 hover:bg-blue-500 text-white px-4 py-2.5 text-sm font-semibold shadow-lg disabled:opacity-50 disabled:cursor-not-allowed transition-all min-h-[44px]"
                onClick={onAddActor}
                disabled={!canAddActor}
              >
                {busy === "actor-add" ? t("adding") : newActorUseProfile ? t("createFromProfile") : t("addAgent")}
              </button>
              {addActorDisabledReason ? (
                <div className="text-[10px] text-amber-600 dark:text-amber-300 mt-1.5">{addActorDisabledReason}</div>
              ) : null}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
