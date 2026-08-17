import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  mergeCanonicalAttachmentsWithOptimisticPreview,
  useChatOutboxStore,
} from "../../src/stores/chatOutboxStore";
import type { LedgerEvent } from "../../src/types";

describe("chat outbox canonical reconciliation", () => {
  beforeEach(() => {
    useChatOutboxStore.getState().clearAll();
    vi.stubGlobal("URL", {
      ...URL,
      revokeObjectURL: vi.fn(),
    });
  });

  it("transfers the optimistic blob preview into the canonical event", () => {
    const optimistic: LedgerEvent = {
      id: "local-1",
      kind: "chat.message",
      by: "user",
      group_id: "g1",
      data: {
        text: "image",
        client_id: "local-1",
        attachments: [{ title: "image.png", local_preview_url: "blob:preview-1" }],
      },
    };
    useChatOutboxStore.getState().enqueue("g1", "local-1", optimistic);

    const canonical: LedgerEvent = {
      id: "evt-1",
      kind: "chat.message",
      by: "user",
      group_id: "g1",
      data: {
        text: "image",
        client_id: "local-1",
        attachments: [{ title: "image.png", path: "uploads/image.png" }],
      },
    };
    const result = mergeCanonicalAttachmentsWithOptimisticPreview(canonical, "g1");
    expect((result.event.data as { attachments: Array<{ local_preview_url?: string }> }).attachments[0]?.local_preview_url)
      .toBe("blob:preview-1");
    expect(result.transferredPreviewUrls).toEqual(["blob:preview-1"]);
  });
});
