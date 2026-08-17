import { describe, expect, it, vi } from "vitest";

const apiMocks = vi.hoisted(() => ({
  sendMessage: vi.fn(),
  replyMessage: vi.fn(),
}));

vi.mock("../../src/services/api", () => apiMocks);

import { sendSlashSkillMessageRequest } from "../../src/hooks/useSlashSkillDispatch";

describe("sendSlashSkillMessageRequest", () => {
  it("uses the reply API when slash skill dispatch has a reply target", async () => {
    apiMocks.replyMessage.mockResolvedValueOnce({ ok: true, result: {} });

    await expect(sendSlashSkillMessageRequest({
      selectedGroupId: "g1",
      message: "请使用已激活的 /using-superpowers skill 完成以下任务：\n\n开始执行",
      toTokens: ["@all"],
      priority: "attention",
      replyRequired: true,
      collaborationRequired: false,
      localId: "local-1",
      replyTarget: {
        eventId: "evt-original",
        by: "foreman",
        text: "失败日志",
      },
      skillCapabilityId: "skill:library:copywriting",
    })).resolves.toEqual({ ok: true, result: {} });

    expect(apiMocks.replyMessage).toHaveBeenCalledWith(
      "g1",
      "请使用已激活的 /using-superpowers skill 完成以下任务：\n\n开始执行",
      ["@all"],
      "evt-original",
      undefined,
      "attention",
      true,
      false,
      "local-1",
      [],
      "skill:library:copywriting",
    );
    expect(apiMocks.sendMessage).not.toHaveBeenCalled();
  });

  it("uses the normal send API when slash skill dispatch has no reply target", async () => {
    apiMocks.sendMessage.mockResolvedValueOnce({ ok: true, result: {} });

    await expect(sendSlashSkillMessageRequest({
      selectedGroupId: "g1",
      message: "请使用已激活的 /using-superpowers skill 完成以下任务：\n\n开始执行",
      toTokens: ["@all"],
      priority: "normal",
      replyRequired: false,
      collaborationRequired: false,
      localId: "local-2",
      replyTarget: null,
      skillCapabilityId: "skill:library:copywriting",
    })).resolves.toEqual({ ok: true, result: {} });

    expect(apiMocks.sendMessage).toHaveBeenCalledWith(
      "g1",
      "请使用已激活的 /using-superpowers skill 完成以下任务：\n\n开始执行",
      ["@all"],
      undefined,
      "normal",
      false,
      false,
      "local-2",
      [],
      undefined,
      "skill:library:copywriting",
    );
  });
});
