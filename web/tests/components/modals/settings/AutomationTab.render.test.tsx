import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { AutomationTab } from "../../../../src/components/modals/settings/AutomationTab";
import enSettings from "../../../../src/i18n/locales/en/settings.json";
import jaSettings from "../../../../src/i18n/locales/ja/settings.json";
import zhSettings from "../../../../src/i18n/locales/zh/settings.json";

vi.mock("react-i18next", async (importOriginal) => ({
  ...(await importOriginal<typeof import("react-i18next")>()),
  useTranslation: () => ({ t: (key: string) => key }),
}));

describe("AutomationTab policy navigation", () => {
  it("keeps policy details out of the main page and orders the sections", () => {
    const noop = () => undefined;
    const save = async () => true;
    const html = renderToStaticMarkup(
      <AutomationTab
        isDark={false}
        groupId="g-demo"
        hideHeader
        devActors={[]}
        busy={false}
        nudgeSeconds={300}
        setNudgeSeconds={noop}
        replyRequiredNudgeSeconds={300}
        setReplyRequiredNudgeSeconds={noop}
        attentionAckNudgeSeconds={600}
        setAttentionAckNudgeSeconds={noop}
        unreadNudgeSeconds={900}
        setUnreadNudgeSeconds={noop}
        nudgeDigestMinIntervalSeconds={120}
        setNudgeDigestMinIntervalSeconds={noop}
        nudgeMaxRepeatsPerObligation={3}
        setNudgeMaxRepeatsPerObligation={noop}
        nudgeEscalateAfterRepeats={2}
        setNudgeEscalateAfterRepeats={noop}
        idleSeconds={0}
        setIdleSeconds={noop}
        keepaliveSeconds={120}
        setKeepaliveSeconds={noop}
        keepaliveMax={3}
        setKeepaliveMax={noop}
        silenceSeconds={0}
        setSilenceSeconds={noop}
        helpNudgeIntervalSeconds={600}
        setHelpNudgeIntervalSeconds={noop}
        helpNudgeMinMessages={10}
        setHelpNudgeMinMessages={noop}
        taskReminderEnabled
        setTaskReminderEnabled={noop}
        taskEmptyCooldownSeconds={900}
        setTaskEmptyCooldownSeconds={noop}
        taskActiveOverdueMilestonesSeconds={[1800, 3000, 3600, 5400]}
        setTaskActiveOverdueMilestonesSeconds={noop}
        taskPlannedUnassignedMilestonesSeconds={[900, 1800, 3600, 7200, 10800, 21600]}
        setTaskPlannedUnassignedMilestonesSeconds={noop}
        onSaveBuiltinPolicies={save}
        onSaveTaskReminderSettings={save}
        onSaveTaskReminderEnabled={async () => true}
        onResetBuiltinPolicies={noop}
        onResetTaskReminder={noop}
      />,
    );

    const rulesIndex = html.indexOf("automation.rulesTitle");
    const reminderIndex = html.indexOf("policies.taskRuntimeAlerts");
    const builtinIndex = html.indexOf("policies.builtinSettings");

    expect(rulesIndex).toBeGreaterThanOrEqual(0);
    expect(reminderIndex).toBeGreaterThan(rulesIndex);
    expect(builtinIndex).toBeGreaterThan(reminderIndex);
    expect(html).not.toContain("policies.needReplyFollowup");
    expect(html).not.toContain("policies.taskEmptyCooldown");
  });

  it("defines the secondary navigation copy in every settings locale", () => {
    const requiredKeys = [
      "compactDescription",
      "builtinSettings",
      "builtinSummary",
      "modalDescription",
      "openSettings",
      "taskReminderSummary",
      "taskReminderSettings",
      "taskReminderModalDescription",
      "taskReminderStatus",
      "statusEnabled",
      "statusDisabled",
      "saveTaskReminder",
      "resetTaskReminder",
    ] as const;

    for (const locale of [zhSettings, enSettings, jaSettings]) {
      expect(locale.automation.unsavedChangesConfirm).toBeTruthy();
      for (const key of requiredKeys) expect(locale.policies[key]).toBeTruthy();
    }
  });
});
