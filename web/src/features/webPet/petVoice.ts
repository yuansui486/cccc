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
      "{{petName}}把 {{actor}} 的回复草稿叼到你手边了。",
      { petName, actor: actorLabel },
    );
  }
  return tr(
    "reminderSummary.mentionVoice",
    "{{petName}}留意到 {{actor}} 刚准备好一条草稿。",
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
    return tr("hint.voiceReminder", "{{petName}}轻轻戳你一下：{{summary}}", {
      petName,
      summary: trimmedSummary,
    });
  }
  if (status === "loading") {
    return tr("hint.voiceLoading", "{{petName}}正在整理现场…", { petName });
  }
  if (status === "error") {
    return tr("hint.voiceUnavailable", "{{petName}}暂时没拿到最新线索。", { petName });
  }
  if (status === "progress" && typeof done === "number" && typeof total === "number") {
    return tr("hint.voiceProgress", "{{petName}}守着进度：{{done}}/{{total}} 已完成", {
      petName,
      done,
      total,
    });
  }
  if (clean(fallback)) {
    return tr("hint.voiceIdle", "{{petName}}正在旁边看着：{{fallback}}", {
      petName,
      fallback: clean(fallback),
    });
  }
  return tr("hint.voiceResting", "{{petName}}在旁边安静看着。", { petName });
}
