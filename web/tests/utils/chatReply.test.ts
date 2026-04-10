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

  it("drops redundant wecom image placeholders when building a reply target", () => {
    const state = buildReplyComposerState(
      {
        id: "evt-image",
        kind: "chat.message",
        by: "user",
        data: {
          text: "[image]",
          source_platform: "wecom",
          attachments: [
            {
              kind: "image",
              path: "state/blobs/demo-image.png",
              title: "demo-image.png",
              mime_type: "image/png",
            },
          ],
          to: ["@foreman"],
        },
      } as any,
      "g-demo",
      [],
      null,
    );

    expect(state?.replyTarget.text).toBe("");
  });

  it("drops redundant wecom image placeholders for inbound media attachments without blob paths", () => {
    const state = buildReplyComposerState(
      {
        id: "evt-wecom-media",
        kind: "chat.message",
        by: "user",
        data: {
          text: "[image]",
          source_platform: "wecom",
          attachments: [
            {
              kind: "image",
              title: "wx-camera-shot",
              mime_type: "image/jpeg",
              download_url: "https://example.test/media/123",
              decryption_key: "aes-demo",
            },
          ],
        },
      } as any,
      "g-demo",
      [],
      null,
    );

    expect(state?.replyTarget.text).toBe("");
  });
});
