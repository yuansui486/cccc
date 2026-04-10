import { describe, expect, it } from "vitest";

import {
  deriveStreamingRenderPhase,
  getEffectiveStreamingActivities,
  getMessageBubbleMotionClass,
  getStreamingPendingDelayMs,
  getStreamingPlaceholderText,
  isQueuedOnlyStreamingPlaceholder,
  mayContainMarkdown,
  shouldReserveStreamingStatusSpace,
  shouldRenderStreamingStatusPanel,
} from "../../src/components/messageBubble/helpers";

describe("isQueuedOnlyStreamingPlaceholder", () => {
  it("returns true for pure queued placeholder without live text", () => {
    expect(isQueuedOnlyStreamingPlaceholder({
      isStreaming: true,
      messageText: "",
      liveStreamingText: "",
      blobAttachmentCount: 0,
      presentationRefCount: 0,
      activities: [{ id: "queued:1", kind: "queued", status: "started", summary: "queued" }],
    })).toBe(true);
  });

  it("returns false once live streaming text exists", () => {
    expect(isQueuedOnlyStreamingPlaceholder({
      isStreaming: true,
      messageText: "",
      liveStreamingText: "我先查一下",
      blobAttachmentCount: 0,
      presentationRefCount: 0,
      activities: [{ id: "queued:1", kind: "queued", status: "started", summary: "queued" }],
    })).toBe(false);
  });

  it("returns false once live activities are no longer queued-only", () => {
    expect(isQueuedOnlyStreamingPlaceholder({
      isStreaming: true,
      messageText: "",
      liveStreamingText: "",
      blobAttachmentCount: 0,
      presentationRefCount: 0,
      activities: [{ id: "reply:1", kind: "reply", status: "started", summary: "replying" }],
    })).toBe(false);
  });

  it("keeps queued placeholder label compact and stable", () => {
    expect(getStreamingPlaceholderText({
      isQueuedOnlyPlaceholder: true,
      placeholderLabel: "Working...",
    })).toBe("queued");
  });
});

describe("getEffectiveStreamingActivities", () => {
  it("keeps a stream-scoped activity timeline isolated from sibling streams", () => {
    const activities = getEffectiveStreamingActivities({
      streamId: "stream-commentary",
      actorId: "coder",
      pendingEventId: "evt-1",
      bucket: {
        streamingActivitiesByStreamId: {
          "stream-commentary": [{ id: "a1", kind: "tool", status: "started", summary: "search docs" }],
          "stream-final": [{ id: "a2", kind: "tool", status: "started", summary: "patch ui" }],
        },
        streamingEvents: [
          {
            id: "stream:commentary",
            ts: "2026-04-04T16:00:00.000Z",
            kind: "chat.message",
            by: "coder",
            data: {
              stream_id: "stream-commentary",
              pending_event_id: "evt-1",
            },
          },
          {
            id: "stream:final",
            ts: "2026-04-04T16:00:02.000Z",
            kind: "chat.message",
            by: "coder",
            data: {
              stream_id: "stream-final",
              pending_event_id: "evt-1",
            },
          },
        ],
      },
      fallbackActivities: [],
    });

    expect(activities).toHaveLength(1);
    expect(activities[0]?.summary).toBe("search docs");
  });

  it("falls back to the latest pending-slot activity batch only when there is no stream id", () => {
    const activities = getEffectiveStreamingActivities({
      streamId: "",
      actorId: "coder",
      pendingEventId: "evt-1",
      bucket: {
        streamingActivitiesByStreamId: {
          "stream-commentary": [{ id: "a1", kind: "tool", status: "started", summary: "search docs" }],
          "stream-final": [{ id: "a2", kind: "tool", status: "started", summary: "patch ui" }],
        },
        streamingEvents: [
          {
            id: "stream:commentary",
            ts: "2026-04-04T16:00:00.000Z",
            kind: "chat.message",
            by: "coder",
            data: {
              stream_id: "stream-commentary",
              pending_event_id: "evt-1",
            },
          },
          {
            id: "stream:final",
            ts: "2026-04-04T16:00:02.000Z",
            kind: "chat.message",
            by: "coder",
            data: {
              stream_id: "stream-final",
              pending_event_id: "evt-1",
            },
          },
        ],
      },
      fallbackActivities: [],
    });

    expect(activities).toHaveLength(1);
    expect(activities[0]?.summary).toBe("patch ui");
  });

  it("drops queued placeholder once real activity timeline arrives", () => {
    const activities = getEffectiveStreamingActivities({
      streamId: "stream-final",
      actorId: "coder",
      pendingEventId: "evt-1",
      bucket: {
        streamingActivitiesByStreamId: {
          "stream-final": [
            { id: "queued:1", kind: "queued", status: "started", summary: "queued" },
            { id: "a2", kind: "tool", status: "started", summary: "search docs" },
          ],
        },
        streamingEvents: [],
      },
      fallbackActivities: [],
    });

    expect(activities).toEqual([
      { id: "a2", kind: "tool", status: "started", summary: "search docs", detail: undefined, ts: undefined },
    ]);
  });
});

describe("getMessageBubbleMotionClass", () => {
  it("animates commentary bubbles with the transient commentary class", () => {
    expect(getMessageBubbleMotionClass({
      isStreaming: true,
      isOptimistic: false,
      streamPhase: "commentary",
    })).toBe("cccc-transient-bubble cccc-transient-bubble-commentary");
  });

  it("animates optimistic bubbles with the base transient class", () => {
    expect(getMessageBubbleMotionClass({
      isStreaming: false,
      isOptimistic: true,
      streamPhase: "",
    })).toBe("cccc-transient-bubble");
  });

  it("keeps stable final bubbles animation-free", () => {
    expect(getMessageBubbleMotionClass({
      isStreaming: false,
      isOptimistic: false,
      streamPhase: "final_answer",
    })).toBe("");
  });
});

describe("mayContainMarkdown", () => {
  it("detects GitHub-style tables so completed chat bubbles render markdown tables", () => {
    expect(mayContainMarkdown([
      "本周天气如下：",
      "",
      "| 日期 | 天气 | 温度 |",
      "| --- | --- | --- |",
      "| 周一 | 晴 | 24°C |",
      "| 周二 | 多云 | 22°C |",
    ].join("\n"))).toBe(true);
  });

  it("keeps internal attachment manifests as plain text", () => {
    expect(mayContainMarkdown("[cccc] Attachments:\n- file.txt")).toBe(false);
  });
});

describe("shouldRenderStreamingStatusPanel", () => {
  it("keeps the status panel visible while streaming even if text already exists", () => {
    expect(shouldRenderStreamingStatusPanel({
      isStreaming: true,
      hasText: true,
      activities: [],
    })).toBe(true);
  });

  it("renders the status panel while activities exist", () => {
    expect(shouldRenderStreamingStatusPanel({
      isStreaming: false,
      hasText: true,
      activities: [{ id: "a1", kind: "tool", status: "started", summary: "search docs" }],
    })).toBe(true);
  });

  it("renders the placeholder panel when there is no text yet", () => {
    expect(shouldRenderStreamingStatusPanel({
      isStreaming: false,
      hasText: false,
      activities: [],
    })).toBe(true);
  });

  it("hides the status panel once text exists and activities are empty", () => {
    expect(shouldRenderStreamingStatusPanel({
      isStreaming: false,
      hasText: true,
      activities: [],
    })).toBe(false);
  });
});

describe("shouldReserveStreamingStatusSpace", () => {
  it("keeps a reserved status row while streaming is pending", () => {
    expect(shouldReserveStreamingStatusSpace({
      isStreaming: true,
      renderPhase: "pending",
    })).toBe(true);
  });

  it("keeps a reserved status row while streaming is exiting", () => {
    expect(shouldReserveStreamingStatusSpace({
      isStreaming: false,
      renderPhase: "exiting",
    })).toBe(true);
  });

  it("releases the reserved status row once streaming completes", () => {
    expect(shouldReserveStreamingStatusSpace({
      isStreaming: false,
      renderPhase: "completed",
    })).toBe(false);
  });
});

describe("deriveStreamingRenderPhase", () => {
  it("starts in pending while streaming has no text or activities", () => {
    expect(deriveStreamingRenderPhase({
      isStreaming: true,
      hasText: false,
      activities: [],
    })).toBe("pending");
  });

  it("switches to active once streaming activities arrive", () => {
    expect(deriveStreamingRenderPhase({
      isStreaming: true,
      hasText: false,
      activities: [{ id: "a1", kind: "tool", status: "started", summary: "search docs" }],
    })).toBe("active");
  });

  it("does not fall back to pending after becoming active", () => {
    expect(deriveStreamingRenderPhase({
      isStreaming: true,
      hasText: false,
      activities: [],
      previousPhase: "active",
    })).toBe("active");
  });

  it("becomes completed once streaming ends", () => {
    expect(deriveStreamingRenderPhase({
      isStreaming: false,
      hasText: true,
      activities: [],
      previousPhase: "active",
    })).toBe("completed");
  });
});

describe("getStreamingPendingDelayMs", () => {
  it("caps the pending hold to a short residual delay", () => {
    expect(getStreamingPendingDelayMs(1_000, 1_000)).toBe(80);
    expect(getStreamingPendingDelayMs(1_000, 1_040)).toBe(40);
    expect(getStreamingPendingDelayMs(1_000, 1_120)).toBe(0);
  });

  it("returns zero when there is no pending start time", () => {
    expect(getStreamingPendingDelayMs(null, 1_000)).toBe(0);
  });
});
