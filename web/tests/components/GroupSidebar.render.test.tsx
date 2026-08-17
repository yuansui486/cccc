import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { GroupSidebar, type GroupSidebarProps } from "../../src/components/layout/GroupSidebar";

vi.mock("react-i18next", () => ({
  initReactI18next: { type: "3rdParty", init: () => undefined },
  useTranslation: () => ({
    t: (key: string) => ({
      teamMembersSection: "团队成员",
      groupChat: "群聊天",
      addAgent: "添加智能体",
      "actorStatus.initializing": "初始化中",
      "actorStatus.failed": "启动失败",
      "actorStatus.working": "工作中",
      "actorStatus.run": "运行",
      "actorStatus.stop": "停止",
    } as Record<string, string>)[key] ?? key,
  }),
}));

const noop = () => undefined;

const baseProps: GroupSidebarProps = {
  orderedGroups: [{ group_id: "group-1", title: "测试团队" }],
  archivedGroupIds: [],
  selectedGroupId: "group-1",
  actors: [{ id: "actor-1", title: "成员一", enabled: false, running: false }],
  activeTab: "chat",
  unreadChatCount: 0,
  theme: "light",
  textScale: 100,
  computerControlAvailability: { supported: false, platform: "test", reason: "test" },
  groupDoc: { group_id: "group-1", title: "测试团队" },
  selectedGroupRunning: false,
  selectedGroupRuntimeStatus: null,
  busy: "",
  sseStatus: "connected",
  isOpen: true,
  isCollapsed: false,
  sidebarWidth: 320,
  isDark: false,
  onThemeChange: noop,
  onTextScaleChange: noop,
  onSelectGroup: noop,
  onWarmGroup: noop,
  onCreateGroup: noop,
  onClose: noop,
  onToggleCollapse: noop,
  onResizeWidth: noop,
  onReorderSection: noop,
  onArchiveGroup: noop,
  onRestoreGroup: noop,
  onTabChange: noop,
  onAddAgent: noop,
  onOpenContext: noop,
  onOpenContextProject: noop,
  onOpenSkillManagement: noop,
  onOpenScheduledReminder: noop,
  onOpenRemoteLink: noop,
  onOpenExperience: noop,
  onOpenSettings: noop,
  onOpenGroupEdit: noop,
  onStartGroup: noop,
  onStopGroup: noop,
  onSetGroupState: noop,
};

describe("GroupSidebar team members navigation", () => {
  it("combines chat and actors under one team members section", () => {
    const html = renderToStaticMarkup(<GroupSidebar {...baseProps} />);

    expect(html.match(/团队成员/g)).toHaveLength(1);
    expect(html).toContain("群聊天");
    expect(html).toContain("成员一");
    expect(html).toContain('aria-label="添加智能体"');
    expect(html).not.toContain("成员管理");
  });

  it("hides the add button for read-only sidebars", () => {
    const html = renderToStaticMarkup(<GroupSidebar {...baseProps} readOnly />);

    expect(html).not.toContain('aria-label="添加智能体"');
  });
});
