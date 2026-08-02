import { describe, expect, it } from "vitest";
import { latestSuggestedUserMessage, normalizeSuggestedUserMessage } from "./suggestedUserMessage";

describe("suggested user message", () => {
  it("only exposes the latest actor message addressed to the user", () => {
    expect(latestSuggestedUserMessage([
      { id: "a", kind: "chat.message", by: "actor", data: { to: ["user"], suggested_user_message: "  Next step? " } },
    ])).toMatchObject({ eventId: "a", text: "Next step?" });
    expect(latestSuggestedUserMessage([
      { id: "a", kind: "chat.message", by: "actor", data: { to: ["user"], suggested_user_message: "Next" } },
      { id: "u", kind: "chat.message", by: "user", data: { text: "done" } },
    ])).toBeNull();
  });

  it("bounds untrusted suggestion length", () => {
    expect(normalizeSuggestedUserMessage("x".repeat(5000))).toHaveLength(4000);
  });
});
