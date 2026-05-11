import type { PetCompanionProfile, PetReminder } from "./types";

function clean(value: string | null | undefined): string {
  return String(value || "").trim();
}

export function getPetVoiceName(companion: PetCompanionProfile | null | undefined): string {
  return clean(companion?.name) || "Momo";
}

export function buildPetVoiceReminderSummary(input: {
  companion: PetCompanionProfile | null | undefined;
  reminder: PetReminder;
  actorLabel: string;
  tr: (key: string, fallback: string, vars?: Record<string, unknown>) => string;
}): string {
  const { companion, reminder, actorLabel, tr } = input;
  const petName = getPetVoiceName(companion);
  if (reminder.source.suggestionKind === "reply_required") {
    return tr(
      "reminderSummary.replyRequiredVoice",
      "{{petName}} has a reply draft from {{actor}} ready for you.",
      { petName, actor: actorLabel },
    );
  }
  return tr(
    "reminderSummary.mentionVoice",
    "{{petName}} noticed that {{actor}} has a draft ready.",
    { petName, actor: actorLabel },
  );
}

export function buildPetVoiceHint(input: {
  companion: PetCompanionProfile | null | undefined;
  summary?: string | null;
  status?: "loading" | "error" | "progress" | "idle";
  tr: (key: string, fallback: string, vars?: Record<string, unknown>) => string;
  done?: number;
  total?: number;
  fallback?: string;
}): string {
  const { companion, summary, status, tr, done, total, fallback } = input;
  const petName = getPetVoiceName(companion);
  const trimmedSummary = clean(summary);
  if (trimmedSummary) {
    return tr("hint.voiceReminder", "{{petName}} has a reminder: {{summary}}", {
      petName,
      summary: trimmedSummary,
    });
  }
  if (status === "loading") {
    return tr("hint.voiceLoading", "{{petName}} is checking the current context...", { petName });
  }
  if (status === "error") {
    return tr("hint.voiceUnavailable", "{{petName}} could not load the latest context.", { petName });
  }
  if (status === "progress" && typeof done === "number" && typeof total === "number") {
    return tr("hint.voiceProgress", "{{petName}} is tracking progress: {{done}}/{{total}} done", {
      petName,
      done,
      total,
    });
  }
  if (clean(fallback)) {
    return tr("hint.voiceIdle", "{{petName}} is watching: {{fallback}}", {
      petName,
      fallback: clean(fallback),
    });
  }
  return tr("hint.voiceResting", "{{petName}} is standing by.", { petName });
}
