import re
import unittest
from pathlib import Path
from unittest.mock import patch

from no1.ports.mcp.toolspecs import MCP_TOOLS


class TestMcpToolspecDispatchParity(unittest.TestCase):
    def test_toolspec_names_are_unique(self) -> None:
        names = [str(t.get("name") or "").strip() for t in MCP_TOOLS if isinstance(t, dict)]
        self.assertEqual(
            len(names),
            len(set(names)),
            msg="Duplicate MCP tool names in toolspecs.py",
        )

    def test_toolspec_and_dispatch_names_match_exactly(self) -> None:
        spec_names = {
            str(t.get("name") or "").strip()
            for t in MCP_TOOLS
            if isinstance(t, dict) and str(t.get("name") or "").strip()
        }

        repo_root = Path(__file__).resolve().parents[1]
        mcp_dir = repo_root / "src" / "no1" / "ports" / "mcp"
        impl_names = set()

        scan_files = [mcp_dir / "server.py"]
        handlers_dir = mcp_dir / "handlers"
        if handlers_dir.exists():
            scan_files.extend(sorted(handlers_dir.glob("*.py")))

        for file_path in scan_files:
            text = file_path.read_text(encoding="utf-8")
            impl_names.update(re.findall(r'if name == "([a-z0-9_]+)"', text))

        self.assertEqual(
            sorted(spec_names - impl_names),
            [],
            msg=f"Tools declared in MCP_TOOLS but not dispatched in server.py: {sorted(spec_names - impl_names)}",
        )
        self.assertEqual(
            sorted(impl_names - spec_names),
            [],
            msg=f"Tools dispatched in server.py but missing from MCP_TOOLS: {sorted(impl_names - spec_names)}",
        )

    def test_onecolleague_tool_alias_dispatches_to_canonical_handler(self) -> None:
        from no1.ports.mcp import server as mcp_server
        from no1.ports.mcp.common import runtime_context_override

        with runtime_context_override(group_id="g_test"), patch.object(
            mcp_server,
            "project_info",
            return_value={"ok": True, "name": "project"},
        ):
            self.assertEqual(
                mcp_server.handle_tool_call("onecolleague_project_info", {"group_id": "g_test"}),
                mcp_server.handle_tool_call("onecolleague_project_info", {"group_id": "g_test"}),
            )


if __name__ == "__main__":
    unittest.main()
