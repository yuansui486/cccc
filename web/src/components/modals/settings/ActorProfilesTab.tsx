import { useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import { ActorProfile, ActorProfileUsage, RUNTIME_INFO, SUPPORTED_RUNTIMES } from "../../../types";
import * as api from "../../../services/api";
import { parsePrivateEnvSetText, parsePrivateEnvUnsetText } from "../../../utils/privateEnvInput";
import { formatCapabilityIdInput, parseCapabilityIdInput } from "../../../utils/capabilityAutoload";
import { normalizeActorRunner, supportsStandardWebHeadlessRuntime } from "../../../utils/headlessRuntimeSupport";
import { useGroupStore } from "../../../stores";
import {
  cardClass,
  inputClass,
  labelClass,
  primaryButtonClass,
  secondaryButtonClass,
  settingsDialogBodyClass,
  settingsDialogFooterClass,
  settingsDialogHeaderClass,
} from "./types";
import { CapabilityPicker } from "../../CapabilityPicker";

interface ActorProfilesTabProps {
  isDark: boolean;
  isActive: boolean;
  scope: "global" | "my";
}

type EditorState = {
  id: string;
  revision: number;
  name: string;
  runtime: string;
  runner: "pty" | "headless";
  command: string;
  useDefaultCommand: boolean;
  submit: "enter" | "newline" | "none";
  capabilityAutoloadText: string;
  capabilityDefaultScope: "actor" | "session";
  capabilitySessionTtlSeconds: number;
};

function formatCommand(cmd: string[] | undefined): string {
  const parts = Array.isArray(cmd) ? cmd.filter((item) => typeof item === "string" && item.trim()) : [];
  return parts.join(" ");
}

const RUNTIME_DEFAULT_COMMANDS: Record<string, string> = {
  amp: "amp",
  auggie: "auggie",
  claude: "claude --dangerously-skip-permissions",
  codex: "codex -c shell_environment_policy.inherit=all --dangerously-bypass-approvals-and-sandbox --search",
  droid: "droid --auto high",
  gemini: "gemini --yolo",
  kimi: "kimi --yolo",
  neovate: "neovate",
  custom: "",
};

function defaultCommandForRuntime(runtime: string): string {
  const key = String(runtime || "").trim();
  return String(RUNTIME_DEFAULT_COMMANDS[key] || key || "").trim();
}

function supportsRuntimeDefaultCommand(runtime: string): boolean {
  return String(runtime || "").trim() !== "custom";
}

function modeButtonClass(selected: boolean): string {
  return [
    "px-3 py-2.5 rounded-xl border text-sm min-h-[44px] font-medium transition-colors",
    selected
      ? "bg-blue-600 text-white border-blue-600"
      : "border-[var(--glass-border-subtle)] bg-[var(--glass-panel-bg)] text-[var(--color-text-secondary)] hover:bg-[var(--glass-tab-bg-hover)]",
  ].join(" ");
}

function buildEditor(profile?: ActorProfile | null): EditorState {
  const runtime = String(profile?.runtime || "codex");
  const runner = supportsStandardWebHeadlessRuntime(runtime) ? normalizeActorRunner(profile?.runner) : "pty";
  const command = formatCommand(profile?.command);
  const defaultCommand = defaultCommandForRuntime(runtime);
  const useDefaultCommand =
    supportsRuntimeDefaultCommand(runtime) &&
    (!command.trim() || command.trim() === defaultCommand);
  return {
    id: String(profile?.id || ""),
    revision: Number(profile?.revision || 0),
    name: String(profile?.name || ""),
    runtime,
    runner,
    command,
    useDefaultCommand,
    submit: (String(profile?.submit || "enter") as "enter" | "newline" | "none"),
    capabilityAutoloadText: formatCapabilityIdInput(profile?.capability_defaults?.autoload_capabilities),
    capabilityDefaultScope:
      String(profile?.capability_defaults?.default_scope || "actor").trim().toLowerCase() === "session"
        ? "session"
        : "actor",
    capabilitySessionTtlSeconds: Math.max(
      60,
      Number(profile?.capability_defaults?.session_ttl_seconds || 3600)
    ),
  };
}

export function ActorProfilesTab({ isDark, isActive, scope }: ActorProfilesTabProps) {
  const { t } = useTranslation("settings");
  const groups = useGroupStore((s) => s.groups);
  const refreshGroups = useGroupStore((s) => s.refreshGroups);
  const refreshActors = useGroupStore((s) => s.refreshActors);
  const [profiles, setProfiles] = useState<ActorProfile[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [query, setQuery] = useState("");
  const [usageBusyProfileId, setUsageBusyProfileId] = useState("");

  const [editorOpen, setEditorOpen] = useState(false);
  const [editorBusy, setEditorBusy] = useState(false);
  const [editorErr, setEditorErr] = useState("");
  const [editor, setEditor] = useState<EditorState>(buildEditor());
  const [secretKeys, setSecretKeys] = useState<string[]>([]);
  const [secretMasks, setSecretMasks] = useState<Record<string, string>>({});
  const [secretSetText, setSecretSetText] = useState("");
  const [secretUnsetText, setSecretUnsetText] = useState("");
  const [secretClear, setSecretClear] = useState(false);
  const [duplicateSourceProfileId, setDuplicateSourceProfileId] = useState("");
  const [sessionUserId, setSessionUserId] = useState("");

  const isMyScope = scope === "my";
  const profileScope: api.ProfileScope = isMyScope ? "user" : "global";
  const profileLookup = useMemo(
    () => ({
      scope: profileScope,
      ownerId: isMyScope ? sessionUserId.trim() : "",
    }),
    [isMyScope, profileScope, sessionUserId]
  );

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return profiles;
    return profiles.filter((item) => {
      const name = String(item.name || "").toLowerCase();
      const id = String(item.id || "").toLowerCase();
      const rt = String(item.runtime || "").toLowerCase();
      return name.includes(q) || id.includes(q) || rt.includes(q);
    });
  }, [profiles, query]);

  const duplicateSourceLabel = useMemo(() => {
    const sourceId = duplicateSourceProfileId.trim();
    if (!sourceId) return "";
    const profile = profiles.find((item) => String(item.id || "").trim() === sourceId);
    if (!profile) return sourceId;
    return String(profile.name || profile.id || sourceId).trim() || sourceId;
  }, [duplicateSourceProfileId, profiles]);

  const editorSupportsDefaultCommand = useMemo(
    () => supportsRuntimeDefaultCommand(editor.runtime),
    [editor.runtime]
  );
  const editorSupportsHeadlessRunner = useMemo(
    () => supportsStandardWebHeadlessRuntime(editor.runtime),
    [editor.runtime]
  );
  const editorDefaultCommand = useMemo(
    () => defaultCommandForRuntime(editor.runtime),
    [editor.runtime]
  );

  const ensureSessionContext = async () => {
    try {
      const resp = await api.fetchWebAccessSession();
      if (!resp.ok) {
        setSessionUserId("");
        return null;
      }
      const session = resp.result?.web_access_session ?? null;
      const userId = String(session?.user_id || "").trim();
      const signedIn = Boolean(session?.current_browser_signed_in);
      setSessionUserId(userId);
      return { userId, signedIn };
    } catch {
      setSessionUserId("");
      return null;
    }
  };

  const scopeRequestError = (code: string | undefined, fallback: string) => {
    const normalized = String(code || "").trim();
    if (isMyScope && (normalized === "unauthorized" || normalized === "permission_denied")) {
      return t("actorProfiles.myProfilesLoginRequired");
    }
    return fallback;
  };

  const closeEditor = () => {
    setDuplicateSourceProfileId("");
    setEditorOpen(false);
  };

  const editorModal = editorOpen ? (
    <div
      className="fixed inset-0 z-[1000] flex items-stretch justify-center p-3 sm:items-center"
      role="dialog"
      aria-modal="true"
      onPointerDown={(e) => {
        if (e.target === e.currentTarget) {
          closeEditor();
        }
      }}
    >
      <div className="absolute inset-0 glass-overlay" />
      <div className="glass-modal relative flex h-full w-full max-w-xl flex-col overflow-hidden rounded-2xl border border-[var(--glass-border-subtle)] shadow-2xl sm:h-auto sm:max-h-[calc(100dvh-2rem)]">
        <div className={settingsDialogHeaderClass}>
          <div className="text-base font-semibold text-[var(--color-text-primary)]">
            {editor.id ? t("actorProfiles.editTitle") : t("actorProfiles.newTitle")}
          </div>
          <button
            type="button"
            onClick={closeEditor}
            className={`${secondaryButtonClass("sm")} ml-auto`}
          >
            {t("common:close")}
          </button>
        </div>
        <div className={`${settingsDialogBodyClass} space-y-4`}>
          {editorErr ? (
            <div className="rounded-lg border px-3 py-2 text-sm border-rose-500/30 bg-rose-500/10 text-rose-400">
              {editorErr}
            </div>
          ) : null}

          <div>
            <label className={labelClass()}>{t("actorProfiles.name")}</label>
            <input
              value={editor.name}
              onChange={(e) => setEditor((prev) => ({ ...prev, name: e.target.value }))}
              className={inputClass()}
            />
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label className={labelClass()}>{t("actorProfiles.runtime")}</label>
              <select
                value={editor.runtime}
                onChange={(e) => {
                  const nextRuntime = String(e.target.value || "");
                  setEditor((prev) => {
                    const supportsDefault = supportsRuntimeDefaultCommand(nextRuntime);
                    return {
                      ...prev,
                      runtime: nextRuntime,
                      runner: supportsStandardWebHeadlessRuntime(nextRuntime) ? prev.runner : "pty",
                      useDefaultCommand: supportsDefault ? prev.useDefaultCommand : false,
                      command: supportsDefault && prev.useDefaultCommand ? "" : prev.command,
                    };
                  });
                }}
                className={inputClass()}
              >
                {SUPPORTED_RUNTIMES.map((rt) => (
                  <option key={rt} value={rt}>{RUNTIME_INFO[rt]?.label || rt}</option>
                ))}
              </select>
            </div>

            <div>
              <label className={labelClass()}>{t("actorProfiles.runner")}</label>
              <div className="grid grid-cols-2 gap-2">
                <button
                  type="button"
                  className={modeButtonClass(editor.runner === "pty")}
                  onClick={() => setEditor((prev) => ({ ...prev, runner: "pty" }))}
                >
                  {t("actorProfiles.pty")}
                </button>
                <button
                  type="button"
                  className={modeButtonClass(editor.runner === "headless")}
                  onClick={() => setEditor((prev) => ({ ...prev, runner: "headless" }))}
                  disabled={!editorSupportsHeadlessRunner}
                >
                  {t("actorProfiles.headless")}
                </button>
              </div>
              <div className="mt-1 text-[10px] text-[var(--color-text-muted)]">
                {editorSupportsHeadlessRunner
                  ? t("actorProfiles.runnerModeHint")
                  : t("actorProfiles.runnerModeHeadlessNote")}
              </div>
            </div>
          </div>

          <div>
            <label className={labelClass()}>{t("actorProfiles.commandOverrideOptional")}</label>
            {editorSupportsDefaultCommand ? (
              <label className="inline-flex items-center gap-2 text-xs mb-2 text-[var(--color-text-secondary)]">
                <input
                  type="checkbox"
                  checked={editor.useDefaultCommand}
                  onChange={(e) => {
                    const checked = e.target.checked;
                    setEditor((prev) => ({
                      ...prev,
                      useDefaultCommand: checked,
                      command: checked ? "" : prev.command,
                    }));
                  }}
                />
                {t("actorProfiles.useRuntimeDefaultCommand")}
              </label>
            ) : null}
            {!editorSupportsDefaultCommand || !editor.useDefaultCommand ? (
              <input
                value={editor.command}
                onChange={(e) => setEditor((prev) => ({ ...prev, command: e.target.value }))}
                className={`${inputClass()} font-mono`}
                placeholder={editorDefaultCommand || "codex"}
              />
            ) : null}
            {editorSupportsDefaultCommand && editorDefaultCommand ? (
              <div className="text-[10px] mt-1 text-[var(--color-text-muted)]">
                {editor.useDefaultCommand ? t("actorProfiles.usingRuntimeDefaultCommand") : t("actorProfiles.default")}{" "}
                <code className="px-1 rounded bg-[var(--color-bg-secondary)]">{editorDefaultCommand}</code>
              </div>
            ) : null}
          </div>

          <div>
            <label className={labelClass()}>{t("actorProfiles.submit")}</label>
            <select
              value={editor.submit}
              onChange={(e) => setEditor((prev) => ({ ...prev, submit: (e.target.value as "enter" | "newline" | "none") }))}
              className={inputClass()}
            >
              <option value="enter">Enter</option>
              <option value="newline">Newline</option>
              <option value="none">None</option>
            </select>
          </div>

          <div className={cardClass()}>
            <div className="text-sm font-semibold text-[var(--color-text-primary)]">
              {t("actorProfiles.capabilityDefaults")}
            </div>
            <div className="text-xs mt-1 text-[var(--color-text-muted)]">
              {t("actorProfiles.capabilityDefaultsHint")}
            </div>
            <div className="mt-3">
              <CapabilityPicker
                isDark={isDark}
                value={parseCapabilityIdInput(editor.capabilityAutoloadText)}
                onChange={(next) =>
                  setEditor((prev) => ({
                    ...prev,
                    capabilityAutoloadText: formatCapabilityIdInput(next),
                  }))
                }
                disabled={editorBusy}
                label={t("actorProfiles.autoloadCapabilities")}
              />
            </div>
            <div className="mt-3 grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div>
                <label className={labelClass()}>{t("actorProfiles.autoloadScope")}</label>
                <select
                  value={editor.capabilityDefaultScope}
                  onChange={(e) =>
                    setEditor((prev) => ({
                      ...prev,
                      capabilityDefaultScope: e.target.value === "session" ? "session" : "actor",
                    }))
                  }
                  className={inputClass()}
                >
                  <option value="actor">{t("actorProfiles.autoloadScopeActor")}</option>
                  <option value="session">{t("actorProfiles.autoloadScopeSession")}</option>
                </select>
              </div>
              <div>
                <label className={labelClass()}>{t("actorProfiles.sessionTtlSeconds")}</label>
                <input
                  type="number"
                  min={60}
                  step={60}
                  value={String(editor.capabilitySessionTtlSeconds || 3600)}
                  onChange={(e) =>
                    setEditor((prev) => ({
                      ...prev,
                      capabilitySessionTtlSeconds: Math.max(60, Number(e.target.value || 3600)),
                    }))
                  }
                  className={inputClass()}
                  disabled={editor.capabilityDefaultScope !== "session"}
                />
              </div>
            </div>
          </div>

          <div className={cardClass()}>
            <div className="text-sm font-semibold text-[var(--color-text-primary)]">{t("actorProfiles.env")}</div>
            <div className="text-xs mt-1 text-[var(--color-text-muted)]">{t("actorProfiles.envHint")}</div>
            {duplicateSourceProfileId ? (
              <div className="text-xs mt-1 text-[var(--color-text-tertiary)]">
                {t("actorProfiles.duplicateSecretsHint", { source: duplicateSourceLabel })}
              </div>
            ) : null}
            {secretKeys.length ? (
              <div className="mt-2 flex flex-wrap gap-1.5">
                {secretKeys.map((key) => (
                  <span
                    key={key}
                    title={secretMasks[key] ? `${key}=${secretMasks[key]}` : key}
                    className="px-2 py-0.5 rounded text-[11px] bg-[var(--color-bg-secondary)] text-[var(--color-text-secondary)]"
                  >
                    {key}
                  </span>
                ))}
              </div>
            ) : (
              <div className="mt-2 text-xs text-[var(--color-text-muted)]">{t("actorProfiles.noSecrets")}</div>
            )}

            <div className="mt-3">
              <label className={labelClass()}>{t("actorProfiles.setSecrets")}</label>
              <textarea
                value={secretSetText}
                onChange={(e) => setSecretSetText(e.target.value)}
                className={`${inputClass()} min-h-[90px] font-mono`}
                placeholder={t("actorProfiles.setSecretsPlaceholder")}
              />
            </div>
            <div className="mt-3">
              <label className={labelClass()}>{t("actorProfiles.unsetSecrets")}</label>
              <textarea
                value={secretUnsetText}
                onChange={(e) => setSecretUnsetText(e.target.value)}
                className={`${inputClass()} min-h-[70px] font-mono`}
                placeholder={t("actorProfiles.unsetSecretsPlaceholder")}
              />
            </div>
            <label className="mt-3 inline-flex items-center gap-2 text-sm text-[var(--color-text-secondary)]">
              <input type="checkbox" checked={secretClear} onChange={(e) => setSecretClear(e.target.checked)} />
              {t("actorProfiles.clearSecrets")}
            </label>
          </div>
        </div>
        <div className={settingsDialogFooterClass}>
          <button
            type="button"
            onClick={closeEditor}
            className={secondaryButtonClass()}
          >
            {t("common:cancel")}
          </button>
          <button
            type="button"
            onClick={() => void handleSave()}
            disabled={editorBusy}
            className={primaryButtonClass(editorBusy)}
          >
            {editorBusy ? t("common:saving") : t("common:save")}
          </button>
        </div>
      </div>
    </div>
  ) : null;

  const loadProfiles = async () => {
    setBusy(true);
    setErr("");
    try {
      const session = await ensureSessionContext();
      if (isMyScope && !(session?.signedIn && session.userId)) {
        setProfiles([]);
        setErr(t("actorProfiles.myProfilesLoginRequired"));
        return;
      }
      const resp = await api.listProfiles(isMyScope ? "my" : "global");
      if (resp.ok) {
        setProfiles(Array.isArray(resp.result?.profiles) ? resp.result.profiles : []);
      } else {
        setErr(scopeRequestError(resp.error?.code, resp.error?.message || t("actorProfiles.loadFailed")));
      }
    } catch (e) {
      console.error("Failed to load actor profiles:", e);
      setErr(t("actorProfiles.loadFailed"));
    } finally {
      setBusy(false);
    }
  };

  const refreshAllGroupsActors = async () => {
    await refreshGroups();
    const latestGroups = useGroupStore.getState().groups;
    const ids = Array.from(
      new Set(
        (Array.isArray(latestGroups) ? latestGroups : groups)
          .map((item) => String(item?.group_id || "").trim())
          .filter((id) => id.length > 0)
      )
    );
    await Promise.all(
      ids.map(async (gid) => {
        try {
          await refreshActors(gid);
        } catch (e) {
          console.error(`Failed to refresh actors for group=${gid}:`, e);
        }
      })
    );
  };

  const loadProfileSecrets = async (profileId: string) => {
    if (!profileId) {
      setSecretKeys([]);
      setSecretMasks({});
      return;
    }
    if (isMyScope && !profileLookup.ownerId) {
      setSecretKeys([]);
      setSecretMasks({});
      return;
    }
    const resp = await api.fetchProfilePrivateEnvKeys(profileId, profileLookup);
    if (!resp.ok) {
      setEditorErr(resp.error?.message || t("actorProfiles.loadSecretsFailed"));
      setSecretKeys([]);
      setSecretMasks({});
      return;
    }
    const keys = Array.isArray(resp.result?.keys) ? resp.result.keys : [];
    const masked = (resp.result?.masked_values && typeof resp.result.masked_values === "object")
      ? resp.result.masked_values
      : {};
    setSecretKeys(keys);
    setSecretMasks(masked);
  };

  useEffect(() => {
    if (isActive) {
      void loadProfiles();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isActive]);

  const openNew = () => {
    setEditor(buildEditor());
    setSecretKeys([]);
    setSecretMasks({});
    setSecretSetText("");
    setSecretUnsetText("");
    setSecretClear(false);
    setDuplicateSourceProfileId("");
    setEditorErr("");
    setEditorOpen(true);
  };

  const openEdit = async (profile: ActorProfile) => {
    setEditor(buildEditor(profile));
    setSecretSetText("");
    setSecretUnsetText("");
    setSecretClear(false);
    setDuplicateSourceProfileId("");
    setEditorErr("");
    setEditorOpen(true);
    await loadProfileSecrets(String(profile.id || ""));
  };

  const openDuplicate = async (profile: ActorProfile) => {
    const sourceId = String(profile.id || "").trim();
    setEditor({
      ...buildEditor(profile),
      id: "",
      revision: 0,
      name: `${String(profile.name || "").trim()} Copy`.trim(),
    });
    setSecretKeys([]);
    setSecretMasks({});
    setSecretSetText("");
    setSecretUnsetText("");
    setSecretClear(false);
    setDuplicateSourceProfileId(sourceId);
    setEditorErr("");
    setEditorOpen(true);
    await loadProfileSecrets(sourceId);
  };

  const handleDelete = async (profile: ActorProfile) => {
    if (!window.confirm(t("actorProfiles.deleteConfirm", { name: profile.name || profile.id }))) return;
    const ownerId = isMyScope ? sessionUserId.trim() : "";
    if (isMyScope && !ownerId) {
      setErr(t("actorProfiles.myProfilesLoginRequired"));
      return;
    }
    setBusy(true);
    setErr("");
    try {
      const resp = await api.deleteProfile(String(profile.id || ""), {
        scope: profileScope,
        ownerId,
      });
      if (!resp.ok) {
        const code = String(resp.error?.code || "").trim();
        if (code === "profile_in_use") {
          const details = resp.error?.details as { usage?: ActorProfileUsage[] } | undefined;
          const usage = Array.isArray(details?.usage) ? details.usage : [];
          const lines = usage
            .slice(0, 6)
            .map((item) => {
              const gid = String(item.group_id || "").trim();
              const aid = String(item.actor_id || "").trim();
              if (!gid || !aid) return "";
              const gtitle = String(item.group_title || "").trim();
              const atitle = String(item.actor_title || "").trim();
              const groupLabel = gtitle ? `${gtitle} (${gid})` : gid;
              const actorLabel = atitle ? `${atitle} (${aid})` : aid;
              return `${t("actorProfiles.deleteInUseEntryGroup", { group: groupLabel })}\n  ${t("actorProfiles.deleteInUseEntryActor", { actor: actorLabel })}`;
            })
            .filter((line) => line.length > 0)
            .join("\n");
          const over = usage.length > 6 ? `\n${t("actorProfiles.deleteInUseMore", { count: usage.length - 6 })}` : "";
          const withUsage = lines ? `\n\n${lines}${over}` : "";
          const forceConfirm = window.confirm(
            `${t("actorProfiles.deleteInUseConfirm", { count: usage.length || Number(profile.usage_count || 0) })}${withUsage}\n\n${t(
              "actorProfiles.deleteInUseForce"
            )}`
          );
          if (!forceConfirm) return;
          const forceResp = await api.deleteProfile(String(profile.id || ""), {
            scope: profileScope,
            ownerId,
            forceDetach: true,
          });
          if (!forceResp.ok) {
            setErr(scopeRequestError(forceResp.error?.code, forceResp.error?.message || t("actorProfiles.deleteFailed")));
            return;
          }
          await loadProfiles();
          await refreshAllGroupsActors();
          return;
        }
        setErr(scopeRequestError(resp.error?.code, resp.error?.message || t("actorProfiles.deleteFailed")));
        return;
      }
      await loadProfiles();
    } catch (e) {
      console.error(`Failed to delete actor profile id=${String(profile.id || "")}:`, e);
      setErr(t("actorProfiles.deleteFailed"));
    } finally {
      setBusy(false);
    }
  };

  const handleShowUsage = async (profile: ActorProfile) => {
    const profileId = String(profile.id || "").trim();
    if (!profileId) return;
    setUsageBusyProfileId(profileId);
    setErr("");
    try {
      const resp = await api.getProfile(profileId, {
        scope: profileScope,
        ownerId: isMyScope ? sessionUserId.trim() : "",
      });
      if (!resp.ok) {
        setErr(scopeRequestError(resp.error?.code, resp.error?.message || t("actorProfiles.usageLoadFailed")));
        return;
      }
      const usage = Array.isArray(resp.result?.usage) ? resp.result.usage : [];
      if (!usage.length) {
        window.alert(t("actorProfiles.usageEmpty"));
        return;
      }
      const title = t("actorProfiles.usageDialogTitle", { name: profile.name || profile.id || profileId });
      const lines = usage
        .map((item) => {
          const gid = String(item.group_id || "").trim();
          const aid = String(item.actor_id || "").trim();
          if (!gid || !aid) return "";
          const gtitle = String(item.group_title || "").trim();
          const atitle = String(item.actor_title || "").trim();
          const groupLabel = gtitle ? `${gtitle} (${gid})` : gid;
          const actorLabel = atitle ? `${atitle} (${aid})` : aid;
          return `${t("actorProfiles.deleteInUseEntryGroup", { group: groupLabel })}\n${t("actorProfiles.deleteInUseEntryActor", { actor: actorLabel })}`;
        })
        .filter((line) => line.length > 0)
        .join("\n\n");
      window.alert(lines ? `${title}\n\n${lines}` : `${title}\n\n${t("actorProfiles.usageEmpty")}`);
    } catch (e) {
      console.error(`Failed to load usage for actor profile id=${profileId}:`, e);
      setErr(t("actorProfiles.usageLoadFailed"));
    } finally {
      setUsageBusyProfileId("");
    }
  };

  const handleSave = async () => {
    const name = editor.name.trim();
    if (!name) {
      setEditorErr(t("actorProfiles.nameRequired"));
      return;
    }
    const ownerId = isMyScope ? sessionUserId.trim() : "";
    if (isMyScope && !ownerId) {
      setEditorErr(t("actorProfiles.myProfilesLoginRequired"));
      return;
    }
    setEditorBusy(true);
    setEditorErr("");
    try {
      const setParsed = parsePrivateEnvSetText(secretSetText);
      if (!setParsed.ok) {
        setEditorErr(setParsed.error);
        return;
      }
      const unsetParsed = parsePrivateEnvUnsetText(secretUnsetText);
      if (!unsetParsed.ok) {
        setEditorErr(unsetParsed.error);
        return;
      }

      const payload: Record<string, unknown> = {
        id: editor.id || undefined,
        name,
        scope: profileScope,
        owner_id: ownerId,
        runtime: editor.runtime,
        runner: editorSupportsHeadlessRunner ? editor.runner : "pty",
        command: editorSupportsDefaultCommand && editor.useDefaultCommand ? "" : editor.command.trim(),
        submit: editor.submit,
        env: {},
        capability_defaults: {
          autoload_capabilities: parseCapabilityIdInput(editor.capabilityAutoloadText),
          default_scope: editor.capabilityDefaultScope,
          session_ttl_seconds: Math.max(60, Math.trunc(editor.capabilitySessionTtlSeconds || 3600)),
        },
      };
      if (
        editorSupportsDefaultCommand &&
        !editor.useDefaultCommand &&
        !String(payload.command || "").trim()
      ) {
        setEditorErr(t("actorProfiles.commandOverrideRequired"));
        return;
      }
      if (
        String(editor.runtime || "").trim() === "custom" &&
        !String(payload.command || "").trim()
      ) {
        setEditorErr(t("actorProfiles.customRuntimeCommandRequired"));
        return;
      }
      const copyFromProfileId = duplicateSourceProfileId.trim();
      const hasSecretOps = secretClear || Object.keys(setParsed.setVars).length > 0 || unsetParsed.unsetKeys.length > 0;
      const expectedRevision = editor.id ? editor.revision : undefined;
      const upsertResp = await api.saveProfile(payload, expectedRevision);
      if (!upsertResp.ok) {
        setEditorErr(scopeRequestError(upsertResp.error?.code, upsertResp.error?.message || t("actorProfiles.saveFailed")));
        return;
      }
      const profile = upsertResp.result?.profile;
      const profileId = String(profile?.id || "").trim();
      if (!profileId) {
        setEditorErr(t("actorProfiles.saveFailed"));
        return;
      }

      if (copyFromProfileId && copyFromProfileId !== profileId) {
        const copyResp = await api.copyProfilePrivateEnvFromProfile(profileId, copyFromProfileId, profileLookup);
        if (!copyResp.ok) {
          setEditorErr(copyResp.error?.message || t("actorProfiles.saveSecretsFailed"));
          return;
        }
      }

      if (hasSecretOps) {
        const secretResp = await api.updateProfilePrivateEnv(
          profileId,
          setParsed.setVars,
          unsetParsed.unsetKeys,
          secretClear,
          profileLookup
        );
        if (!secretResp.ok) {
          setEditorErr(secretResp.error?.message || t("actorProfiles.saveSecretsFailed"));
          return;
        }
      }
      await loadProfiles();
      setDuplicateSourceProfileId("");
      setEditorOpen(false);
    } catch {
      setEditorErr(t("actorProfiles.saveFailed"));
    } finally {
      setEditorBusy(false);
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={t("actorProfiles.searchPlaceholder")}
          className={`${inputClass()} max-w-sm`}
        />
        <button className={primaryButtonClass(false)} onClick={openNew}>
          {t("actorProfiles.newProfile")}
        </button>
        <button
          className="glass-btn text-[var(--color-text-secondary)] px-3 py-2 rounded-lg text-sm min-h-[44px] font-medium transition-colors"
          onClick={() => void loadProfiles()}
          disabled={busy}
        >
          {busy ? t("common:loading") : t("actorProfiles.refresh")}
        </button>
      </div>

      {err ? (
        <div className="rounded-lg border px-3 py-2 text-sm border-rose-500/30 bg-rose-500/10 text-rose-400">
          {err}
        </div>
      ) : null}

      <div className="space-y-2">
        {filtered.map((profile) => (
          <div key={profile.id} className={cardClass()}>
            <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
              <div className="min-w-0">
              <div className="text-sm font-semibold truncate text-[var(--color-text-primary)]">
                  {profile.name || profile.id}
                </div>
                <div className="mt-0.5 text-xs text-[var(--color-text-tertiary)]">
                  <code>{profile.id}</code> · {RUNTIME_INFO[String(profile.runtime)]?.label || profile.runtime}
                </div>
                <div className="mt-0.5 text-[11px] text-[var(--color-text-muted)]">
                  {t("actorProfiles.usageCount", { count: Number(profile.usage_count || 0) })} · {t("actorProfiles.revision", { revision: Number(profile.revision || 0) })}
                </div>
              </div>
              <div className="flex flex-wrap gap-2">
                <button
                  onClick={() => void openEdit(profile)}
                  className="glass-btn text-[var(--color-text-secondary)] px-3 py-2 rounded-lg text-sm min-h-[40px]"
                >
                  {t("common:edit")}
                </button>
                <button
                  onClick={() => void openDuplicate(profile)}
                  className="glass-btn text-[var(--color-text-secondary)] px-3 py-2 rounded-lg text-sm min-h-[40px]"
                >
                  {t("actorProfiles.duplicate")}
                </button>
                <button
                  onClick={() => void handleShowUsage(profile)}
                  disabled={usageBusyProfileId === String(profile.id || "")}
                  className="glass-btn text-[var(--color-text-secondary)] px-3 py-2 rounded-lg text-sm min-h-[40px] disabled:opacity-60"
                >
                  {usageBusyProfileId === String(profile.id || "") ? t("common:loading") : t("actorProfiles.viewUsage")}
                </button>
                <button
                  onClick={() => void handleDelete(profile)}
                  className="px-3 py-2 rounded-lg text-sm min-h-[40px] bg-rose-500/15 text-rose-600 dark:text-rose-400 border border-rose-500/30 hover:bg-rose-500/25 transition-colors"
                >
                  {t("common:delete")}
                </button>
              </div>
            </div>
          </div>
        ))}
        {!busy && filtered.length === 0 ? (
          <div className="text-sm text-[var(--color-text-muted)]">{t("actorProfiles.empty")}</div>
        ) : null}
      </div>

      {editorModal && typeof document !== "undefined" ? createPortal(editorModal, document.body) : editorModal}
    </div>
  );
}
