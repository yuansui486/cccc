import { fetchTerminalTail } from "../../services/api";
import type { ApiResponse } from "../../services/api/base";

type PetTailIssue = "auth_expired" | null;
const PET_ACTOR_ID = "pet-peer";

export type PetManualReviewDiagnosis =
  | { kind: "no_reminders" }
  | { kind: "runtime_unavailable" }
  | { kind: "runtime_not_running" }
  | { kind: "runtime_auth_expired" };

export function classifyPetRuntimeTail(text: string): PetTailIssue {
  const value = String(text || "").trim();
  if (!value) return null;
  if (
    /please run\s+\/login/i.test(value) ||
    /oauth token has expired/i.test(value) ||
    /authentication[_ ]error/i.test(value) ||
    /api error:\s*401/i.test(value) ||
    /\bunauthorized\b/i.test(value)
  ) {
    return "auth_expired";
  }
  return null;
}

export function classifyPetManualReviewResponse(
  response: ApiResponse<{ text: string; hint: string }>,
): PetManualReviewDiagnosis {
  if (!response.ok) {
    const code = String(response.error?.code || "").trim().toLowerCase();
    if (code === "actor_not_found") {
      return { kind: "runtime_unavailable" };
    }
    if (code === "actor_not_running") {
      return { kind: "runtime_not_running" };
    }
    return { kind: "no_reminders" };
  }

  if (classifyPetRuntimeTail(response.result.text || "") === "auth_expired") {
    return { kind: "runtime_auth_expired" };
  }

  return { kind: "no_reminders" };
}

export async function diagnosePetManualReview(
  groupId: string,
): Promise<PetManualReviewDiagnosis> {
  const tailResp = await fetchTerminalTail(groupId, PET_ACTOR_ID, 4000, true, true);
  return classifyPetManualReviewResponse(tailResp);
}
