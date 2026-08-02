import type { Actor } from "../../types";

export type ComposerMentionKind = "actor" | "group";

export type ComposerMentionSuggestion = {
  kind: ComposerMentionKind;
  token: string;
  label: string;
  secondary?: string;
};

export function buildComposerMentionSuggestions(
  recipientActors: Actor[],
  groupLabelById: Record<string, string>,
): ComposerMentionSuggestion[] {
  const actorSuggestions: ComposerMentionSuggestion[] = [
    { kind: "actor", token: "@all", label: "@all" },
    ...(recipientActors || []).flatMap((actor): ComposerMentionSuggestion[] => {
      const token = String(actor.id || "").trim();
      if (!token) return [];
      const label = String(actor.title || "").trim() || token;
      return [{
        kind: "actor",
        token,
        label,
        secondary: label !== token ? token : undefined,
      }];
    }),
  ];
  const groupSuggestions: ComposerMentionSuggestion[] = Object.entries(groupLabelById || {})
    .flatMap(([token, rawLabel]): ComposerMentionSuggestion[] => {
      const id = String(token || "").trim();
      if (!id) return [];
      const label = String(rawLabel || "").trim() || id;
      return [{
        kind: "group",
        token: id,
        label,
        secondary: label !== id ? id : undefined,
      }];
    });
  return [...actorSuggestions, ...groupSuggestions];
}

export function filterComposerMentionSuggestions(
  suggestions: ComposerMentionSuggestion[],
  kind: ComposerMentionKind,
  filter: string,
): ComposerMentionSuggestion[] {
  const needle = String(filter || "").trim().toLowerCase();
  return suggestions.filter((item) => {
    if (item.kind !== kind) return false;
    if (!needle) return true;
    return [item.token, item.label, item.secondary]
      .filter(Boolean)
      .some((value) => String(value).toLowerCase().includes(needle));
  });
}
