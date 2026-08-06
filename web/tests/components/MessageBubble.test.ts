import { describe, expect, it } from "vitest";
import {
  getActorAccentBorderClass,
  getMessageBubbleMotionClass as getMotionClass,
  mayContainMarkdown as mayRenderMarkdown,
} from "../../src/components/messageBubble/helpers";
import { getActorAccentColor } from "../../src/types";
import { hasRenderableAttachmentSource } from "../../src/utils/messageAttachments";

describe("getMessageBubbleMotionClass", () => {
  it("animates commentary bubbles with the transient commentary class", () => {
    expect(getMotionClass({
      isStreaming: true,
      isOptimistic: false,
      streamPhase: "commentary",
    })).toBe("onecolleague-transient-bubble onecolleague-transient-bubble-commentary");
  });

  it("animates optimistic bubbles with the base transient class", () => {
    expect(getMotionClass({
      isStreaming: false,
      isOptimistic: true,
      streamPhase: "",
    })).toBe("onecolleague-transient-bubble");
  });

  it("keeps stable final bubbles animation-free", () => {
    expect(getMotionClass({
      isStreaming: false,
      isOptimistic: false,
      streamPhase: "final_answer",
    })).toBe("");
  });
});

describe("actor message accents", () => {
  it.each([
    ["text-sky-300", "border-l-sky-400/80"],
    ["text-indigo-300", "border-l-indigo-400/80"],
    ["text-violet-300", "border-l-violet-400/80"],
    ["text-fuchsia-300", "border-l-fuchsia-400/80"],
    ["text-cyan-300", "border-l-cyan-400/80"],
    ["text-teal-300", "border-l-teal-400/80"],
    ["text-emerald-300", "border-l-emerald-400/80"],
    ["text-amber-300", "border-l-amber-400/80"],
    ["text-sky-700", "border-l-sky-500"],
    ["text-indigo-700", "border-l-indigo-500"],
    ["text-violet-700", "border-l-violet-500"],
    ["text-fuchsia-700", "border-l-fuchsia-500"],
    ["text-cyan-700", "border-l-cyan-500"],
    ["text-teal-700", "border-l-teal-500"],
    ["text-emerald-700", "border-l-emerald-500"],
    ["text-amber-700", "border-l-amber-500"],
  ])("maps %s to %s", (accentClass, borderClass) => {
    expect(getActorAccentBorderClass(accentClass)).toBe(borderClass);
  });

  it("keeps an actor stable while distinguishing different actors", () => {
    const codexOneAccent = getActorAccentColor("codex-1", true);
    const repeatedAccent = getActorAccentColor("codex-1", true);
    const codexTwoAccent = getActorAccentColor("codex-2", true);

    expect(codexOneAccent).toEqual(repeatedAccent);
    expect(getActorAccentBorderClass(codexOneAccent.text)).not.toBe(
      getActorAccentBorderClass(codexTwoAccent.text),
    );
  });

  it("uses a neutral border when no actor accent is available", () => {
    expect(getActorAccentBorderClass()).toBe("border-l-[var(--glass-border-subtle)]");
    expect(getActorAccentBorderClass("unknown-accent")).toBe("border-l-[var(--glass-border-subtle)]");
  });
});

describe("mayContainMarkdown", () => {
  it("detects GitHub-style tables so completed chat bubbles render markdown tables", () => {
    expect(mayRenderMarkdown([
      "本周天气如下：",
      "",
      "| 日期 | 天气 | 温度 |",
      "| --- | --- | --- |",
      "| 周一 | 晴 | 24°C |",
      "| 周二 | 多云 | 22°C |",
    ].join("\n"))).toBe(true);
  });

  it("keeps internal attachment manifests as plain text", () => {
    expect(mayRenderMarkdown("[onecolleague] Attachments:\n- file.txt")).toBe(false);
  });
});

describe("WeCom attachment rendering", () => {
  it("keeps download-url images renderable even without blob paths", () => {
    const attachment = {
      kind: "image",
      title: "wechat-shot",
      mime_type: "image/jpeg",
      download_url: "https://example.test/media/123",
      decryption_key: "aes-demo",
    };

    expect(hasRenderableAttachmentSource(attachment as any)).toBe(true);
  });
});
