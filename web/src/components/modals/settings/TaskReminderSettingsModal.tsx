import { useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";

import { useModalA11y } from "../../../hooks/useModalA11y";
import { BellIcon } from "../../Icons";
import { BodyPortal } from "../../ui/BodyPortal";
import { NumberInputRow } from "./automationUtils";
import { PolicyGroup } from "./AutomationPoliciesSection";
import {
  primaryButtonClass,
  secondaryButtonClass,
  settingsDialogBodyClass,
  settingsDialogFooterClass,
  settingsDialogHeaderClass,
  settingsDialogPanelClass,
} from "./types";
import type { TaskReminderPolicyValues } from "./useAutomationPolicyDraft";

interface TaskReminderSettingsModalProps {
  open: boolean;
  isDark: boolean;
  busy: boolean;
  taskReminderEnabled: boolean;
  taskEmptyCooldownSeconds: number;
  setTaskEmptyCooldownSeconds: (value: number) => void;
  taskActiveOverdueMilestonesSeconds: number[];
  setTaskActiveOverdueMilestonesSeconds: (value: number[]) => void;
  taskPlannedUnassignedMilestonesSeconds: number[];
  setTaskPlannedUnassignedMilestonesSeconds: (value: number[]) => void;
  onClose: () => void;
  onReset: () => void;
  onSave: () => Promise<boolean>;
}

function readValues(props: TaskReminderSettingsModalProps): TaskReminderPolicyValues {
  return {
    task_reminder_enabled: props.taskReminderEnabled,
    task_empty_cooldown_seconds: props.taskEmptyCooldownSeconds,
    task_active_overdue_milestones_seconds: [...props.taskActiveOverdueMilestonesSeconds],
    task_planned_unassigned_milestones_seconds: [
      ...props.taskPlannedUnassignedMilestonesSeconds,
    ],
  };
}

function restoreValues(
  props: TaskReminderSettingsModalProps,
  values: TaskReminderPolicyValues,
) {
  props.setTaskEmptyCooldownSeconds(values.task_empty_cooldown_seconds);
  props.setTaskActiveOverdueMilestonesSeconds([
    ...values.task_active_overdue_milestones_seconds,
  ]);
  props.setTaskPlannedUnassignedMilestonesSeconds([
    ...values.task_planned_unassigned_milestones_seconds,
  ]);
}

export function TaskReminderSettingsModal(props: TaskReminderSettingsModalProps) {
  const { t } = useTranslation(["common", "settings"]);
  const openingValuesRef = useRef<TaskReminderPolicyValues | null>(null);

  useEffect(() => {
    if (props.open) openingValuesRef.current = readValues(props);
    // A new snapshot is intentionally taken only when the modal opens.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.open]);

  const isDirty = () =>
    JSON.stringify(readValues(props)) !== JSON.stringify(openingValuesRef.current);

  const close = () => {
    if (isDirty() && !window.confirm(t("settings:automation.unsavedChangesConfirm"))) return;
    if (openingValuesRef.current) restoreValues(props, openingValuesRef.current);
    props.onClose();
  };

  const { modalRef } = useModalA11y(props.open, close);

  if (!props.open) return null;

  const save = async () => {
    if (!(await props.onSave())) return;
    openingValuesRef.current = readValues(props);
    props.onClose();
  };

  const setActiveMilestone = (index: number, value: number) => {
    const next = [...props.taskActiveOverdueMilestonesSeconds];
    next[index] = value;
    props.setTaskActiveOverdueMilestonesSeconds(next);
  };

  const setPlannedMilestone = (index: number, value: number) => {
    const next = [...props.taskPlannedUnassignedMilestonesSeconds];
    next[index] = value;
    props.setTaskPlannedUnassignedMilestonesSeconds(next);
  };

  return (
    <BodyPortal>
      <div className="fixed inset-0 z-[1000]" role="dialog" aria-modal="true">
        <div className="absolute inset-0 glass-overlay" onPointerDown={close} />
        <div ref={modalRef} className={settingsDialogPanelClass("lg")}>
          <div className={settingsDialogHeaderClass}>
            <div className="flex min-w-0 items-start gap-3">
              <div className="mt-0.5 rounded-md border border-[var(--glass-accent-border)] bg-[var(--glass-accent-bg)] p-1.5 text-[var(--color-accent-primary)]">
                <BellIcon className="h-4 w-4" />
              </div>
              <div className="min-w-0">
                <h2 className="text-sm font-semibold text-[var(--color-text-primary)] sm:text-base">
                  {t("settings:policies.taskReminderSettings")}
                </h2>
                <p className="mt-1 text-xs leading-relaxed text-[var(--color-text-muted)]">
                  {t("settings:policies.taskReminderModalDescription")}
                </p>
              </div>
            </div>
            <button
              type="button"
              className={`${secondaryButtonClass("sm")} ml-auto shrink-0`}
              onClick={close}
            >
              {t("common:close")}
            </button>
          </div>

          <div className={settingsDialogBodyClass}>
            <div className="space-y-4">
              <PolicyGroup
                title={t("settings:policies.taskRuntimeAlerts")}
                description={t("settings:policies.taskRuntimeAlertsHelp")}
              >
                <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                  <NumberInputRow
                    isDark={props.isDark}
                    label={t("settings:policies.taskEmptyCooldown")}
                    value={props.taskEmptyCooldownSeconds}
                    onChange={props.setTaskEmptyCooldownSeconds}
                    helperText={t("settings:policies.taskEmptyCooldownHelp")}
                  />
                  {props.taskActiveOverdueMilestonesSeconds.map((value, index) => (
                    <NumberInputRow
                      key={`task-active-${index}`}
                      isDark={props.isDark}
                      label={t("settings:policies.taskActiveMilestone", { index: index + 1 })}
                      value={value}
                      onChange={(next) => setActiveMilestone(index, next)}
                      helperText={t("settings:policies.taskActiveMilestoneHelp")}
                    />
                  ))}
                </div>
              </PolicyGroup>

              <PolicyGroup
                title={t("settings:policies.taskPlannedAlerts")}
                description={t("settings:policies.taskPlannedAlertsHelp")}
              >
                <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
                  {props.taskPlannedUnassignedMilestonesSeconds.map((value, index) => (
                    <NumberInputRow
                      key={`task-planned-${index}`}
                      isDark={props.isDark}
                      label={t("settings:policies.taskPlannedMilestone", { index: index + 1 })}
                      value={value}
                      onChange={(next) => setPlannedMilestone(index, next)}
                      helperText={t("settings:policies.taskPlannedMilestoneHelp")}
                    />
                  ))}
                </div>
              </PolicyGroup>
            </div>
          </div>

          <div className={settingsDialogFooterClass}>
            <button
              type="button"
              className={secondaryButtonClass()}
              disabled={props.busy}
              onClick={props.onReset}
            >
              {t("settings:policies.resetTaskReminder")}
            </button>
            <button
              type="button"
              className={primaryButtonClass(props.busy)}
              disabled={props.busy}
              onClick={() => void save()}
            >
              {props.busy
                ? t("settings:automation.saving")
                : t("settings:policies.saveTaskReminder")}
            </button>
          </div>
        </div>
      </div>
    </BodyPortal>
  );
}
