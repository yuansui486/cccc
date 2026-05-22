import { useCallback, useEffect, useMemo, useState } from "react";

import * as api from "../services/api";
import {
  buildCapabilityOverviewSkillSlashCommands,
  buildSlashCommands,
  type SlashCommandItem,
} from "../utils/slashCommands";
import { subscribeCapabilityChanged } from "../utils/capabilityEvents";

const slashCommandCache = new Map<string, SlashCommandItem[]>();
const teamSkillCommandCache = new Map<string, SlashCommandItem[]>();
const globalSkillCommandCache = new Map<string, SlashCommandItem[]>();

function cachedSlashCommands(groupId: string): SlashCommandItem[] {
  return slashCommandCache.get(groupId) || buildSlashCommands({ state: null });
}

function cacheSlashCommands(groupId: string, commands: SlashCommandItem[]): SlashCommandItem[] {
  slashCommandCache.set(groupId, commands);
  return commands;
}

function cachedTeamSkillCommands(groupId: string): SlashCommandItem[] {
  return teamSkillCommandCache.get(groupId) || [];
}

function cacheTeamSkillCommands(groupId: string, commands: SlashCommandItem[]): SlashCommandItem[] {
  teamSkillCommandCache.set(groupId, commands);
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
  const [teamSkillCommands, setTeamSkillCommands] = useState<SlashCommandItem[]>(() => {
    return selectedGid ? cachedTeamSkillCommands(selectedGid) : [];
  });
  const [globalSkillCommands, setGlobalSkillCommands] = useState<SlashCommandItem[]>(() => {
    return selectedGid ? cachedGlobalSkillCommands(selectedGid) : [];
  });

  const buildTeamSkillCommands = useCallback((
    stateResp: Awaited<ReturnType<typeof api.fetchGroupCapabilityState>>,
    overviewResp: Awaited<ReturnType<typeof api.fetchCapabilityOverview>>,
    reservedCommands: SlashCommandItem[],
  ) => {
    const enabledSet = new Set<string>();
    if (stateResp.ok && stateResp.result) {
      const enabled = Array.isArray(stateResp.result.enabled) ? stateResp.result.enabled : [];
      for (const row of enabled) {
        if (String(row.scope || "").trim().toLowerCase() !== "group") continue;
        const capabilityId = String(row.capability_id || "").trim();
        if (capabilityId) enabledSet.add(capabilityId);
      }
    }
    return buildCapabilityOverviewSkillSlashCommands({
      items: overviewResp.ok ? overviewResp.result?.items : [],
      reservedCommands,
      includeCapabilityIds: enabledSet,
      active: true,
    });
  }, []);

  const buildGlobalSkillCommands = useCallback((
    overviewResp: Awaited<ReturnType<typeof api.fetchCapabilityOverview>>,
    reservedCommands: SlashCommandItem[],
  ) => {
    return buildCapabilityOverviewSkillSlashCommands({
      items: overviewResp.ok ? overviewResp.result?.items : [],
      reservedCommands,
      active: false,
    });
  }, []);

  const refreshSlashCommands = useCallback(async () => {
    const gid = selectedGid;
    if (!gid) return;
    try {
      const [stateResp, overviewResp] = await Promise.all([
        api.fetchGroupCapabilityState(gid, "user", { noCache: true }),
        api.fetchCapabilityOverview({ includeIndexed: true, limit: 1200, kind: "skill" }),
      ]);
      const nextSlashCommands = buildSlashCommands({
        state: stateResp.ok ? stateResp.result : null,
      });
      setSlashCommands(cacheSlashCommands(gid, nextSlashCommands));
      setTeamSkillCommands(cacheTeamSkillCommands(gid, buildTeamSkillCommands(stateResp, overviewResp, nextSlashCommands)));
      if (overviewResp.ok) {
        setGlobalSkillCommands(cacheGlobalSkillCommands(gid, buildGlobalSkillCommands(overviewResp, nextSlashCommands)));
      }
    } catch {
      setSlashCommands(cachedSlashCommands(gid));
      setTeamSkillCommands(cachedTeamSkillCommands(gid));
      setGlobalSkillCommands(cachedGlobalSkillCommands(gid));
    }
  }, [buildGlobalSkillCommands, buildTeamSkillCommands, selectedGid]);

  const visibleSlashCommands = selectedGid ? slashCommands : [];
  const visibleTeamSkillCommands = selectedGid ? teamSkillCommands : [];
  const visibleGlobalSkillCommands = selectedGid ? globalSkillCommands : [];

  useEffect(() => {
    let cancelled = false;
    const gid = selectedGid;
    if (!gid) return;
    queueMicrotask(() => {
      if (!cancelled) {
        setSlashCommands(cachedSlashCommands(gid));
        setTeamSkillCommands(cachedTeamSkillCommands(gid));
        setGlobalSkillCommands(cachedGlobalSkillCommands(gid));
      }
    });
    void Promise.all([
      api.fetchGroupCapabilityState(gid, "user"),
      api.fetchCapabilityOverview({ includeIndexed: true, limit: 1200, kind: "skill" }),
    ]).then(([stateResp, overviewResp]) => {
      if (cancelled) return;
      const nextSlashCommands = buildSlashCommands({
        state: stateResp.ok ? stateResp.result : null,
      });
      setSlashCommands(cacheSlashCommands(gid, nextSlashCommands));
      setTeamSkillCommands(cacheTeamSkillCommands(gid, buildTeamSkillCommands(stateResp, overviewResp, nextSlashCommands)));
      setGlobalSkillCommands(cacheGlobalSkillCommands(gid, buildGlobalSkillCommands(overviewResp, nextSlashCommands)));
    }).catch(() => {
      if (!cancelled) {
        setSlashCommands(cachedSlashCommands(gid));
        setTeamSkillCommands(cachedTeamSkillCommands(gid));
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

  return {
    slashCommands: visibleSlashCommands,
    teamSkillCommands: visibleTeamSkillCommands,
    globalSkillCommands: visibleGlobalSkillCommands,
    refreshSlashCommands,
  };
}
