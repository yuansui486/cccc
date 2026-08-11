import { useEffect, useMemo, useRef, useState } from "react";
import {
  ActorProfile,
  OpenCodeDefaultVariant,
  RuntimeInfo,
  SupportedRuntime,
  RUNTIME_INFO,
} from "../../types";
import { useTranslation } from "react-i18next";
import { BASIC_MCP_CONFIG_SNIPPET } from "../../utils/mcpConfigSnippets";
import { useModalA11y } from "../../hooks/useModalA11y";
import { CapabilityPicker } from "../CapabilityPicker";
import { RolePresetPicker } from "../RolePresetPicker";
import { ActorAvatarField } from "../ActorAvatarField";
import { ClaudeReasoningEffortSelector, CodexReasoningEffortSelector, OpenCodeReasoningEffortSelector } from "../ReasoningEffortSelector";
import { formatCapabilityIdInput, parseCapabilityIdInput } from "../../utils/capabilityAutoload";
import { actorProfileIdentityKey } from "../../utils/actorProfiles";
import { supportsStandardWebHeadlessRuntime } from "../../utils/headlessRuntimeSupport";
import { buildRuntimeChoiceGroups } from "../../utils/runtimeChoiceGroups";
import { buildRuntimePriceMap, type RuntimePriceMap } from "../../utils/runtimePrices";
import {
  claudeReasoningEffortFromCommand,
  commandHasModelFlag,
  commandForRuntimePreset,
  codexReasoningEffortFromCommand,
  defaultRuntimePresetFor,
  mergePresetSecrets,
  mergeRuntimeAuthSecret,
  needsDedicatedOneColleagueKey,
  OPENCODE_FALLBACK_MODELS,
  opencodeDeepSeekModelFromCommand,
  runtimePresetById,
  runtimePresetIdFor,
  withClaudeReasoningEffort,
  withCodexReasoningEffort,
  type ClaudeReasoningEffort,
  type CodexReasoningEffort,
  type RuntimePresetId,
} from "../../utils/runtimePresets";
import { getCurrentDoneHubCodexApiKey, useDoneHubStore } from "../../stores/useDoneHubStore";
import { fetchDoneHubModels, fetchDoneHubPrices } from "../../services/doneHub";
import { Button } from "../ui/button";
import { Input } from "../ui/input";
import { Surface } from "../ui/surface";
import { Textarea } from "../ui/textarea";

export interface AddActorModalProps {
  isOpen: boolean;
  isDark: boolean;
  busy: string;
  hasForeman: boolean;
  developerMode: boolean;
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
  newActorRunner: "pty" | "headless";
  setNewActorRunner: (runner: "pty" | "headless") => void;

  newActorCommand: string;
  setNewActorCommand: (cmd: string) => void;

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
  createdActorId?: string;

  canAddActor: boolean;
  addActorDisabledReason: string;

  onAddActor: (avatarFile?: File | null, defaultVariant?: OpenCodeDefaultVariant) => Promise<boolean> | boolean;
  onEditCreatedActor?: () => void;
  onSaveAsProfile: (defaultVariant?: OpenCodeDefaultVariant) => void;
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

function modeSwitchButtonClass(): string {
  return [
    "rounded-2xl border px-4 py-2 text-sm font-semibold shadow-[0_10px_24px_-20px_rgba(37,99,235,0.72)] transition-all active:scale-[0.98] sm:text-base",
    "border-blue-200/80 bg-blue-50/80 text-blue-700",
    "hover:border-blue-300 hover:bg-blue-100/80 hover:text-blue-800",
    "dark:border-blue-400/25 dark:bg-blue-500/12 dark:text-blue-100 dark:hover:border-blue-300/35 dark:hover:bg-blue-500/18",
  ].join(" ");
}

function secretsPlaceholderForRuntime(runtime: SupportedRuntime): string {
  if (runtime === "claude") {
    return 'ANTHROPIC_AUTH_TOKEN="..."\nANTHROPIC_BASE_URL="..."';
  }
  if (runtime === "codex") {
    return 'ONECOLLEAGUE_API_KEY="..."';
  }
  if (runtime === "opencode") {
    return 'ONECOLLEAGUE_API_KEY="..."';
  }
  if (runtime === "gemini") {
    return 'GOOGLE_API_KEY="..."';
  }
  if (runtime === "hermes") {
    return "# Configure Hermes providers, OAuth, and tools in your Hermes profile.";
  }
  return 'ANTHROPIC_AUTH_TOKEN="..."\nANTHROPIC_BASE_URL="..."';
}

export function AddActorModal({
  isOpen,
  isDark,
  busy,
  hasForeman,
  developerMode,
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
  newActorRunner,
  setNewActorRunner,
  newActorCommand,
  setNewActorCommand,
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
  createdActorId,
  canAddActor,
  addActorDisabledReason,
  onAddActor,
  onEditCreatedActor,
  onSaveAsProfile,
  onClose,
  onCancelAndReset,
}: AddActorModalProps) {
  const { t } = useTranslation("actors");
  const [avatarFile, setAvatarFile] = useState<File | null>(null);
  const [selectedRuntimePresetId, setSelectedRuntimePresetId] = useState<RuntimePresetId | "">("");
  const [runtimePriceMap, setRuntimePriceMap] = useState<RuntimePriceMap | null>(null);
  const [opencodeModels, setOpencodeModels] = useState<string[]>([]);
  const [opencodeDefaultVariant, setOpencodeDefaultVariant] = useState<OpenCodeDefaultVariant>("high");
  const primedRuntimePresetRef = useRef("");
  const primedRuntimeAuthRef = useRef("");
  const primedCommandRef = useRef("");
  const doneHubCodexApiKey = useDoneHubStore((state) => String(state.session?.codex_api_key || "").trim());
  const runtimeChoiceGroups = useMemo(
    () => buildRuntimeChoiceGroups(runtimes, runtimePriceMap, opencodeModels),
    [runtimes, runtimePriceMap, opencodeModels],
  );
  const avatarPreviewUrl = useMemo(() => (avatarFile ? URL.createObjectURL(avatarFile) : null), [avatarFile]);

  useEffect(() => {
    return () => {
      if (avatarPreviewUrl) URL.revokeObjectURL(avatarPreviewUrl);
    };
  }, [avatarPreviewUrl]);

  useEffect(() => {
    if (!isOpen || runtimePriceMap) return;
    let cancelled = false;
    void Promise.all([fetchDoneHubPrices(), fetchDoneHubModels()]).then(([priceResp, modelResp]) => {
      if (cancelled) return;
      setRuntimePriceMap(priceResp.ok ? buildRuntimePriceMap(priceResp.result?.items || []) : {});
      const models = modelResp.ok
        ? (modelResp.result?.models || modelResp.result?.items?.map((item) => item.model) || []).filter(Boolean)
        : [];
      setOpencodeModels(models.length ? models : [...OPENCODE_FALLBACK_MODELS]);
    });
    return () => {
      cancelled = true;
    };
  }, [isOpen, runtimePriceMap]);

  const handleClose = () => {
    setAvatarFile(null);
    setSelectedRuntimePresetId("");
    onClose();
  };

  const { modalRef } = useModalA11y(isOpen, handleClose);

  const runtimeInfo = runtimes.find((r) => r.name === newActorRuntime);
  const runtimeAvailable = runtimeInfo?.available ?? false;
  const defaultCommand = runtimeInfo?.recommended_command || "";
  const derivedRuntimePresetId = runtimePresetIdFor(newActorRuntime, newActorCommand);
  const effectiveRuntimePresetId =
    selectedRuntimePresetId && runtimePresetById(selectedRuntimePresetId)?.runtime === newActorRuntime
      ? selectedRuntimePresetId
      : derivedRuntimePresetId;
  const selectedRuntimePreset = runtimePresetById(effectiveRuntimePresetId);
  const runtimeChoiceDescription = selectedRuntimePreset?.description || RUNTIME_INFO[newActorRuntime]?.desc || "";
  const newActorSecretsPlaceholder = secretsPlaceholderForRuntime(newActorRuntime);
  const needsOneColleagueKey = !newActorUseProfile
    && needsDedicatedOneColleagueKey(newActorRuntime, newActorSecretsSetText);
  const selectedProfile = actorProfiles.find((item) => actorProfileIdentityKey(item) === String(newActorProfileId || "").trim());
  const selectedProfileRuntime = String(selectedProfile?.runtime || "").trim() as SupportedRuntime;
  const selectedProfileCommand = commandPreview(selectedProfile?.command);
  const showRuntimeSetup = !newActorUseProfile && newActorRuntime === "custom";
  const showCommandEditor = !newActorUseProfile;
  const previewRuntime = newActorUseProfile ? selectedProfileRuntime || null : newActorRuntime;
  const previewTitle = String(newActorId || "").trim() || suggestedActorId;
  const selectedCodexReasoningEffort = codexReasoningEffortFromCommand(newActorCommand) || "medium";
  const selectedClaudeReasoningEffort = claudeReasoningEffortFromCommand(newActorCommand) || "high";
  const showOpenCodeReasoning = !newActorUseProfile && newActorRuntime === "opencode" && !!opencodeDeepSeekModelFromCommand(newActorCommand);

  useEffect(() => {
    if (!isOpen) {
      primedRuntimePresetRef.current = "";
      primedRuntimeAuthRef.current = "";
      primedCommandRef.current = "";
      return;
    }
    if (newActorUseProfile || !selectedRuntimePreset) return;
    const currentDoneHubCodexApiKey = doneHubCodexApiKey || getCurrentDoneHubCodexApiKey();
    const primeKey = `${selectedRuntimePreset.id}:${currentDoneHubCodexApiKey ? "auth" : "noauth"}`;
    if (primedRuntimePresetRef.current === primeKey) return;
    primedRuntimePresetRef.current = primeKey;
    const nextSecrets = mergePresetSecrets(newActorSecretsSetText, selectedRuntimePreset, currentDoneHubCodexApiKey);
    if (nextSecrets !== newActorSecretsSetText) {
      setNewActorSecretsSetText(nextSecrets);
    }
    if (
      selectedRuntimePreset.envPrivate ||
      (["codex", "opencode"].includes(selectedRuntimePreset.runtime) && currentDoneHubCodexApiKey)
    ) {
      setShowAdvancedActor(true);
    }
  }, [
    isOpen,
    newActorUseProfile,
    selectedRuntimePreset,
    doneHubCodexApiKey,
    newActorSecretsSetText,
    setNewActorSecretsSetText,
    setShowAdvancedActor,
  ]);

  useEffect(() => {
    if (!isOpen) {
      primedRuntimeAuthRef.current = "";
      return;
    }
    if (newActorUseProfile) return;
    const currentDoneHubCodexApiKey = doneHubCodexApiKey || getCurrentDoneHubCodexApiKey();
    const primeKey = `${newActorRuntime}:${currentDoneHubCodexApiKey ? "auth" : "noauth"}`;
    if (primedRuntimeAuthRef.current === primeKey) return;
    primedRuntimeAuthRef.current = primeKey;
    const nextSecrets = mergeRuntimeAuthSecret(newActorSecretsSetText, newActorRuntime, currentDoneHubCodexApiKey);
    if (nextSecrets !== newActorSecretsSetText) {
      setNewActorSecretsSetText(nextSecrets);
    }
    if (["codex", "opencode"].includes(newActorRuntime) && currentDoneHubCodexApiKey) {
      setShowAdvancedActor(true);
    }
  }, [
    isOpen,
    newActorUseProfile,
    newActorRuntime,
    doneHubCodexApiKey,
    newActorSecretsSetText,
    setNewActorSecretsSetText,
    setShowAdvancedActor,
  ]);

  useEffect(() => {
    if (!isOpen || newActorUseProfile || newActorCommand.trim()) return;
    const defaultPreset = defaultRuntimePresetFor(newActorRuntime);
    const commandToPrime = defaultPreset
      ? commandForRuntimePreset(defaultPreset, runtimeInfo).trim()
      : defaultCommand.trim();
    if (!commandToPrime) return;
    const primeKey = `${newActorRuntime}:${commandToPrime}`;
    if (primedCommandRef.current === primeKey) return;
    primedCommandRef.current = primeKey;
    // The modal defaults are derived from the selected runtime preset.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setNewActorCommand(commandToPrime);
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (defaultPreset) setSelectedRuntimePresetId(defaultPreset.id);
  }, [isOpen, newActorUseProfile, newActorRuntime, newActorCommand, runtimeInfo, defaultCommand, setNewActorCommand]);

  if (!isOpen) return null;

  const sectionCardClass = "rounded-2xl p-4 sm:p-5 glass-panel";
  const sectionTitleClass = "text-sm font-semibold text-[var(--color-text-primary)]";
  const sectionHintClass = "mt-1 text-xs text-[var(--color-text-muted)]";
  const collapsibleSummaryClass =
    "flex cursor-pointer list-none items-start justify-between gap-3 [&::-webkit-details-marker]:hidden";
  const collapsibleLabelClass = "text-xs font-medium text-[var(--color-text-secondary)]";
  const collapsibleChevronClass =
    "text-sm transition-transform group-open:rotate-180 text-[var(--color-text-tertiary)]";
  const nestedCardClass = "rounded-xl border p-3 border-[var(--glass-border-subtle)] bg-[var(--glass-bg)]";

  const handleSubmit = async () => {
    try {
      const ok = await Promise.resolve(onAddActor(avatarFile, showOpenCodeReasoning ? opencodeDefaultVariant : undefined));
      if (ok) {
        setAvatarFile(null);
        setSelectedRuntimePresetId("");
      }
    } catch (e) {
      setAddActorError(e instanceof Error ? e.message : t("failedToAddAgent"));
    }
  };

  const handleCancel = () => {
    setAvatarFile(null);
    setSelectedRuntimePresetId("");
    setOpencodeDefaultVariant("high");
    onCancelAndReset();
  };

  const updateCodexReasoningEffort = (effort: CodexReasoningEffort) => {
    const preset = selectedRuntimePreset || defaultRuntimePresetFor(newActorRuntime);
    const baseCommand =
      preset && !commandHasModelFlag(newActorCommand)
        ? commandForRuntimePreset(preset, runtimeInfo)
        : newActorCommand.trim() || defaultCommand.trim();
    setNewActorCommand(withCodexReasoningEffort(baseCommand, effort));
    if (preset) setSelectedRuntimePresetId(preset.id);
  };

  const updateClaudeReasoningEffort = (effort: ClaudeReasoningEffort) => {
    const preset = selectedRuntimePreset || defaultRuntimePresetFor(newActorRuntime);
    const baseCommand =
      preset && !commandHasModelFlag(newActorCommand)
        ? commandForRuntimePreset(preset, runtimeInfo)
        : newActorCommand.trim() || defaultCommand.trim();
    setNewActorCommand(withClaudeReasoningEffort(baseCommand, effort));
    if (preset) setSelectedRuntimePresetId(preset.id);
  };

  return (
    <div
      className="fixed inset-0 backdrop-blur-sm flex items-stretch sm:items-start justify-center p-0 sm:p-6 z-50 animate-fade-in glass-overlay"
      onPointerDown={(e) => {
        if (e.target === e.currentTarget) handleClose();
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

        <div className="flex-1 min-h-0 overflow-y-auto bg-[radial-gradient(circle_at_top,rgba(255,255,255,0.92),rgba(255,255,255,0)_30%),linear-gradient(180deg,rgb(251,250,247),rgb(245,244,241))] p-4 dark:bg-[radial-gradient(circle_at_top,rgba(255,255,255,0.05),rgba(255,255,255,0)_34%),linear-gradient(180deg,rgba(17,18,22,0.98),rgba(11,12,15,1))] sm:p-6 safe-area-bottom-compact">
          <div className="mx-auto max-w-2xl space-y-4">
            <Surface className={`${sectionCardClass} relative overflow-hidden`}>
              <span
                aria-hidden="true"
                className="pointer-events-none absolute left-0 top-0 h-0 w-0 border-r-[4.75rem] border-t-[4.75rem] border-r-transparent border-t-blue-600 opacity-95 dark:border-t-blue-500"
              />
              <div className="grid gap-5 lg:grid-cols-[10rem_minmax(0,1fr)] lg:items-center">
                <div className="flex min-h-[7rem] items-center justify-center">
                  <ActorAvatarField
                    label={null}
                    avatarUrl={undefined}
                    previewUrl={avatarPreviewUrl}
                    runtime={previewRuntime}
                    command={newActorUseProfile ? selectedProfile?.command : newActorCommand}
                    title={previewTitle}
                    isDark={isDark}
                    sizeClassName="h-[5.25rem] w-[5.25rem]"
                    disabled={busy === "actor-add"}
                    resetDisabled={!avatarFile}
                    reserveActionSpace={false}
                    onSelectFile={setAvatarFile}
                    onReset={() => setAvatarFile(null)}
                  />
                </div>

                <div className="space-y-4">
                  <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                    <div className="flex min-w-0 flex-1 flex-col gap-1.5 sm:flex-row sm:items-center sm:gap-2">
                      <label className="text-sm font-medium text-[var(--color-text-muted)] sm:w-16 sm:shrink-0">
                        {t("nickname", { defaultValue: "昵称" })}
                      </label>
                      <div className="min-w-0 flex-1">
                        <Input
                          value={newActorId}
                          onChange={(e) => setNewActorId(e.target.value)}
                          placeholder={`${t("leaveEmptyToUse")} ${suggestedActorId}`}
                        />
                      </div>
                    </div>

                    <div className="flex shrink-0 flex-col gap-3 sm:flex-row sm:items-center">
                      <button
                        type="button"
                        className={modeSwitchButtonClass()}
                        onClick={() => setNewActorUseProfile(!newActorUseProfile)}
                      >
                        {newActorUseProfile ? t("customAgent") : t("fromActorProfile")}
                      </button>
                    </div>
                  </div>

                  {newActorUseProfile ? (
                    <>
                      <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
                        <label className="text-sm font-medium text-[var(--color-text-muted)] sm:w-16 sm:shrink-0">{t("actorProfile")}</label>
                        <div className="min-w-0 flex-1">
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
                      </div>

                      {selectedProfile ? (
                        <Surface className="ml-0 px-3 py-3 text-[var(--color-text-secondary)] sm:ml-20" variant="subtle" radius="md" padding="none">
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
                        </Surface>
                      ) : null}
                    </>
                  ) : (
                    <>
                      <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
                        <label className="text-sm font-medium text-[var(--color-text-muted)] sm:w-16 sm:shrink-0">{t("aiRuntime")}</label>
                        <div className="min-w-0 flex-1">
                          <select
                            className="onecolleague-runtime-select w-full rounded-xl border px-4 py-2.5 text-sm min-h-[44px] transition-colors glass-input text-[var(--color-text-primary)]"
                            value={effectiveRuntimePresetId || newActorRuntime}
                            onChange={(e) => {
                              const raw = e.target.value;
                              const preset = runtimePresetById(raw);
                              const next = (preset?.runtime || raw) as SupportedRuntime;
                              const nextRuntimeInfo = runtimes.find((r) => r.name === next);
                              const presetCommand = preset ? commandForRuntimePreset(preset, nextRuntimeInfo) : "";
                              setNewActorRuntime(next);
                              if (!supportsStandardWebHeadlessRuntime(next)) setNewActorRunner("pty");
                              setNewActorCommand(presetCommand);
                              setSelectedRuntimePresetId(preset?.id || "");
                              if (preset) {
                                const doneHubCodexApiKey = getCurrentDoneHubCodexApiKey();
                                setNewActorSecretsSetText(
                                  mergePresetSecrets(newActorSecretsSetText, preset, doneHubCodexApiKey)
                                );
                                if (
                                  preset.envPrivate ||
                                  (["codex", "opencode"].includes(preset.runtime) && doneHubCodexApiKey)
                                ) {
                                  setShowAdvancedActor(true);
                                }
                              }
                            }}
                          >
                            {runtimeChoiceGroups.map((group) => (
                              <optgroup key={group.labelKey} label={t(group.labelKey, { defaultValue: group.labelFallback })}>
                                {group.options.map((option) => (
                                  <option key={option.id} value={option.id} disabled={option.disabled}>
                                    {option.label}
                                    {option.disabled ? ` ${t("notInstalled")}` : ""}
                                  </option>
                                ))}
                              </optgroup>
                            ))}
                          </select>
                          {runtimeChoiceDescription ? (
                            <div className="text-[10px] mt-1.5 text-[var(--color-text-muted)]">
                              {runtimeChoiceDescription}
                            </div>
                          ) : null}
                        </div>
                      </div>

                      {newActorRuntime === "codex" ? (
                        <CodexReasoningEffortSelector value={selectedCodexReasoningEffort} onChange={updateCodexReasoningEffort} labelPlacement="inline" />
                      ) : null}

                      {newActorRuntime === "claude" ? (
                        <ClaudeReasoningEffortSelector value={selectedClaudeReasoningEffort} onChange={updateClaudeReasoningEffort} labelPlacement="inline" />
                      ) : null}

                      {showOpenCodeReasoning ? (
                        <OpenCodeReasoningEffortSelector
                          value={opencodeDefaultVariant}
                          onChange={setOpencodeDefaultVariant}
                          labelPlacement="inline"
                        />
                      ) : null}
                    </>
                  )}
                </div>
              </div>

                  {developerMode && showCommandEditor ? (
                    <div>
                      <label className="block text-xs font-medium mb-2 text-[var(--color-text-muted)]">
                        {t("commandOverrideOptional")}
                      </label>
                      <Input
                        className="font-mono"
                        value={newActorCommand}
                        onChange={(e) => {
                          setNewActorCommand(e.target.value);
                          setSelectedRuntimePresetId("");
                        }}
                        placeholder={defaultCommand || t("enterCommand")}
                      />
                    </div>
                  ) : null}

                  {developerMode && defaultCommand.trim() ? (
                    <div className="text-[10px] text-[var(--color-text-muted)]">
                      {t("default")}{" "}
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
            </Surface>

            <Surface className={sectionCardClass}>
              <div className={sectionTitleClass}>{t("promptSettings")}</div>
              <div className={sectionHintClass}>{t("promptSettingsHint")}</div>

              <div className="mt-4 space-y-4">
                <RolePresetPicker
                  draftValue={newActorRoleNotes}
                  onChangeDraft={setNewActorRoleNotes}
                  disabled={busy === "actor-add"}
                />

                <div>
                  <label className="block text-xs font-medium mb-2 text-[var(--color-text-muted)]">{t("roleNotes")}</label>
                  <Textarea
                    className="min-h-[144px]"
                    value={newActorRoleNotes}
                    onChange={(e) => setNewActorRoleNotes(e.target.value)}
                    placeholder={t("roleNotesPlaceholder")}
                    spellCheck={false}
                  />
                  <div className="text-[10px] mt-1.5 text-[var(--color-text-muted)]">{t("newActorRoleNotesHint")}</div>
                </div>
              </div>
            </Surface>

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
                              <code className="px-1 rounded bg-amber-500/15">onecolleague</code> {t("thatRuns")}{" "}
                              <code className="px-1 rounded bg-amber-500/15">onecolleague mcp</code>.
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
                      <Textarea
                        className="min-h-[112px] font-mono"
                        value={newActorSecretsSetText}
                        onChange={(e) => setNewActorSecretsSetText(e.target.value)}
                        placeholder={newActorSecretsPlaceholder}
                        spellCheck={false}
                      />
                      <div className="text-[10px] mt-1.5 text-[var(--color-text-muted)]">
                        {t("secretsStoredLocally").replace(/<1>|<\/1>/g, "")}
                      </div>
                      <div className="text-[10px] mt-1 text-[var(--color-text-muted)]">
                        {t("secretsFormat").replace(/<1>|<\/1>|<2>|<\/2>/g, "")}
                      </div>
                      {needsOneColleagueKey ? (
                        <div className="mt-1.5 text-[10px] text-amber-700 dark:text-amber-300" role="alert">
                          {t("oneColleagueKeyRequired")}
                        </div>
                      ) : null}
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
                      <Button
                        type="button"
                        variant="secondary"
                        onClick={() => onSaveAsProfile(showOpenCodeReasoning ? opencodeDefaultVariant : undefined)}
                        disabled={busy === "actor-profile-save" || busy === "actor-add"}
                      >
                        {busy === "actor-profile-save" ? t("savingProfile") : t("addToActorProfiles")}
                      </Button>
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
              <Button
                type="button"
                variant="secondary"
                onClick={handleCancel}
              >
                {t("common:cancel")}
              </Button>

            {createdActorId && onEditCreatedActor ? (
              <Button
                type="button"
                variant="secondary"
                onClick={onEditCreatedActor}
                disabled={busy === "actor-add"}
              >
                {t("editCreatedActor")}
              </Button>
            ) : null}

            <div className="flex-1 min-w-0">
              <Button
                type="button"
                className="w-full font-semibold"
                onClick={() => {
                  void handleSubmit();
                }}
                disabled={!canAddActor}
              >
                {busy === "actor-add"
                  ? (createdActorId ? t("retryingStart") : t("adding"))
                  : createdActorId
                    ? t("retryStart")
                    : newActorUseProfile
                      ? t("createFromProfile")
                      : t("addAgent")}
              </Button>
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
