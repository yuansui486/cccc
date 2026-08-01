import { useEffect, useState } from "react";
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

  const load = async () => {
    if (!groupId) return;
    setBusy(true);
    setError("");
    try {
      const [identityResp, registrationsResp, trustsResp, requestsResp] = await Promise.all([
        api.groupBridgeApi.identity(groupId),
        api.groupBridgeApi.registrations(groupId),
        api.groupBridgeApi.trusts(groupId),
        api.groupBridgeApi.pairingRequests(groupId),
      ]);
      if (!identityResp.ok) throw new Error(identityResp.error?.message || t("groupBridge.loadFailed"));
      if (!registrationsResp.ok) throw new Error(registrationsResp.error?.message || t("groupBridge.loadFailed"));
      if (!trustsResp.ok) throw new Error(trustsResp.error?.message || t("groupBridge.loadFailed"));
      if (!requestsResp.ok) throw new Error(requestsResp.error?.message || t("groupBridge.loadFailed"));
      setIdentity(identityResp.result.identity);
      setRegistrations(registrationsResp.result.registrations || []);
      setTrusts(trustsResp.result.trusts || []);
      setRequests(requestsResp.result.requests || []);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("groupBridge.loadFailed"));
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    void load();
    // The selected group is the only source for this management view.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [groupId]);

  const runAction = async <T,>(key: string, action: () => Promise<api.ApiResponse<T>>, success: string) => {
    setBusyAction(key);
    setError("");
    setNotice("");
    try {
      const response = await action();
      if (!response.ok) {
        setError(response.error?.message || t("groupBridge.actionFailed"));
        return null;
      }
      setNotice(success);
      await load();
      return response;
    } catch (err) {
      setError(err instanceof Error ? err.message : t("groupBridge.actionFailed"));
      return null;
    } finally {
      setBusyAction("");
    }
  };

  const createInvite = async () => {
    if (!groupId) return;
    const result = await runAction(
      "invite",
      () => api.groupBridgeApi.invite({
        groupId,
        expectedRemoteGroupId: remoteGroupId.trim(),
        expectedRemotePeerId: remotePeerId.trim(),
        multiaddrs: multiaddrs.split(/\s*,\s*|\n/).map((item) => item.trim()).filter(Boolean),
        ttlSeconds,
      }),
      t("groupBridge.inviteCreated"),
    );
    if (result?.ok) {
      setPairingCode(String(result.result.invite?.pairing_code || "").trim());
      setRemoteGroupId("");
      setRemotePeerId("");
      setMultiaddrs("");
    }
  };

  const copyPairingCode = async () => {
    if (!pairingCode || !navigator.clipboard) return;
    await navigator.clipboard.writeText(pairingCode);
    setNotice(t("groupBridge.codeCopied", { defaultValue: "Pairing code copied." }));
  };

  if (!groupId) {
    return <div className="text-sm text-[var(--color-text-secondary)]">{t("groupBridge.openFromGroup")}</div>;
  }

  return (
    <div className="space-y-4">
      {error ? <div role="alert" className="rounded-xl border border-rose-500/30 bg-rose-500/10 p-3 text-sm text-rose-700 dark:text-rose-300">{error}</div> : null}
      {notice ? <div role="status" className="rounded-xl border border-emerald-500/30 bg-emerald-500/10 p-3 text-sm text-emerald-700 dark:text-emerald-300">{notice}</div> : null}

      <section className={settingsWorkspaceShellClass(isDark)}>
        <div className={settingsWorkspaceHeaderClass(isDark)}>
          <div>
            <h3 className="text-sm font-semibold text-[var(--color-text-primary)]">{t("groupBridge.identityTitle")}</h3>
            <p className="mt-1 text-xs text-[var(--color-text-tertiary)]">{t("groupBridge.identityHint")}</p>
          </div>
          <button type="button" className={secondaryButtonClass("sm")} onClick={() => void load()} disabled={busy || !!busyAction}>
            {t("groupBridge.refresh")}
          </button>
        </div>
        <div className={settingsWorkspacePanelClass(isDark)}>
          <dl className="grid gap-3 text-sm sm:grid-cols-3">
            <div><dt className="text-xs text-[var(--color-text-tertiary)]">{t("groupBridge.peerId")}</dt><dd className="mt-1 break-all font-mono text-xs text-[var(--color-text-primary)]">{shortValue(identity.peer_id, 42) || "-"}</dd></div>
            <div><dt className="text-xs text-[var(--color-text-tertiary)]">{t("groupBridge.nodeId")}</dt><dd className="mt-1 break-all font-mono text-xs text-[var(--color-text-primary)]">{shortValue(identity.node_id, 42) || "-"}</dd></div>
            <div><dt className="text-xs text-[var(--color-text-tertiary)]">{t("groupBridge.publicKey")}</dt><dd className="mt-1 break-all font-mono text-xs text-[var(--color-text-primary)]">{shortValue(identity.public_key, 42) || "-"}</dd></div>
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
            <input className={inputClass(isDark)} value={remoteGroupId} onChange={(event) => setRemoteGroupId(event.target.value)} placeholder="group_..." />
          </label>
          <label className={labelClass(isDark)}>{t("groupBridge.remotePeerId")}
            <input className={inputClass(isDark)} value={remotePeerId} onChange={(event) => setRemotePeerId(event.target.value)} placeholder="peer_..." />
          </label>
          <label className={labelClass(isDark)}>{t("groupBridge.multiaddrs")}
            <input className={inputClass(isDark)} value={multiaddrs} onChange={(event) => setMultiaddrs(event.target.value)} placeholder="https://remote.example" />
          </label>
          <label className={labelClass(isDark)}>{t("groupBridge.ttl")}
            <input className={inputClass(isDark)} type="number" min={60} max={3600} value={ttlSeconds} onChange={(event) => setTtlSeconds(Number(event.target.value) || 600)} />
          </label>
        </div>
        <div className={settingsWorkspaceActionBarClass(isDark)}>
          <button type="button" className={primaryButtonClass(busyAction === "invite")} onClick={() => void createInvite()} disabled={busy || !!busyAction || !remoteGroupId.trim() || !remotePeerId.trim()}>
            {busyAction === "invite" ? t("groupBridge.working") : t("groupBridge.createInvite")}
          </button>
        </div>
        {pairingCode ? (
          <div className="mx-4 mb-4 rounded-lg border border-amber-500/30 bg-amber-500/10 p-3">
            <div className="text-xs font-medium text-amber-800 dark:text-amber-200">{t("groupBridge.pairingCode", { defaultValue: "One-time pairing code" })}</div>
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <code className="rounded border border-amber-500/30 bg-black/5 px-2 py-1 text-sm font-semibold tracking-[0.12em] text-[var(--color-text-primary)] dark:bg-white/10">{pairingCode}</code>
              <button type="button" className={secondaryButtonClass("sm")} onClick={() => void copyPairingCode()} title={t("groupBridge.copyCode", { defaultValue: "Copy pairing code" })} aria-label={t("groupBridge.copyCode", { defaultValue: "Copy pairing code" })}>
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
          {requests.length === 0 ? <p className="text-sm text-[var(--color-text-tertiary)]">{t("groupBridge.noRequests")}</p> : requests.map((request) => (
            <div key={request.request_id} className="rounded-lg border border-[var(--glass-border-subtle)] p-3">
              <div className="flex flex-wrap items-start justify-between gap-3"><div className="min-w-0"><div className="font-medium text-[var(--color-text-primary)]">{request.remote_group_title || request.remote_group_id}</div><div className="mt-1 break-all text-xs text-[var(--color-text-tertiary)]">{request.remote_peer_id} · {request.status}</div></div>
                {request.status === "pending" || request.status === "approving" ? <div className="flex gap-2"><button type="button" className={primaryButtonClass(busyAction === `approve:${request.request_id}`)} disabled={!!busyAction} onClick={() => void runAction(`approve:${request.request_id}`, () => api.groupBridgeApi.approve(groupId, request.request_id), t("groupBridge.approved"))}>{t("groupBridge.approve")}</button><button type="button" className={dangerButtonClass("sm")} disabled={!!busyAction} onClick={() => void runAction(`reject:${request.request_id}`, () => api.groupBridgeApi.reject(groupId, request.request_id), t("groupBridge.rejected"))}>{t("groupBridge.reject")}</button></div> : null}
              </div>
            </div>
          ))}
        </div>
      </section>

      <section className={settingsWorkspaceShellClass(isDark)}>
        <div className={settingsWorkspaceHeaderClass(isDark)}><div><h3 className="text-sm font-semibold text-[var(--color-text-primary)]">{t("groupBridge.trustTitle")}</h3><p className="mt-1 text-xs text-[var(--color-text-tertiary)]">{t("groupBridge.trustHint")}</p></div></div>
        <div className="space-y-2 p-4">
          {trusts.length === 0 ? <p className="text-sm text-[var(--color-text-tertiary)]">{t("groupBridge.noTrusts")}</p> : trusts.map((trust) => (
            <div key={trust.trust_id} className="rounded-lg border border-[var(--glass-border-subtle)] p-3">
              <div className="flex flex-wrap items-start justify-between gap-3"><div className="min-w-0"><div className="font-medium text-[var(--color-text-primary)]">{trust.remote_group_title || trust.remote_group_id}</div><div className="mt-1 break-all text-xs text-[var(--color-text-tertiary)]">{trust.remote_peer_id} · {trust.status}</div></div><button type="button" className={dangerButtonClass("sm")} disabled={!!busyAction} onClick={() => void runAction(`revoke:${trust.trust_id}`, () => api.groupBridgeApi.revoke(groupId, trust.trust_id, trust.revision), t("groupBridge.revoked"))}>{t("groupBridge.revoke")}</button></div>
              <div className="mt-3 flex flex-wrap items-center gap-3"><label className="text-xs text-[var(--color-text-secondary)]">{t("groupBridge.accessLevel")}
                <select className={inputClass(isDark) + " mt-1 min-w-40"} value={trust.access_level} disabled={!!busyAction} onChange={(event) => void runAction(`access:${trust.trust_id}`, () => api.groupBridgeApi.setAccess(groupId, trust.trust_id, event.target.value as GroupBridgeAccessLevel, trust.revision), t("groupBridge.accessUpdated"))}><option value="messages">{t("groupBridge.messages")}</option><option value="read">{t("groupBridge.read")}</option><option value="full">{t("groupBridge.full")}</option></select>
              </label><span className="text-xs text-[var(--color-text-tertiary)]">{trust.remote_endpoint || t("groupBridge.endpointUnavailable")}</span></div>
            </div>
          ))}
        </div>
      </section>

      <section className={settingsWorkspaceShellClass(isDark)}>
        <div className={settingsWorkspaceHeaderClass(isDark)}><div><h3 className="text-sm font-semibold text-[var(--color-text-primary)]">{t("groupBridge.registrationsTitle")}</h3><p className="mt-1 text-xs text-[var(--color-text-tertiary)]">{t("groupBridge.registrationsHint")}</p></div></div>
        <div className="space-y-2 p-4">{registrations.length === 0 ? <p className="text-sm text-[var(--color-text-tertiary)]">{t("groupBridge.noRegistrations")}</p> : registrations.map((registration) => <div key={registration.registration_id} className="rounded-lg border border-[var(--glass-border-subtle)] p-3 text-sm"><div className="font-medium text-[var(--color-text-primary)]">{registration.remote_group_id || registration.registration_id}</div><div className="mt-1 break-all text-xs text-[var(--color-text-tertiary)]">{registration.remote_peer_id} · {registration.url} · {registration.status}</div></div>)}</div>
      </section>
    </div>
  );
}
