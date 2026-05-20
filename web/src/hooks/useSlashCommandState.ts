import { useCallback, useEffect, useMemo, useState } from "react";

import * as api from "../services/api";
import {
  buildCapabilityOverviewSkillSlashCommands,
  buildSlashCommands,
  type SlashCommandItem,
} from "../utils/slashCommands";
import { subscribeCapabilityChanged } from "../utils/capabilityEvents";

const slashCommandCache = new Map<string, SlashCommandItem[]>();
const globalSkillCommandCache = new Map<string, SlashCommandItem[]>();

function cachedSlashCommands(groupId: string): SlashCommandItem[] {
  return slashCommandCache.get(groupId) || buildSlashCommands({ state: null });
}

function cacheSlashCommands(groupId: string, commands: SlashCommandItem[]): SlashCommandItem[] {
  slashCommandCache.set(groupId, commands);
  return commands;
}

function cachedGlobalSkillCommands(groupId: string): SlashCommandItem[] {
  return globalSkillCommandCache.get(groupId) || [];
}

function cacheGlobalSkillCommands(groupId: string, commands: SlashCommandItem[]): SlashCommandItem[] {
  globalSkillCommandCache.set(groupId, commands);
  return commands;
}

export function useSlashCommandState(selectedGroupId: string) {
  const selectedGid = useMemo(() => String(selectedGroupId || "").trim(), [selectedGroupId]);
  const [slashCommands, setSlashCommands] = useState<SlashCommandItem[]>(() => {
    return selectedGid ? cachedSlashCommands(selectedGid) : [];
  });
  const [globalSkillCommands, setGlobalSkillCommands] = useState<SlashCommandItem[]>(() => {
    return selectedGid ? cachedGlobalSkillCommands(selectedGid) : [];
  });

  const refreshSlashCommands = useCallback(async () => {
    const gid = selectedGid;
    if (!gid) return;
    try {
      const [stateResp, overviewResp] = await Promise.all([
        api.fetchSlashCommandCapabilityState(gid, "user", { noCache: true }),
        api.fetchCapabilityOverview({ includeIndexed: true, limit: 1200, kind: "skill" }),
      ]);
      const nextSlashCommands = buildSlashCommands({
        state: stateResp.ok ? stateResp.result : null,
      });
      setSlashCommands(cacheSlashCommands(gid, nextSlashCommands));
      if (overviewResp.ok) {
        setGlobalSkillCommands(cacheGlobalSkillCommands(gid, buildCapabilityOverviewSkillSlashCommands({
          items: overviewResp.result?.items,
          reservedCommands: nextSlashCommands,
        })));
      }
    } catch {
      setSlashCommands(cachedSlashCommands(gid));
      setGlobalSkillCommands(cachedGlobalSkillCommands(gid));
    }
  }, [selectedGid]);

  const visibleSlashCommands = selectedGid ? slashCommands : [];
  const visibleGlobalSkillCommands = selectedGid ? globalSkillCommands : [];

  useEffect(() => {
    let cancelled = false;
    const gid = selectedGid;
    if (!gid) return;
    queueMicrotask(() => {
      if (!cancelled) {
        setSlashCommands(cachedSlashCommands(gid));
        setGlobalSkillCommands(cachedGlobalSkillCommands(gid));
      }
    });
    void Promise.all([
      api.fetchSlashCommandCapabilityState(gid, "user"),
      api.fetchCapabilityOverview({ includeIndexed: true, limit: 1200, kind: "skill" }),
    ]).then(([stateResp, overviewResp]) => {
      if (cancelled) return;
      const nextSlashCommands = buildSlashCommands({
        state: stateResp.ok ? stateResp.result : null,
      });
      setSlashCommands(cacheSlashCommands(gid, nextSlashCommands));
      setGlobalSkillCommands(cacheGlobalSkillCommands(gid, buildCapabilityOverviewSkillSlashCommands({
        items: overviewResp.ok ? overviewResp.result?.items : [],
        reservedCommands: nextSlashCommands,
      })));
    }).catch(() => {
      if (!cancelled) {
        setSlashCommands(cachedSlashCommands(gid));
        setGlobalSkillCommands(cachedGlobalSkillCommands(gid));
      }
    });
    return () => {
      cancelled = true;
    };
  }, [selectedGid]);

  useEffect(() => {
    const handleFocus = () => {
      void refreshSlashCommands();
    };
    window.addEventListener("focus", handleFocus);
    return () => {
      window.removeEventListener("focus", handleFocus);
    };
  }, [refreshSlashCommands]);

  useEffect(() => {
    return subscribeCapabilityChanged(selectedGid, () => {
      void refreshSlashCommands();
    });
  }, [refreshSlashCommands, selectedGid]);

  return { slashCommands: visibleSlashCommands, globalSkillCommands: visibleGlobalSkillCommands, refreshSlashCommands };
}
