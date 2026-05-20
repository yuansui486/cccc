import type { SlashCommandItem } from "./slashCommands";

export const BUILTIN_SLASH_COMMANDS: SlashCommandItem[] = [
  {
    name: "install",
    command: "/install",
    description: "Install or enable a skill, MCP toolpack, capability, URL, repo, or local path.",
    capabilityId: "skill:onecolleague:install",
    sourceType: "builtin_command",
  },
];
