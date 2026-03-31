import { beforeEach, describe, expect, it, vi } from "vitest";

function makeStorage() {
  const data = new Map<string, string>();
  return {
    getItem: vi.fn((key: string) => data.get(key) ?? null),
    setItem: vi.fn((key: string, value: string) => {
      data.set(key, String(value));
    }),
    removeItem: vi.fn((key: string) => {
      data.delete(key);
    }),
    clear: vi.fn(() => {
      data.clear();
    }),
  };
}

const localStorageMock = makeStorage();
vi.stubGlobal("localStorage", localStorageMock);

describe("stagePetReminderDraft", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.clearAllMocks();
    localStorageMock.clear();
  });

  it("hydrates an empty composer with PET draft routing", async () => {
    const { useComposerStore } = await import("../../stores/useComposerStore");
    const { useGroupStore } = await import("../../stores/useGroupStore");
    const { useUIStore } = await import("../../stores/useUIStore");
    const { stagePetReminderDraft } = await import("./petSuggestionDraft");

    useGroupStore.setState({
      selectedGroupId: "g-1",
      events: [
        {
          id: "evt-1",
          kind: "chat.message",
          by: "user",
          data: { text: "original message" },
        },
      ],
      chatByGroup: {
        "g-1": {
          events: [
            {
              id: "evt-1",
              kind: "chat.message",
              by: "user",
              data: { text: "original message" },
            },
          ],
          chatWindow: null,
          hasMoreHistory: false,
          hasLoadedTail: true,
          isLoadingHistory: false,
          isChatWindowLoading: false,
        },
      },
    });
    useUIStore.setState({ activeTab: "developer", chatSessions: {} });

    const staged = stagePetReminderDraft({
      id: "d1",
      kind: "suggestion",
      priority: 80,
      summary: "reply to user",
      agent: "pet-peer",
      source: { eventId: "evt-1", suggestionKind: "reply_required" },
      fingerprint: "fp-1",
      action: {
        type: "draft_message",
        groupId: "g-1",
        text: "I will follow up on this.",
        to: ["@foreman"],
        replyTo: "evt-1",
      },
    });

    expect(staged).toBe(true);
    expect(useUIStore.getState().activeTab).toBe("chat");
    expect(useUIStore.getState().chatSessions["g-1"]?.mobileSurface).toBe("messages");
    expect(useComposerStore.getState().destGroupId).toBe("g-1");
    expect(useComposerStore.getState().composerText).toBe("I will follow up on this.");
    expect(useComposerStore.getState().toText).toBe("@foreman");
    expect(useComposerStore.getState().replyTarget).toEqual({
      eventId: "evt-1",
      by: "user",
      text: "original message",
    });
  });

  it("appends into an existing draft without clobbering current routing", async () => {
    const { useComposerStore } = await import("../../stores/useComposerStore");
    const { useGroupStore } = await import("../../stores/useGroupStore");
    const { stagePetReminderDraft } = await import("./petSuggestionDraft");

    useGroupStore.setState({ selectedGroupId: "g-1" });
    useComposerStore.setState({
      composerText: "Existing draft",
      toText: "peer-1",
      replyTarget: {
        eventId: "evt-existing",
        by: "peer-1",
        text: "current thread",
      },
      destGroupId: "g-1",
    });

    const staged = stagePetReminderDraft({
      id: "d2",
      kind: "suggestion",
      priority: 80,
      summary: "reply to user",
      agent: "pet-peer",
      source: { eventId: "evt-2", suggestionKind: "reply_required" },
      fingerprint: "fp-2",
      action: {
        type: "draft_message",
        groupId: "g-1",
        text: "Add PET suggestion",
        to: ["@foreman"],
        replyTo: "evt-2",
      },
    });

    expect(staged).toBe(true);
    expect(useComposerStore.getState().composerText).toBe("Existing draft\n\nAdd PET suggestion");
    expect(useComposerStore.getState().toText).toBe("peer-1");
    expect(useComposerStore.getState().replyTarget).toEqual({
      eventId: "evt-existing",
      by: "peer-1",
      text: "current thread",
    });
  });

  it("stages task proposals into the normal composer instead of sending directly", async () => {
    const { useComposerStore } = await import("../../stores/useComposerStore");
    const { useGroupStore } = await import("../../stores/useGroupStore");
    const { useUIStore } = await import("../../stores/useUIStore");
    const { stagePetReminderDraft } = await import("./petSuggestionDraft");

    useGroupStore.setState({
      selectedGroupId: "g-2",
      events: [],
      chatByGroup: {},
    });
    useUIStore.setState({ activeTab: "developer", chatSessions: {} });
    useComposerStore.setState({
      composerText: "",
      toText: "",
      replyTarget: null,
      destGroupId: "",
    });

    const staged = stagePetReminderDraft({
      id: "tp-1",
      kind: "suggestion",
      priority: 85,
      summary: "建议让 foreman 把 T315 推进到 active。",
      agent: "pet-peer",
      source: { taskId: "T315" },
      fingerprint: "fp-task-1",
      action: {
        type: "task_proposal",
        groupId: "g-2",
        operation: "move",
        taskId: "T315",
        status: "active",
      },
    });

    expect(staged).toBe(true);
    expect(useUIStore.getState().activeTab).toBe("chat");
    expect(useComposerStore.getState().destGroupId).toBe("g-2");
    expect(useComposerStore.getState().toText).toBe("@foreman");
    expect(useComposerStore.getState().replyTarget).toBe(null);
    expect(useComposerStore.getState().composerText).toBe(
      "Pet task proposal: please use cccc_task to move this task (task_id=T315, status=active).",
    );
  });

  it("preserves cross-group pet drafts by prewriting the target group draft before switching", async () => {
    const { useComposerStore } = await import("../../stores/useComposerStore");
    const { useGroupStore } = await import("../../stores/useGroupStore");
    const { useUIStore } = await import("../../stores/useUIStore");
    const { stagePetReminderDraft } = await import("./petSuggestionDraft");

    useGroupStore.setState({
      selectedGroupId: "g-1",
      events: [],
      chatByGroup: {
        "g-2": {
          events: [
            {
              id: "evt-2",
              kind: "chat.message",
              by: "user",
              data: { text: "target message" },
            },
          ],
          chatWindow: null,
          hasMoreHistory: false,
          hasLoadedTail: true,
          isLoadingHistory: false,
          isChatWindowLoading: false,
        },
      },
    });
    useUIStore.setState({ activeTab: "developer", chatSessions: {} });
    useComposerStore.setState({
      composerText: "old group draft",
      toText: "",
      replyTarget: null,
      destGroupId: "g-1",
    });

    const staged = stagePetReminderDraft({
      id: "d3",
      kind: "suggestion",
      priority: 80,
      summary: "reply across groups",
      agent: "pet-peer",
      source: { eventId: "evt-2", suggestionKind: "reply_required" },
      fingerprint: "fp-3",
      action: {
        type: "draft_message",
        groupId: "g-2",
        text: "Cross-group PET suggestion",
        to: ["@foreman"],
        replyTo: "evt-2",
      },
    });

    expect(staged).toBe(true);
    expect(useGroupStore.getState().selectedGroupId).toBe("g-2");
    useComposerStore.getState().switchGroup("g-1", "g-2");
    expect(useComposerStore.getState().composerText).toBe("Cross-group PET suggestion");
    expect(useComposerStore.getState().toText).toBe("@foreman");
    expect(useComposerStore.getState().replyTarget).toEqual({
      eventId: "evt-2",
      by: "user",
      text: "target message",
    });
  });
});
