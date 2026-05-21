import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import * as api from "../../../services/api";
import type { IMPlatform, IMStatus, WeixinLoginStatus } from "../../../types";
import { IMConfigDraft, saveAndStartIMBridge, saveIMConfigDraft } from "./imBridgeConfig";

const supportedIMPlatforms = new Set<IMPlatform>(["feishu", "dingtalk", "wecom", "weixin"]);

function normalizeIMPlatform(platform?: string | null): IMPlatform {
  return supportedIMPlatforms.has(platform as IMPlatform) ? (platform as IMPlatform) : "feishu";
}

function createEmptyDraft(): IMConfigDraft {
  return {
    botTokenEnv: "",
    appTokenEnv: "",
    feishuDomain: "https://open.feishu.cn",
    feishuAppId: "",
    feishuAppSecret: "",
    dingtalkAppKey: "",
    dingtalkAppSecret: "",
    dingtalkRobotCode: "",
    wecomBotId: "",
    wecomSecret: "",
    weixinAccountId: "",
  };
}

export function useIMBridgeSettings({ active, groupId }: { active: boolean; groupId?: string }) {
  const { t } = useTranslation("settings");
  const [imStatus, setImStatus] = useState<IMStatus | null>(null);
  const [imPlatform, setImPlatform] = useState<IMPlatform>("feishu");
  const [imBotTokenEnv, setImBotTokenEnv] = useState("");
  const [imAppTokenEnv, setImAppTokenEnv] = useState("");
  const [imFeishuDomain, setImFeishuDomain] = useState("https://open.feishu.cn");
  const [imFeishuAppId, setImFeishuAppId] = useState("");
  const [imFeishuAppSecret, setImFeishuAppSecret] = useState("");
  const [imDingtalkAppKey, setImDingtalkAppKey] = useState("");
  const [imDingtalkAppSecret, setImDingtalkAppSecret] = useState("");
  const [imDingtalkRobotCode, setImDingtalkRobotCode] = useState("");
  const [imWecomBotId, setImWecomBotId] = useState("");
  const [imWecomSecret, setImWecomSecret] = useState("");
  const [imWeixinAccountId, setImWeixinAccountId] = useState("");
  const [weixinLoginStatus, setWeixinLoginStatus] = useState<WeixinLoginStatus | null>(null);
  const [imBusy, setImBusy] = useState(false);
  const [imConfigDrafts, setImConfigDrafts] = useState<Partial<Record<IMPlatform, IMConfigDraft>>>({});
  const imLoadSeq = useRef(0);
  const weixinAutoStartRef = useRef(false);

  const applyIMConfigDraft = useCallback((draft: IMConfigDraft) => {
    setImBotTokenEnv(draft.botTokenEnv);
    setImAppTokenEnv(draft.appTokenEnv);
    setImFeishuDomain(draft.feishuDomain);
    setImFeishuAppId(draft.feishuAppId);
    setImFeishuAppSecret(draft.feishuAppSecret);
    setImDingtalkAppKey(draft.dingtalkAppKey);
    setImDingtalkAppSecret(draft.dingtalkAppSecret);
    setImDingtalkRobotCode(draft.dingtalkRobotCode);
    setImWecomBotId(draft.wecomBotId);
    setImWecomSecret(draft.wecomSecret);
    setImWeixinAccountId(draft.weixinAccountId);
  }, []);

  const resetIMState = useCallback(() => {
    setImStatus(null);
    setImPlatform("feishu");
    applyIMConfigDraft(createEmptyDraft());
  }, [applyIMConfigDraft]);

  const loadIMStatus = useCallback(async (opts?: { resetFirst?: boolean }) => {
    const gid = String(groupId || "").trim();
    const seq = ++imLoadSeq.current;
    if (opts?.resetFirst) resetIMState();
    if (!gid) return;
    try {
      const statusResp = await api.fetchIMStatus(gid);
      if (seq !== imLoadSeq.current) return;
      if (statusResp.ok) {
        setImStatus(statusResp.result);
        if (statusResp.result.platform) {
          setImPlatform(normalizeIMPlatform(statusResp.result.platform));
        }
      }
      const configResp = await api.fetchIMConfig(gid);
      if (seq !== imLoadSeq.current) return;
      if (configResp.ok && configResp.result.im) {
        const im = configResp.result.im;
        if (im.platform) setImPlatform(normalizeIMPlatform(im.platform));
        setImBotTokenEnv(im.bot_token_env || im.bot_token || im.token_env || im.token || "");
        setImAppTokenEnv(im.app_token_env || im.app_token || "");
        const raw = String(im.feishu_domain || "https://open.feishu.cn").trim();
        const canon = raw
          .replace(/\/+$/, "")
          .replace(/\/open-apis$/, "")
          .replace(/^open\.larksuite\.com$/i, "https://open.larkoffice.com")
          .replace(/^https?:\/\/open\.larksuite\.com$/i, "https://open.larkoffice.com")
          .replace(/^open\.larkoffice\.com$/i, "https://open.larkoffice.com");
        setImFeishuDomain(canon);
        setImFeishuAppId(im.feishu_app_id || im.feishu_app_id_env || "");
        setImFeishuAppSecret(im.feishu_app_secret || im.feishu_app_secret_env || "");
        setImDingtalkAppKey(im.dingtalk_app_key || im.dingtalk_app_key_env || "");
        setImDingtalkAppSecret(im.dingtalk_app_secret || im.dingtalk_app_secret_env || "");
        setImDingtalkRobotCode(im.dingtalk_robot_code || im.dingtalk_robot_code_env || "");
        setImWecomBotId(im.wecom_bot_id || "");
        setImWecomSecret(im.wecom_secret || "");
        setImWeixinAccountId(im.weixin_account_id || "");
      }
    } catch (e) {
      console.error("Failed to load IM status:", e);
    }
  }, [groupId, resetIMState]);

  const getCurrentIMConfigDraft = useCallback((): IMConfigDraft => ({
    botTokenEnv: imBotTokenEnv,
    appTokenEnv: imAppTokenEnv,
    feishuDomain: imFeishuDomain,
    feishuAppId: imFeishuAppId,
    feishuAppSecret: imFeishuAppSecret,
    dingtalkAppKey: imDingtalkAppKey,
    dingtalkAppSecret: imDingtalkAppSecret,
    dingtalkRobotCode: imDingtalkRobotCode,
    wecomBotId: imWecomBotId,
    wecomSecret: imWecomSecret,
    weixinAccountId: imWeixinAccountId,
  }), [
    imAppTokenEnv,
    imBotTokenEnv,
    imDingtalkAppKey,
    imDingtalkAppSecret,
    imDingtalkRobotCode,
    imFeishuAppId,
    imFeishuAppSecret,
    imFeishuDomain,
    imWecomBotId,
    imWecomSecret,
    imWeixinAccountId,
  ]);

  const getCurrentIMSaveRequest = useCallback(() => ({
    groupId: String(groupId || ""),
    platform: imPlatform,
    ...getCurrentIMConfigDraft(),
  }), [getCurrentIMConfigDraft, groupId, imPlatform]);

  const toWeixinErrorStatus = useCallback((message: string): WeixinLoginStatus => ({
    status: "error",
    logged_in: false,
    account_id: "",
    qrcode_url: "",
    qr_ascii: "",
    error: String(message || "").trim(),
    running: false,
    pid: null,
    updated_at: new Date().toISOString(),
  }), []);

  useEffect(() => {
    if (!active) return;
    loadIMStatus({ resetFirst: true });
  }, [active, groupId, loadIMStatus]);

  useEffect(() => {
    if (!active || !groupId || imPlatform !== "weixin") return;
    let cancelled = false;
    const loadWeixinStatus = async () => {
      try {
        const resp = await api.fetchWeixinLoginStatus(groupId);
        if (cancelled) return;
        if (resp.ok) {
          setWeixinLoginStatus(resp.result ?? null);
        } else {
          setWeixinLoginStatus(toWeixinErrorStatus(resp.error?.message || t("imBridge.weixinStatusLoadFailed")));
        }
      } catch {
        if (!cancelled) {
          setWeixinLoginStatus(toWeixinErrorStatus(t("imBridge.weixinStatusLoadFailed")));
        }
      }
    };
    void loadWeixinStatus();
    const needsPoll = weixinLoginStatus?.status === "waiting_scan";
    if (!needsPoll) return () => { cancelled = true; };
    const timer = window.setInterval(() => {
      void loadWeixinStatus();
    }, 3000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [active, groupId, imPlatform, t, toWeixinErrorStatus, weixinLoginStatus?.status]);

  useEffect(() => {
    if (imPlatform !== "weixin") {
      weixinAutoStartRef.current = false;
      return;
    }
    if (!groupId) return;
    if (!weixinLoginStatus?.logged_in) return;
    if (!imStatus?.configured || String(imStatus.platform || "") !== "weixin") return;
    if (!imStatus.enabled) {
      weixinAutoStartRef.current = false;
      return;
    }
    if (imStatus.running) {
      weixinAutoStartRef.current = false;
      return;
    }
    if (weixinAutoStartRef.current) return;

    weixinAutoStartRef.current = true;
    void (async () => {
      setImBusy(true);
      try {
        const resp = await api.startIMBridge(groupId);
        if (resp.ok) {
          await loadIMStatus();
        }
      } catch (e) {
        console.error("Failed to auto-start weixin bridge:", e);
      } finally {
        setImBusy(false);
      }
    })();
  }, [groupId, imPlatform, imStatus, loadIMStatus, weixinLoginStatus]);

  const handlePlatformChange = (newPlatform: IMPlatform) => {
    if (newPlatform === imPlatform) return;
    setImConfigDrafts((prev) => ({
      ...prev,
      [imPlatform]: getCurrentIMConfigDraft(),
    }));
    const cachedDraft = imConfigDrafts[newPlatform];
    applyIMConfigDraft(cachedDraft || createEmptyDraft());
    setImPlatform(newPlatform);
  };

  const handleSaveIMConfig = async () => {
    if (!groupId) return;
    setImBusy(true);
    try {
      const resp = await saveIMConfigDraft(getCurrentIMSaveRequest());
      if (resp.ok) await loadIMStatus();
    } catch (e) {
      console.error("Failed to save IM config:", e);
    } finally {
      setImBusy(false);
    }
  };

  const handleRemoveIMConfig = async () => {
    if (!groupId) return;
    setImBusy(true);
    try {
      const resp = await api.unsetIMConfig(groupId);
      if (resp.ok) {
        applyIMConfigDraft(createEmptyDraft());
        await loadIMStatus();
      }
    } catch (e) {
      console.error("Failed to remove IM config:", e);
    } finally {
      setImBusy(false);
    }
  };

  const handleStartBridge = async () => {
    if (!groupId) return;
    setImBusy(true);
    try {
      const resp = await saveAndStartIMBridge(getCurrentIMSaveRequest());
      if (resp.ok) await loadIMStatus();
    } catch (e) {
      console.error("Failed to start bridge:", e);
    } finally {
      setImBusy(false);
    }
  };

  const handleStopBridge = async () => {
    if (!groupId) return;
    setImBusy(true);
    try {
      await api.stopIMBridge(groupId);
      await loadIMStatus();
    } catch (e) {
      console.error("Failed to stop bridge:", e);
    } finally {
      setImBusy(false);
    }
  };

  const handleStartWeixinLogin = async () => {
    if (!groupId) return;
    setImBusy(true);
    try {
      const saveResp = await saveIMConfigDraft(getCurrentIMSaveRequest());
      if (!saveResp.ok) {
        setWeixinLoginStatus(toWeixinErrorStatus(saveResp.error?.message || t("imBridge.weixinStartFailed")));
        return;
      }
      await loadIMStatus();
      weixinAutoStartRef.current = false;
      const resp = await api.startWeixinLogin(groupId);
      if (resp.ok) {
        setWeixinLoginStatus(resp.result ?? null);
      } else {
        setWeixinLoginStatus(toWeixinErrorStatus(resp.error?.message || t("imBridge.weixinStartFailed")));
      }
    } catch (e) {
      setWeixinLoginStatus(toWeixinErrorStatus(t("imBridge.weixinStartFailed")));
      console.error("Failed to start weixin login:", e);
    } finally {
      setImBusy(false);
    }
  };

  const handleLogoutWeixin = async () => {
    if (!groupId) return;
    setImBusy(true);
    try {
      weixinAutoStartRef.current = false;
      const resp = await api.logoutWeixin(groupId);
      if (resp.ok) {
        setWeixinLoginStatus(resp.result ?? null);
      } else {
        setWeixinLoginStatus(toWeixinErrorStatus(resp.error?.message || t("imBridge.weixinLogoutFailed")));
      }
    } catch (e) {
      setWeixinLoginStatus(toWeixinErrorStatus(t("imBridge.weixinLogoutFailed")));
      console.error("Failed to logout weixin:", e);
    } finally {
      setImBusy(false);
    }
  };

  return {
    imStatus,
    imPlatform,
    onPlatformChange: handlePlatformChange,
    imBotTokenEnv,
    setImBotTokenEnv,
    imAppTokenEnv,
    setImAppTokenEnv,
    imFeishuDomain,
    setImFeishuDomain,
    imFeishuAppId,
    setImFeishuAppId,
    imFeishuAppSecret,
    setImFeishuAppSecret,
    imDingtalkAppKey,
    setImDingtalkAppKey,
    imDingtalkAppSecret,
    setImDingtalkAppSecret,
    imDingtalkRobotCode,
    setImDingtalkRobotCode,
    imWecomBotId,
    setImWecomBotId,
    imWecomSecret,
    setImWecomSecret,
    imWeixinAccountId,
    setImWeixinAccountId,
    weixinLoginStatus,
    onStartWeixinLogin: handleStartWeixinLogin,
    onLogoutWeixin: handleLogoutWeixin,
    imBusy,
    onSaveConfig: handleSaveIMConfig,
    onRemoveConfig: handleRemoveIMConfig,
    onStartBridge: handleStartBridge,
    onStopBridge: handleStopBridge,
  };
}
