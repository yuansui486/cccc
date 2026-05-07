import type { TFunction } from "i18next";

type UnknownRecord = Record<string, unknown>;

type SettingsErrorShape = {
  code?: unknown;
  message?: unknown;
  details?: unknown;
};

function asString(value: unknown): string {
  return typeof value === "string" ? value : value == null ? "" : String(value);
}

function asRecord(value: unknown): UnknownRecord | null {
  return value && typeof value === "object" ? (value as UnknownRecord) : null;
}

function deriveReasonFromMessage(message: string): string {
  const normalized = String(message || "").trim();
  if (!normalized) return "";
  if (normalized === "desktop pet requires a foreman actor") {
    return "desktop_pet_requires_foreman";
  }
  if (normalized === "failed to start pet actor" || normalized.startsWith("failed to start pet actor:")) {
    return "pet_actor_start_failed";
  }
  if (normalized.startsWith("pet start failed and rollback restart failed:")) {
    return "pet_actor_rollback_restart_failed";
  }
  return "";
}

function deriveCauseFromMessage(message: string, reason: string): string {
  const normalized = String(message || "").trim();
  if (!normalized || !reason) return "";
  if (reason === "pet_actor_start_failed" && normalized.startsWith("failed to start pet actor:")) {
    return normalized.split(":").slice(1).join(":").trim();
  }
  if (reason === "pet_actor_rollback_restart_failed" && normalized.startsWith("pet start failed and rollback restart failed:")) {
    return normalized.split(":").slice(1).join(":").trim();
  }
  return "";
}

export function formatGroupSettingsUpdateError(
  t: TFunction,
  error: SettingsErrorShape | null | undefined,
): string {
  const code = asString(error?.code).trim();
  const message = asString(error?.message).trim();
  const details = asRecord(error?.details);
  const reason = asString(details?.reason).trim() || deriveReasonFromMessage(message);
  const cause = asString(details?.cause).trim() || deriveCauseFromMessage(message, reason);

  if (code === "permission_denied") {
    return t("modals:context.settingsPermissionDenied", "You do not have permission to update these settings.");
  }
  if (code === "group_not_found") {
    return t("modals:context.settingsGroupNotFound", "Working group not found.");
  }
  if (code === "missing_group_id") {
    return t("modals:context.settingsMissingGroup", "Missing working group.");
  }
  if (code === "invalid_patch") {
    return t("modals:context.settingsInvalidPatch", "Invalid settings update payload.");
  }

  if (code === "group_settings_update_failed") {
    if (reason === "desktop_pet_requires_foreman") {
      return t("modals:context.desktopPetRequiresForeman", "Add a foreman actor before turning on Web Pet.");
    }
    if (reason === "pet_actor_start_failed") {
      return cause
        ? t("modals:context.desktopPetStartFailedWithCause", { defaultValue: "Failed to start the Web Pet runtime: {{cause}}", cause })
        : t("modals:context.desktopPetStartFailed", "Failed to start the Web Pet runtime.");
    }
    if (reason === "pet_actor_rollback_restart_failed") {
      return cause
        ? t("modals:context.desktopPetRollbackFailedWithCause", {
            defaultValue: "Web Pet startup failed, and rollback restart also failed: {{cause}}",
            cause,
          })
        : t("modals:context.desktopPetRollbackFailed", "Web Pet startup failed, and rollback restart also failed.");
    }
    return message
      ? t("modals:context.failedToUpdateSettingsWithCause", {
          defaultValue: "Failed to update group settings: {{cause}}",
          cause: message,
        })
      : t("modals:context.failedToUpdateSettings", "Failed to update group settings.");
  }

  if (code && message) return `${code}: ${message}`;
  return message || t("modals:context.failedToUpdateSettings", "Failed to update group settings.");
}
