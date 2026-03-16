import { useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import * as api from "../../../services/api";
import type { Actor } from "../../../types";
import { buildHelpMarkdown, parseHelpMarkdown, type HelpChangedBlock, type ParsedHelpMarkdown } from "../../../utils/helpMarkdown";
import {
  cardClass,
  inputClass,
  labelClass,
  primaryButtonClass,
  preClass,
  secondaryButtonClass,
  settingsDialogBodyClass,
  settingsDialogPanelClass,
} from "./types";

type PromptKind = "preamble" | "help";
type PromptInfo = api.GroupPromptInfo;
type HelpViewMode = "structured" | "raw";
type HelpScopeId = "common" | "role:foreman" | "role:peer" | `actor:${string}`;

const EMPTY_HELP: ParsedHelpMarkdown = {
  common: "",
  foreman: "",
  peer: "",
  actorNotes: {},
  extraTaggedBlocks: [],
  usedLegacyRoleNotes: false,
};

function displayActorName(actor: Actor): string {
  return String(actor.title || actor.id || "").trim() || String(actor.id || "").trim();
}

function uniqueChangedBlocks(blocks: HelpChangedBlock[]): HelpChangedBlock[] {
  const seen = new Set<string>();
  const out: HelpChangedBlock[] = [];
  for (const block of blocks) {
    const key = String(block || "").trim();
    if (!key || seen.has(key)) continue;
    seen.add(key);
    out.push(block);
  }
  return out;
}

export function GuidanceTab({ isDark, groupId }: {
  isDark: boolean;
  groupId?: string;
}) {
  const { t } = useTranslation("settings");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [prompts, setPrompts] = useState<Record<PromptKind, PromptInfo> | null>(null);
  const [actors, setActors] = useState<Actor[]>([]);
  const [expandedKind, setExpandedKind] = useState<PromptKind | null>(null);
  const [helpViewMode, setHelpViewMode] = useState<HelpViewMode>("structured");
  const [helpStructured, setHelpStructured] = useState<ParsedHelpMarkdown>(EMPTY_HELP);
  const [helpTouchedRaw, setHelpTouchedRaw] = useState(false);
  const [helpChangedBlocks, setHelpChangedBlocks] = useState<HelpChangedBlock[]>([]);
  const [selectedHelpScope, setSelectedHelpScope] = useState<HelpScopeId>("common");

  const actorIds = useMemo(
    () => actors.map((actor) => String(actor.id || "").trim()).filter(Boolean),
    [actors]
  );
  const actorIdSet = useMemo(() => new Set(actorIds), [actorIds]);
  const orphanActorIds = useMemo(
    () => Object.keys(helpStructured.actorNotes).filter((actorId) => !actorIdSet.has(actorId)).sort(),
    [helpStructured.actorNotes, actorIdSet]
  );

  const syncHelpState = (content: string) => {
    setHelpStructured(parseHelpMarkdown(content));
  };

  const load = async () => {
    if (!groupId) return;
    setBusy(true);
    setErr("");
    try {
      const [promptsResp, actorsResp] = await Promise.all([
        api.fetchGroupPrompts(groupId),
        api.fetchActors(groupId, false),
      ]);
      if (!promptsResp.ok) {
        setErr(promptsResp.error?.message || t("guidance.failedToLoad"));
        setPrompts(null);
        setActors([]);
        return;
      }
      const p = promptsResp.result?.preamble;
      const h = promptsResp.result?.help;
      if (!p || !h) {
        setErr(t("guidance.invalidResponse"));
        setPrompts(null);
        setActors([]);
        return;
      }
      const nextActors = actorsResp.ok ? (actorsResp.result?.actors || []) : [];
      setPrompts({ preamble: p, help: h });
      setActors(nextActors);
      syncHelpState(String(h.content || ""));
      setHelpTouchedRaw(false);
      setHelpChangedBlocks([]);
      setHelpViewMode("structured");
      setSelectedHelpScope("common");
    } catch {
      setErr(t("guidance.failedToLoad"));
      setPrompts(null);
      setActors([]);
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    if (groupId) void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- reload when group changes.
  }, [groupId]);

  const setPromptContent = (kind: PromptKind, content: string) => {
    setPrompts((current) => {
      if (!current) return current;
      return { ...current, [kind]: { ...current[kind], content } };
    });
  };

  const setHelpContentRaw = (content: string) => {
    setPromptContent("help", content);
    setHelpStructured(parseHelpMarkdown(content));
    setHelpTouchedRaw(true);
    setHelpChangedBlocks([]);
  };

  const applyStructuredHelp = (next: ParsedHelpMarkdown, changed: HelpChangedBlock) => {
    const content = buildHelpMarkdown({
      common: next.common,
      foreman: next.foreman,
      peer: next.peer,
      actorNotes: next.actorNotes,
      actorOrder: actorIds,
      extraTaggedBlocks: next.extraTaggedBlocks,
    });
    setPromptContent("help", content);
    // Keep the live textarea state as typed. A build->parse roundtrip trims
    // trailing blank lines, which makes Enter feel broken in structured mode.
    setHelpStructured(next);
    setHelpChangedBlocks((current) => uniqueChangedBlocks([...current, changed]));
  };

  const updateCommon = (value: string) => {
    applyStructuredHelp({ ...helpStructured, common: value }, "common");
  };

  const updateRole = (role: "foreman" | "peer", value: string) => {
    if (role === "foreman") {
      applyStructuredHelp({ ...helpStructured, foreman: value }, "role:foreman");
      return;
    }
    applyStructuredHelp({ ...helpStructured, peer: value }, "role:peer");
  };

  const updateActorNote = (actorId: string, value: string) => {
    const nextActorNotes = { ...helpStructured.actorNotes, [actorId]: value };
    if (!String(value || "").trim()) delete nextActorNotes[actorId];
    applyStructuredHelp({ ...helpStructured, actorNotes: nextActorNotes }, `actor:${actorId}`);
  };

  const savePrompt = async (kind: PromptKind) => {
    if (!groupId || !prompts) return;
    setBusy(true);
    setErr("");
    try {
      const resp = await api.updateGroupPrompt(groupId, kind, prompts[kind].content || "");
      if (!resp.ok) {
        setErr(resp.error?.message || t("guidance.failedToSave", { kind }));
        return;
      }
      await load();
    } catch {
      setErr(t("guidance.failedToSave", { kind }));
    } finally {
      setBusy(false);
    }
  };

  const saveHelp = async () => {
    if (!groupId || !prompts) return;
    setBusy(true);
    setErr("");
    try {
      const resp = await api.updateGroupPrompt(
        groupId,
        "help",
        prompts.help.content || "",
        helpTouchedRaw
          ? { editorMode: "raw" }
          : { editorMode: "structured", changedBlocks: helpChangedBlocks }
      );
      if (!resp.ok) {
        setErr(resp.error?.message || t("guidance.failedToSave", { kind: "help" }));
        return;
      }
      await load();
    } catch {
      setErr(t("guidance.failedToSave", { kind: "help" }));
    } finally {
      setBusy(false);
    }
  };

  const resetPrompt = async (kind: PromptKind) => {
    if (!groupId || !prompts) return;
    const filename = prompts[kind]?.filename || kind;
    const ok = window.confirm(t("automation.resetGuidanceConfirm", { kind, filename }));
    if (!ok) return;
    setBusy(true);
    setErr("");
    try {
      const resp = await api.resetGroupPrompt(groupId, kind);
      if (!resp.ok) {
        setErr(resp.error?.message || t("guidance.failedToReset", { kind }));
        return;
      }
      await load();
    } catch {
      setErr(t("guidance.failedToReset", { kind }));
    } finally {
      setBusy(false);
    }
  };

  if (!groupId) {
    return (
      <div className={cardClass(isDark)}>
        <div className={`text-sm ${isDark ? "text-slate-300" : "text-gray-700"}`}>{t("guidance.openFromGroup")}</div>
      </div>
    );
  }

  const preamble = prompts?.preamble;
  const help = prompts?.help;
  const helpSource = help?.source || "builtin";
  const helpBadge =
    helpSource === "home"
      ? isDark
        ? "bg-sky-500/15 text-sky-300 border border-sky-500/30"
        : "bg-sky-50 text-sky-700 border border-sky-200"
      : isDark
        ? "bg-slate-800 text-slate-300 border border-slate-700"
        : "bg-gray-100 text-gray-700 border border-gray-200";

  const preambleSource = preamble?.source || "builtin";
  const preambleBadge =
    preambleSource === "home"
      ? isDark
        ? "bg-sky-500/15 text-sky-300 border border-sky-500/30"
        : "bg-sky-50 text-sky-700 border border-sky-200"
      : isDark
        ? "bg-slate-800 text-slate-300 border border-slate-700"
        : "bg-gray-100 text-gray-700 border border-gray-200";

  const renderSourceBadge = (kind: PromptKind) => {
    const badgeClass = kind === "help" ? helpBadge : preambleBadge;
    const source = kind === "help" ? helpSource : preambleSource;
    return (
      <div className={`px-2 py-1 rounded-md text-[11px] ${badgeClass}`}>
        {source === "home" ? t("guidance.overrideBadge") : t("guidance.builtinBadge")}
      </div>
    );
  };

  const renderPreambleCard = (expanded = false) => (
    <div className={`${cardClass(isDark)} ${expanded ? "max-w-5xl mx-auto" : ""}`}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className={`text-sm font-semibold ${isDark ? "text-slate-100" : "text-gray-900"}`}>{t("guidance.preambleTitle")}</div>
          <div className={`text-[11px] ${isDark ? "text-slate-500" : "text-gray-500"}`}>{t("guidance.preambleHint")}</div>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {!expanded ? (
            <button
              type="button"
              className={`px-2 py-1 rounded-md text-[11px] transition-colors ${
                isDark ? "bg-slate-900 hover:bg-slate-800 text-slate-300 border border-slate-800" : "bg-white hover:bg-gray-50 text-gray-700 border border-gray-200"
              }`}
              onClick={() => setExpandedKind("preamble")}
              disabled={busy}
              title={t("guidance.expandTitle")}
            >
              {t("guidance.expand")}
            </button>
          ) : null}
          <div className={`px-2 py-1 rounded-md text-[11px] ${preambleBadge}`}>
            {preambleSource === "home" ? t("guidance.overrideBadge") : t("guidance.builtinBadge")}
          </div>
        </div>
      </div>

      {preamble?.path ? (
        <div className={preClass(isDark)}>
          <span className="font-mono">{preamble.path}</span>
        </div>
      ) : null}

      <div className="mt-3">
        <label className={labelClass(isDark)}>{t("guidance.markdown")}</label>
        <textarea
          className={`${inputClass(isDark)} font-mono text-[12px]`}
          style={{ minHeight: expanded ? 440 : 220 }}
          value={preamble?.content || ""}
          onChange={(e) => setPromptContent("preamble", e.target.value)}
          spellCheck={false}
        />
      </div>

      <div className="mt-3 flex items-center gap-2">
        <button className={primaryButtonClass(busy)} onClick={() => void savePrompt("preamble")} disabled={busy}>
          {t("common:save")}
        </button>
        <button
          className={`px-4 py-2 text-sm rounded-lg min-h-[44px] transition-colors font-medium disabled:opacity-50 ${
            isDark ? "bg-slate-800 hover:bg-slate-700 text-slate-200" : "bg-gray-100 hover:bg-gray-200 text-gray-800"
          }`}
          onClick={() => void resetPrompt("preamble")}
          disabled={busy || preambleSource !== "home"}
          title={preambleSource === "home" ? t("guidance.resetHint") : t("guidance.noOverride")}
        >
          {t("common:reset")}
        </button>
        <button
          className={`ml-auto px-3 py-2 text-sm rounded-lg min-h-[44px] transition-colors disabled:opacity-50 ${
            isDark ? "bg-slate-900 hover:bg-slate-800 text-slate-300 border border-slate-800" : "bg-white hover:bg-gray-50 text-gray-700 border border-gray-200"
          }`}
          onClick={() => void load()}
          disabled={busy}
          title={t("guidance.discardChanges")}
        >
          {t("guidance.discardChanges")}
        </button>
      </div>
    </div>
  );


  const commonScope = {
    id: "common" as HelpScopeId,
    title: t("guidance.commonNotesTitle", "Common Notes"),
    hint: t("guidance.commonNotesHint", "Untagged help content shared by all actors."),
    placeholder: t("guidance.commonNotesPlaceholder", "Keep shared guidance, workflow details, and appendices here…"),
    value: helpStructured.common,
    roleLabel: undefined as string | undefined,
    isOrphan: false,
  };

  const foremanScope = {
    id: "role:foreman" as HelpScopeId,
    title: t("guidance.foremanNotesTitle", "Foreman Notes"),
    hint: t("guidance.foremanNotesHint", "Only foreman actors receive this scoped block."),
    placeholder: t("guidance.foremanNotesPlaceholder", "Own outcome quality, review peer outputs, and keep shared direction coherent…"),
    value: helpStructured.foreman,
    roleLabel: undefined as string | undefined,
    isOrphan: false,
  };

  const peerScope = {
    id: "role:peer" as HelpScopeId,
    title: t("guidance.peerNotesTitle", "Peer Notes"),
    hint: t("guidance.peerNotesHint", "Only peer actors receive this scoped block."),
    placeholder: t("guidance.peerNotesPlaceholder", "Report risks early, deliver verifiable outputs, and say when the direction is wrong…"),
    value: helpStructured.peer,
    roleLabel: undefined as string | undefined,
    isOrphan: false,
  };

  const actorScopes = actors.map((actor) => {
    const actorId = String(actor.id || "").trim();
    const note = String(helpStructured.actorNotes[actorId] || "");
    const roleLabel = String(actor.role || t("guidance.unknownRole", "Unknown")).trim() || t("guidance.unknownRole", "Unknown");
    return {
      id: `actor:${actorId}` as HelpScopeId,
      title: displayActorName(actor),
      hint: t("guidance.actorNotesHint", "Local notes for specific actors. This is the same source edited from the actor modal shortcut."),
      placeholder: t("guidance.actorNotePlaceholder", "Describe only this actor's local responsibilities, boundaries, and preferred behavior…"),
      value: note,
      roleLabel,
      isOrphan: false,
    };
  });

  const orphanActorScopes = orphanActorIds.map((actorId) => {
    const note = String(helpStructured.actorNotes[actorId] || "");
    return {
      id: `actor:${actorId}` as HelpScopeId,
      title: actorId,
      hint: t("guidance.actorNotesHint", "Local notes for specific actors. This is the same source edited from the actor modal shortcut."),
      placeholder: t("guidance.orphanActorNotePlaceholder", "Keep or clean this leftover note for an actor that no longer exists…"),
      value: note,
      roleLabel: t("guidance.orphanActorRole", "No longer in group"),
      isOrphan: true,
    };
  });

  const visibleActorScopes = [...actorScopes, ...orphanActorScopes];

  useEffect(() => {
    if (!visibleActorScopes.length) return;
    if (!visibleActorScopes.some((item) => item.id === selectedHelpScope)) {
      setSelectedHelpScope(visibleActorScopes[0].id);
    }
  }, [selectedHelpScope, visibleActorScopes]);

  const selectedHelpScopeItem = visibleActorScopes.find((item) => item.id === selectedHelpScope) || visibleActorScopes[0] || null;

  const updateSelectedHelpScopeValue = (value: string) => {
    if (!selectedHelpScopeItem) return;
    updateActorNote(selectedHelpScopeItem.id.slice("actor:".length), value);
  };

  const renderHelpScopeButton = (item: {
    id: HelpScopeId;
    title: string;
    roleLabel?: string;
  }) => {
    const active = item.id === selectedHelpScope;
    return (
      <button
        key={item.id}
        type="button"
        className={`w-full text-left rounded-xl border px-2.5 py-2 transition-colors ${
          active
            ? isDark
              ? "border-blue-500/40 bg-blue-500/10 text-slate-100"
              : "border-blue-200 bg-blue-50 text-gray-900"
            : isDark
              ? "border-slate-800 bg-slate-950/40 text-slate-300 hover:bg-slate-900"
              : "border-gray-200 bg-white text-gray-800 hover:bg-gray-50"
        }`}
        onClick={() => setSelectedHelpScope(item.id)}
      >
        <div className="flex items-center gap-1.5 min-w-0">
          <span className="font-medium truncate">{item.title}</span>
          {item.roleLabel ? (
            <span className={`text-[10px] px-1.5 py-0.5 rounded-full shrink-0 ${isDark ? "bg-slate-800 text-slate-300" : "bg-gray-100 text-gray-600"}`}>
              {item.roleLabel}
            </span>
          ) : null}
        </div>
      </button>
    );
  };

  const renderHelpCard = (expanded = false) => (
    <div className={`${expanded ? "flex h-full min-h-0 flex-col" : cardClass(isDark)}`}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className={`text-sm font-semibold ${isDark ? "text-slate-100" : "text-gray-900"}`}>{t("guidance.helpTitle")}</div>
          <div className={`text-[11px] ${isDark ? "text-slate-500" : "text-gray-500"}`}>{t("guidance.helpHint")}</div>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {!expanded ? (
            <button
              type="button"
              className={secondaryButtonClass("sm")}
              onClick={() => setExpandedKind("help")}
              disabled={busy}
              title={t("guidance.expandTitle")}
            >
              {t("guidance.expand")}
            </button>
          ) : null}
          {renderSourceBadge("help")}
        </div>
      </div>

      {help?.path ? (
        <div className={preClass(isDark)}>
          <span className="font-mono">{help.path}</span>
        </div>
      ) : null}

      <div className={`${expanded ? "mt-3 min-h-0 flex flex-1 flex-col" : `mt-3 rounded-xl border px-3 py-3 ${isDark ? "border-slate-800 bg-slate-950/30" : "border-gray-200 bg-white"}`}`}>
        <div className={`grid grid-cols-1 gap-4 ${expanded ? "min-h-0 flex-1 xl:grid-cols-[240px_minmax(0,1fr)]" : "items-start xl:grid-cols-[210px_minmax(0,1fr)]"}`}>
            <div className={`rounded-xl border p-2.5 ${isDark ? "border-slate-800 bg-slate-950/40" : "border-gray-200 bg-gray-50"} ${expanded ? "min-h-0 flex flex-col" : "space-y-2.5"}`}>
              <div className={`text-[11px] font-medium ${isDark ? "text-slate-400" : "text-gray-600"}`}>
                {t("guidance.actorNotesTitle", "Actor Notes")}
              </div>
              {visibleActorScopes.length ? (
                <div className={expanded ? "min-h-0 flex-1 space-y-2 overflow-y-auto pr-1" : "space-y-2 max-h-[420px] overflow-y-auto pr-1"}>
                  {visibleActorScopes.map((item) => renderHelpScopeButton(item))}
                </div>
              ) : (
                <div className={`rounded-lg border border-dashed px-3 py-4 text-sm ${isDark ? "border-slate-800 text-slate-500" : "border-gray-200 text-gray-400"}`}>
                  {t("guidance.noActorsForStructuredHelp", "No actors available in this group yet.")}
                </div>
              )}
            </div>

            <div className={`rounded-xl border p-3 ${isDark ? "border-slate-800 bg-slate-950/40" : "border-gray-200 bg-white"} ${expanded ? "min-h-0 flex flex-col" : ""}`}>
              {selectedHelpScopeItem ? (
                <>
                  <div className="flex items-start gap-3 mb-3">
                    <div className="min-w-0">
                      <div className={`text-xs font-medium ${isDark ? "text-slate-400" : "text-gray-500"}`}>
                        {t("guidance.editKind", { kind: selectedHelpScopeItem.title })}
                      </div>
                      <div className={`text-sm font-semibold mt-0.5 ${isDark ? "text-slate-100" : "text-gray-900"}`}>
                        {selectedHelpScopeItem.title}
                      </div>
                      <div className={`text-[11px] mt-1 ${isDark ? "text-slate-500" : "text-gray-500"}`}>
                        {selectedHelpScopeItem.hint}
                      </div>
                    </div>
                    {selectedHelpScopeItem.roleLabel ? (
                      <div className={`ml-auto text-[10px] px-2 py-1 rounded-full shrink-0 ${isDark ? "bg-slate-800 text-slate-300" : "bg-gray-100 text-gray-600"}`}>
                        {selectedHelpScopeItem.roleLabel}
                      </div>
                    ) : null}
                  </div>

                  <textarea
                    className={`${inputClass(isDark)} font-mono text-[12px] resize-y ${expanded ? "min-h-[440px] flex-1" : ""}`}
                    style={expanded ? undefined : { minHeight: 320, maxHeight: "44vh" }}
                    value={selectedHelpScopeItem.value}
                    onChange={(e) => updateSelectedHelpScopeValue(e.target.value)}
                    placeholder={selectedHelpScopeItem.placeholder}
                    spellCheck={false}
                  />
                </>
              ) : (
                <div className={`rounded-lg border border-dashed px-3 py-6 text-sm ${isDark ? "border-slate-800 text-slate-500" : "border-gray-200 text-gray-400"}`}>
                  {t("guidance.noActorsForStructuredHelp", "No actors available in this group yet.")}
                </div>
              )}
            </div>
          </div>
      </div>

      <div className="mt-3 flex items-center gap-2">
        <button className={primaryButtonClass(busy)} onClick={() => void saveHelp()} disabled={busy}>
          {t("common:save")}
        </button>
        <button
          className={`px-4 py-2 text-sm rounded-lg min-h-[44px] transition-colors font-medium disabled:opacity-50 ${
            isDark ? "bg-slate-800 hover:bg-slate-700 text-slate-200" : "bg-gray-100 hover:bg-gray-200 text-gray-800"
          }`}
          onClick={() => void resetPrompt("help")}
          disabled={busy || helpSource !== "home"}
          title={helpSource === "home" ? t("guidance.resetHint") : t("guidance.noOverride")}
        >
          {t("common:reset")}
        </button>
        <button
          className={`ml-auto px-3 py-2 text-sm rounded-lg min-h-[44px] transition-colors disabled:opacity-50 ${
            isDark ? "bg-slate-900 hover:bg-slate-800 text-slate-300 border border-slate-800" : "bg-white hover:bg-gray-50 text-gray-700 border border-gray-200"
          }`}
          onClick={() => void load()}
          disabled={busy}
          title={t("guidance.discardChanges")}
        >
          {t("guidance.discardChanges")}
        </button>
      </div>
    </div>
  );

  return (
    <div className="space-y-4">
      {err ? <div className={`text-sm ${isDark ? "text-rose-300" : "text-red-600"}`}>{err}</div> : null}
      {renderHelpCard()}

      {expandedKind && typeof document !== "undefined"
        ? createPortal(
            <div
              className="fixed inset-0 z-[1000] animate-fade-in"
              role="dialog"
              aria-modal="true"
              onPointerDown={(e) => {
                if (e.target === e.currentTarget) setExpandedKind(null);
              }}
            >
              <div className="absolute inset-0 glass-overlay" />
              <div className={settingsDialogPanelClass("xl")}>
                <div className="flex shrink-0 justify-end border-b border-[var(--glass-border-subtle)] px-3 py-2 sm:px-4 sm:py-3">
                  <button type="button" className={secondaryButtonClass("sm")} onClick={() => setExpandedKind(null)}>
                    {t("common:close")}
                  </button>
                </div>
                <div className={settingsDialogBodyClass}>
                  {renderHelpCard(true)}
                </div>
              </div>
            </div>,
            document.body
          )
        : null}
    </div>
  );
}
