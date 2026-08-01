import { useLayoutEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Copy } from "lucide-react";
import * as api from "../../../services/api";
import type {
  GroupBridgeAccessLevel,
  GroupBridgeIdentity,
  GroupBridgePairingRequest,
  GroupBridgeRegistration,
  GroupBridgeTrust,
} from "../../../services/api/groupBridge";
import {
  inputClass,
  labelClass,
  primaryButtonClass,
  secondaryButtonClass,
  dangerButtonClass,
  settingsWorkspaceActionBarClass,
  settingsWorkspaceHeaderClass,
  settingsWorkspacePanelClass,
  settingsWorkspaceShellClass,
} from "./types";
import {
  GroupBridgeLifecycle,
  isGroupBridgeViewCurrent,
  normalizeGroupBridgeGroupId,
} from "./groupBridgeLifecycle";

interface GroupBridgeTabProps {
  isDark: boolean;
  groupId?: string;
}

const emptyIdentity: GroupBridgeIdentity = { peer_id: "", public_key: "", node_id: "" };

function shortValue(value: string, limit = 32): string {
  const normalized = String(value || "").trim();
  if (normalized.length <= limit) return normalized;
  return `${normalized.slice(0, Math.max(8, limit - 8))}...${normalized.slice(-6)}`;
}

export function GroupBridgeTab({ isDark, groupId }: GroupBridgeTabProps) {
  const { t } = useTranslation("settings");
  const [lifecycle] = useState(() => new GroupBridgeLifecycle());
  const [identity, setIdentity] = useState<GroupBridgeIdentity>(emptyIdentity);
  const [registrations, setRegistrations] = useState<GroupBridgeRegistration[]>([]);
  const [trusts, setTrusts] = useState<GroupBridgeTrust[]>([]);
  const [requests, setRequests] = useState<GroupBridgePairingRequest[]>([]);
  const [busy, setBusy] = useState(false);
  const [busyAction, setBusyAction] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [remoteGroupId, setRemoteGroupId] = useState("");
  const [remotePeerId, setRemotePeerId] = useState("");
  const [multiaddrs, setMultiaddrs] = useState("");
  const [ttlSeconds, setTtlSeconds] = useState(600);
  const [pairingCode, setPairingCode] = useState("");
  const [viewGroupId, setViewGroupId] = useState(() => String(groupId || "").trim());

  const selectedGroupId = normalizeGroupBridgeGroupId(groupId);
  const viewCurrent = isGroupBridgeViewCurrent(selectedGroupId, viewGroupId);
  const visibleIdentity = viewCurrent ? identity : emptyIdentity;
  const visibleRegistrations = viewCurrent ? registrations : [];
  const visibleTrusts = viewCurrent ? trusts : [];
  const visibleRequests = viewCurrent ? requests : [];
  const visibleError = viewCurrent ? error : "";
  const visibleNotice = viewCurrent ? notice : "";
  const visibleRemoteGroupId = viewCurrent ? remoteGroupId : "";
  const visibleRemotePeerId = viewCurrent ? remotePeerId : "";
  const visibleMultiaddrs = viewCurrent ? multiaddrs : "";
  const visibleTtlSeconds = viewCurrent ? ttlSeconds : 600;
  const visiblePairingCode = viewCurrent ? pairingCode : "";
  const mutationsDisabled = !viewCurrent || busy || !!busyAction || !lifecycle.canMutate(selectedGroupId);

  const load = async (targetGroupId: string) => {
    const token = lifecycle.beginLoad(targetGroupId);
    if (!token) return false;
    setBusy(true);
    setError("");
    try {
      const [identityResp, registrationsResp, trustsResp, requestsResp] = await Promise.all([
        api.groupBridgeApi.identity(targetGroupId),
        api.groupBridgeApi.registrations(targetGroupId),
        api.groupBridgeApi.trusts(targetGroupId),
        api.groupBridgeApi.pairingRequests(targetGroupId),
      ]);
      if (!identityResp.ok) throw new Error(identityResp.error?.message || t("groupBridge.loadFailed"));
      if (!registrationsResp.ok) throw new Error(registrationsResp.error?.message || t("groupBridge.loadFailed"));
      if (!trustsResp.ok) throw new Error(trustsResp.error?.message || t("groupBridge.loadFailed"));
      if (!requestsResp.ok) throw new Error(requestsResp.error?.message || t("groupBridge.loadFailed"));
      return lifecycle.commit(token, () => {
        setIdentity(identityResp.result.identity);
        setRegistrations(registrationsResp.result.registrations || []);
        setTrusts(trustsResp.result.trusts || []);
        setRequests(requestsResp.result.requests || []);
      });
    } catch (err) {
      lifecycle.commit(token, () => {
        setError(err instanceof Error ? err.message : t("groupBridge.loadFailed"));
      });
      return false;
    } finally {
      if (lifecycle.finish(token)) setBusy(false);
    }
  };

  useLayoutEffect(() => {
    const selected = lifecycle.selectGroup(selectedGroupId);
    setViewGroupId(selected.groupId);
    setIdentity(emptyIdentity);
    setRegistrations([]);
    setTrusts([]);
    setRequests([]);
    setBusy(false);
    setBusyAction("");
    setError("");
    setNotice("");
    setRemoteGroupId("");
    setRemotePeerId("");
    setMultiaddrs("");
    setTtlSeconds(600);
    setPairingCode("");
    if (selected.groupId) void load(selected.groupId);
    // The selected group is the only source for this management view.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedGroupId]);

  const runAction = async <T,>(
    targetGroupId: string,
    key: string,
    action: (actionGroupId: string) => Promise<api.ApiResponse<T>>,
    success: string,
  ) => {
    const token = lifecycle.beginAction(targetGroupId);
    if (!token) return null;
    setBusyAction(key);
    setError("");
    setNotice("");
    try {
      const response = await action(targetGroupId);
      if (!lifecycle.isCurrent(token)) return null;
      if (!response.ok) {
        lifecycle.commit(token, () => {
          setError(response.error?.message || t("groupBridge.actionFailed"));
        });
        return null;
      }
      lifecycle.commit(token, () => setNotice(success));
      await load(targetGroupId);
      if (!lifecycle.isCurrent(token)) return null;
      return response;
    } catch (err) {
      lifecycle.commit(token, () => {
        setError(err instanceof Error ? err.message : t("groupBridge.actionFailed"));
      });
      return null;
    } finally {
      if (lifecycle.finish(token)) setBusyAction("");
    }
  };

  const createInvite = async () => {
    if (!selectedGroupId) return;
    const result = await runAction(
      selectedGroupId,
      "invite",
      (actionGroupId) => api.groupBridgeApi.invite({
        groupId: actionGroupId,
        expectedRemoteGroupId: remoteGroupId.trim(),
        expectedRemotePeerId: remotePeerId.trim(),
        multiaddrs: multiaddrs.split(/\s*,\s*|\n/).map((item) => item.trim()).filter(Boolean),
        ttlSeconds,
      }),
      t("groupBridge.inviteCreated"),
    );
    if (result?.ok && lifecycle.isViewCurrent(selectedGroupId)) {
      setPairingCode(String(result.result.invite?.pairing_code || "").trim());
      setRemoteGroupId("");
      setRemotePeerId("");
      setMultiaddrs("");
    }
  };

  const copyPairingCode = async () => {
    if (!selectedGroupId || !pairingCode || !navigator.clipboard) return;
    const token = lifecycle.beginAction(selectedGroupId);
    if (!token) return;
    setBusyAction("copy");
    setError("");
    setNotice("");
    try {
      await navigator.clipboard.writeText(pairingCode);
      lifecycle.commit(token, () => {
        setNotice(t("groupBridge.codeCopied", { defaultValue: "Pairing code copied." }));
      });
    } catch (err) {
      lifecycle.commit(token, () => {
        setError(err instanceof Error ? err.message : t("groupBridge.actionFailed"));
      });
    } finally {
      if (lifecycle.finish(token)) setBusyAction("");
    }
  };

  if (!selectedGroupId) {
    return <div className="text-sm text-[var(--color-text-secondary)]">{t("groupBridge.openFromGroup")}</div>;
  }

  return (
    <div className="space-y-4">
      {visibleError ? <div role="alert" className="rounded-xl border border-rose-500/30 bg-rose-500/10 p-3 text-sm text-rose-700 dark:text-rose-300">{visibleError}</div> : null}
      {visibleNotice ? <div role="status" className="rounded-xl border border-emerald-500/30 bg-emerald-500/10 p-3 text-sm text-emerald-700 dark:text-emerald-300">{visibleNotice}</div> : null}

      <section className={settingsWorkspaceShellClass(isDark)}>
        <div className={settingsWorkspaceHeaderClass(isDark)}>
          <div>
            <h3 className="text-sm font-semibold text-[var(--color-text-primary)]">{t("groupBridge.identityTitle")}</h3>
            <p className="mt-1 text-xs text-[var(--color-text-tertiary)]">{t("groupBridge.identityHint")}</p>
          </div>
          <button type="button" className={secondaryButtonClass("sm")} onClick={() => void load(selectedGroupId)} disabled={!viewCurrent || busy || !!busyAction}>
            {t("groupBridge.refresh")}
          </button>
        </div>
        <div className={settingsWorkspacePanelClass(isDark)}>
          <dl className="grid gap-3 text-sm sm:grid-cols-3">
            <div><dt className="text-xs text-[var(--color-text-tertiary)]">{t("groupBridge.peerId")}</dt><dd className="mt-1 break-all font-mono text-xs text-[var(--color-text-primary)]">{shortValue(visibleIdentity.peer_id, 42) || "-"}</dd></div>
            <div><dt className="text-xs text-[var(--color-text-tertiary)]">{t("groupBridge.nodeId")}</dt><dd className="mt-1 break-all font-mono text-xs text-[var(--color-text-primary)]">{shortValue(visibleIdentity.node_id, 42) || "-"}</dd></div>
            <div><dt className="text-xs text-[var(--color-text-tertiary)]">{t("groupBridge.publicKey")}</dt><dd className="mt-1 break-all font-mono text-xs text-[var(--color-text-primary)]">{shortValue(visibleIdentity.public_key, 42) || "-"}</dd></div>
          </dl>
          <p className="mt-3 text-xs text-[var(--color-text-tertiary)]">{t("groupBridge.credentialFree")}</p>
        </div>
      </section>

      <section className={settingsWorkspaceShellClass(isDark)}>
        <div className={settingsWorkspaceHeaderClass(isDark)}>
          <div><h3 className="text-sm font-semibold text-[var(--color-text-primary)]">{t("groupBridge.inviteTitle")}</h3><p className="mt-1 text-xs text-[var(--color-text-tertiary)]">{t("groupBridge.inviteHint")}</p></div>
        </div>
        <div className="grid gap-3 p-4 sm:grid-cols-2">
          <label className={labelClass(isDark)}>{t("groupBridge.remoteGroupId")}
            <input className={inputClass(isDark)} value={visibleRemoteGroupId} disabled={mutationsDisabled} onChange={(event) => setRemoteGroupId(event.target.value)} placeholder="group_..." />
          </label>
          <label className={labelClass(isDark)}>{t("groupBridge.remotePeerId")}
            <input className={inputClass(isDark)} value={visibleRemotePeerId} disabled={mutationsDisabled} onChange={(event) => setRemotePeerId(event.target.value)} placeholder="peer_..." />
          </label>
          <label className={labelClass(isDark)}>{t("groupBridge.multiaddrs")}
            <input className={inputClass(isDark)} value={visibleMultiaddrs} disabled={mutationsDisabled} onChange={(event) => setMultiaddrs(event.target.value)} placeholder="https://remote.example" />
          </label>
          <label className={labelClass(isDark)}>{t("groupBridge.ttl")}
            <input className={inputClass(isDark)} type="number" min={60} max={3600} value={visibleTtlSeconds} disabled={mutationsDisabled} onChange={(event) => setTtlSeconds(Number(event.target.value) || 600)} />
          </label>
        </div>
        <div className={settingsWorkspaceActionBarClass(isDark)}>
          <button type="button" className={primaryButtonClass(busyAction === "invite")} onClick={() => void createInvite()} disabled={mutationsDisabled || !visibleRemoteGroupId.trim() || !visibleRemotePeerId.trim()}>
            {busyAction === "invite" ? t("groupBridge.working") : t("groupBridge.createInvite")}
          </button>
        </div>
        {visiblePairingCode ? (
          <div className="mx-4 mb-4 rounded-lg border border-amber-500/30 bg-amber-500/10 p-3">
            <div className="text-xs font-medium text-amber-800 dark:text-amber-200">{t("groupBridge.pairingCode", { defaultValue: "One-time pairing code" })}</div>
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <code className="rounded border border-amber-500/30 bg-black/5 px-2 py-1 text-sm font-semibold tracking-[0.12em] text-[var(--color-text-primary)] dark:bg-white/10">{visiblePairingCode}</code>
              <button type="button" className={secondaryButtonClass("sm")} disabled={mutationsDisabled} onClick={() => void copyPairingCode()} title={t("groupBridge.copyCode", { defaultValue: "Copy pairing code" })} aria-label={t("groupBridge.copyCode", { defaultValue: "Copy pairing code" })}>
                <Copy size={14} aria-hidden="true" />
                {t("groupBridge.copyCode", { defaultValue: "Copy code" })}
              </button>
            </div>
            <p className="mt-2 text-xs text-amber-800/80 dark:text-amber-200/80">{t("groupBridge.codeOneTimeHint", { defaultValue: "Store or share this code now. It is only returned when the invite is created." })}</p>
          </div>
        ) : null}
      </section>

      <section className={settingsWorkspaceShellClass(isDark)}>
        <div className={settingsWorkspaceHeaderClass(isDark)}><div><h3 className="text-sm font-semibold text-[var(--color-text-primary)]">{t("groupBridge.requestsTitle")}</h3><p className="mt-1 text-xs text-[var(--color-text-tertiary)]">{t("groupBridge.requestsHint")}</p></div></div>
        <div className="space-y-2 p-4">
          {visibleRequests.length === 0 ? <p className="text-sm text-[var(--color-text-tertiary)]">{t("groupBridge.noRequests")}</p> : visibleRequests.map((request) => (
            <div key={request.request_id} className="rounded-lg border border-[var(--glass-border-subtle)] p-3">
              <div className="flex flex-wrap items-start justify-between gap-3"><div className="min-w-0"><div className="font-medium text-[var(--color-text-primary)]">{request.remote_group_title || request.remote_group_id}</div><div className="mt-1 break-all text-xs text-[var(--color-text-tertiary)]">{request.remote_peer_id} · {request.status}</div></div>
                {request.status === "pending" || request.status === "approving" ? <div className="flex gap-2"><button type="button" className={primaryButtonClass(busyAction === `approve:${request.request_id}`)} disabled={mutationsDisabled} onClick={() => void runAction(selectedGroupId, `approve:${request.request_id}`, (actionGroupId) => api.groupBridgeApi.approve(actionGroupId, request.request_id), t("groupBridge.approved"))}>{t("groupBridge.approve")}</button><button type="button" className={dangerButtonClass("sm")} disabled={mutationsDisabled} onClick={() => void runAction(selectedGroupId, `reject:${request.request_id}`, (actionGroupId) => api.groupBridgeApi.reject(actionGroupId, request.request_id), t("groupBridge.rejected"))}>{t("groupBridge.reject")}</button></div> : null}
              </div>
            </div>
          ))}
        </div>
      </section>

      <section className={settingsWorkspaceShellClass(isDark)}>
        <div className={settingsWorkspaceHeaderClass(isDark)}><div><h3 className="text-sm font-semibold text-[var(--color-text-primary)]">{t("groupBridge.trustTitle")}</h3><p className="mt-1 text-xs text-[var(--color-text-tertiary)]">{t("groupBridge.trustHint")}</p></div></div>
        <div className="space-y-2 p-4">
          {visibleTrusts.length === 0 ? <p className="text-sm text-[var(--color-text-tertiary)]">{t("groupBridge.noTrusts")}</p> : visibleTrusts.map((trust) => (
            <div key={trust.trust_id} className="rounded-lg border border-[var(--glass-border-subtle)] p-3">
              <div className="flex flex-wrap items-start justify-between gap-3"><div className="min-w-0"><div className="font-medium text-[var(--color-text-primary)]">{trust.remote_group_title || trust.remote_group_id}</div><div className="mt-1 break-all text-xs text-[var(--color-text-tertiary)]">{trust.remote_peer_id} · {trust.status}</div></div><button type="button" className={dangerButtonClass("sm")} disabled={mutationsDisabled} onClick={() => void runAction(selectedGroupId, `revoke:${trust.trust_id}`, (actionGroupId) => api.groupBridgeApi.revoke(actionGroupId, trust.trust_id, trust.revision), t("groupBridge.revoked"))}>{t("groupBridge.revoke")}</button></div>
              <div className="mt-3 flex flex-wrap items-center gap-3"><label className="text-xs text-[var(--color-text-secondary)]">{t("groupBridge.accessLevel")}
                <select className={inputClass(isDark) + " mt-1 min-w-40"} value={trust.access_level} disabled={mutationsDisabled} onChange={(event) => void runAction(selectedGroupId, `access:${trust.trust_id}`, (actionGroupId) => api.groupBridgeApi.setAccess(actionGroupId, trust.trust_id, event.target.value as GroupBridgeAccessLevel, trust.revision), t("groupBridge.accessUpdated"))}><option value="messages">{t("groupBridge.messages")}</option><option value="read">{t("groupBridge.read")}</option><option value="full">{t("groupBridge.full")}</option></select>
              </label><span className="text-xs text-[var(--color-text-tertiary)]">{trust.remote_endpoint || t("groupBridge.endpointUnavailable")}</span></div>
            </div>
          ))}
        </div>
      </section>

      <section className={settingsWorkspaceShellClass(isDark)}>
        <div className={settingsWorkspaceHeaderClass(isDark)}><div><h3 className="text-sm font-semibold text-[var(--color-text-primary)]">{t("groupBridge.registrationsTitle")}</h3><p className="mt-1 text-xs text-[var(--color-text-tertiary)]">{t("groupBridge.registrationsHint")}</p></div></div>
        <div className="space-y-2 p-4">{visibleRegistrations.length === 0 ? <p className="text-sm text-[var(--color-text-tertiary)]">{t("groupBridge.noRegistrations")}</p> : visibleRegistrations.map((registration) => <div key={registration.registration_id} className="rounded-lg border border-[var(--glass-border-subtle)] p-3 text-sm"><div className="font-medium text-[var(--color-text-primary)]">{registration.remote_group_id || registration.registration_id}</div><div className="mt-1 break-all text-xs text-[var(--color-text-tertiary)]">{registration.remote_peer_id} · {registration.url} · {registration.status}</div></div>)}</div>
      </section>
    </div>
  );
}
