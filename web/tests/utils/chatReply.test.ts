import { describe, expect, it } from "vitest";

import { buildReplyComposerState } from "../../src/utils/chatReply";

describe("buildReplyComposerState", () => {
  it("prefers existing quote_text over message text when building a reply target", () => {
    const state = buildReplyComposerState(
      {
        id: "evt-1",
        kind: "chat.message",
        by: "user",
        data: {
          text: "测试activity消息抖动",
          quote_text: "为什么activity 会出现再消失，当前抖动太严重了",
          to: ["reviewer"],
        },
      } as any,
      "g-demo",
      [],
      null,
    );

    expect(state?.replyTarget.text).toBe("为什么activity 会出现再消失，当前抖动太严重了");
  });
});
