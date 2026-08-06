from __future__ import annotations

import unittest

from no1.kernel.capabilities import (
    BUILTIN_CAPABILITY_PACKS,
    CAPABILITY_ADMIN_TOOLS,
    CORE_ADMIN_TOOLS,
    CORE_BASIC_TOOLS,
    CORE_TOOL_NAMES,
    SPECIALIZED_CORE_TOOL_NAMES,
    WEB_MODEL_CORE_TOOLS,
)
from no1.ports.mcp.toolspecs import MCP_TOOLS


class TestMcpCapabilitySurface(unittest.TestCase):
    def test_core_and_pack_coverage_matches_toolspecs(self) -> None:
        names = {str(t.get("name") or "").strip() for t in MCP_TOOLS if isinstance(t, dict)}
        core = {str(x) for x in CORE_TOOL_NAMES}
        pack_union = {
            str(tool_name)
            for pack in BUILTIN_CAPABILITY_PACKS.values()
            for tool_name in (pack.get("tool_names") or ())
        }
        specialized_core = {str(x) for x in SPECIALIZED_CORE_TOOL_NAMES}

        self.assertTrue(core.issubset(names), msg=f"core tools missing: {sorted(core - names)}")
        self.assertTrue(pack_union.issubset(names), msg=f"pack tools missing: {sorted(pack_union - names)}")
        self.assertTrue(
            specialized_core.issubset(names),
            msg=f"specialized core tools missing: {sorted(specialized_core - names)}",
        )

        missing_mapping = sorted(names - core - pack_union - specialized_core)
        self.assertEqual(
            missing_mapping,
            [],
            msg=f"tools missing from capability surface model: {missing_mapping}",
        )

    def test_core_surface_budget_is_small(self) -> None:
        total = len(MCP_TOOLS)
        core = len(CORE_TOOL_NAMES)
        self.assertEqual(core, 14, msg=f"unexpected lean core size: core={core}, total={total}")

    def test_capability_runtime_tools_are_core_and_admin_tools_are_packaged(self) -> None:
        core = set(CORE_TOOL_NAMES)
        basic = set(CORE_BASIC_TOOLS)
        admin = set(CORE_ADMIN_TOOLS)
        capability_admin_pack = set(BUILTIN_CAPABILITY_PACKS["pack:capability-admin"]["tool_names"])

        self.assertIn("onecolleague_capability_search", core)
        self.assertIn("onecolleague_capability_use", core)
        self.assertIn("onecolleague_capability_use", basic)
        self.assertEqual(admin, set())
        self.assertEqual(
            capability_admin_pack,
            {
                "onecolleague_capability_state",
                "onecolleague_capability_enable",
                "onecolleague_capability_install",
                *CAPABILITY_ADMIN_TOOLS,
            },
        )
        self.assertNotIn("onecolleague_capability_state", core)
        self.assertNotIn("onecolleague_capability_enable", core)
        self.assertNotIn("onecolleague_capability_install", core)
        self.assertNotIn("onecolleague_capability_block", core)
        self.assertNotIn("onecolleague_capability_import", core)
        self.assertNotIn("onecolleague_capability_uninstall", core)
        self.assertIn("onecolleague_agent_state", core)
        self.assertIn("onecolleague_coordination", core)
        self.assertIn("onecolleague_task", core)
        self.assertIn("onecolleague_experience", core)
        self.assertNotIn("onecolleague_memory", core)
        self.assertNotIn("onecolleague_coordination", BUILTIN_CAPABILITY_PACKS["pack:context-advanced"]["tool_names"])
        self.assertNotIn("onecolleague_task", BUILTIN_CAPABILITY_PACKS["pack:context-advanced"]["tool_names"])
        self.assertIn("onecolleague_memory", BUILTIN_CAPABILITY_PACKS["pack:context-advanced"]["tool_names"])

    def test_tools_removed_from_lean_core_remain_reachable_through_existing_packs(self) -> None:
        moved = {
            "onecolleague_project_info",
            "onecolleague_capability_state",
            "onecolleague_capability_enable",
            "onecolleague_capability_install",
            "onecolleague_tracked_send",
            "onecolleague_repo",
            "onecolleague_presentation",
            "onecolleague_memory",
        }
        packaged = {
            str(tool_name)
            for pack in BUILTIN_CAPABILITY_PACKS.values()
            for tool_name in (pack.get("tool_names") or ())
        }

        self.assertFalse(moved & set(CORE_BASIC_TOOLS))
        self.assertTrue(moved <= packaged)

    def test_web_model_keeps_existing_fixed_schema_fallbacks(self) -> None:
        fixed_schema_fallbacks = {
            "onecolleague_project_info",
            "onecolleague_capability_state",
            "onecolleague_tracked_send",
            "onecolleague_repo",
            "onecolleague_presentation",
            "onecolleague_memory",
        }

        self.assertFalse(fixed_schema_fallbacks & set(CORE_BASIC_TOOLS))
        self.assertTrue(fixed_schema_fallbacks <= set(WEB_MODEL_CORE_TOOLS))


if __name__ == "__main__":
    unittest.main()
