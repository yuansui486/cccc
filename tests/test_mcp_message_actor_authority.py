import ast
import unittest
from pathlib import Path


class TestMCPMessageActorAuthority(unittest.TestCase):
    def _issue(self, **overrides):
        from no1.ports.mcp.actor_authority import _issue_actor_message_authority

        values = {
            "group_id": "group-1",
            "actor_id": "actor-1",
            "source": "local_mcp",
            "tool_name": "onecolleague_message_send",
            "nonce": "nonce-1",
        }
        values.update(overrides)
        return _issue_actor_message_authority(**values)

    def _consume(self, value, **overrides):
        from no1.ports.mcp.actor_authority import consume_actor_message_authority

        values = {
            "group_id": "group-1",
            "actor_id": "actor-1",
            "tool_names": {"onecolleague_message_send"},
        }
        values.update(overrides)
        return consume_actor_message_authority(value, **values)

    def test_valid_authority_is_consumed_once(self) -> None:
        from no1.ports.mcp.common import MCPError

        authority = self._issue()
        self.assertIs(self._consume(authority), authority)
        with self.assertRaises(MCPError):
            self._consume(authority)

    def test_directly_constructed_authority_is_rejected(self) -> None:
        from no1.ports.mcp.actor_authority import ActorMessageAuthority
        from no1.ports.mcp.common import MCPError

        forged = ActorMessageAuthority(
            "group-1",
            "actor-1",
            "local_mcp",
            "onecolleague_message_send",
            "nonce-1",
            object(),
        )
        with self.assertRaises(MCPError):
            self._consume(forged)

    def test_group_actor_and_tool_mismatch_each_invalidate_authority(self) -> None:
        from no1.ports.mcp.common import MCPError

        mismatches = (
            {"group_id": "group-2"},
            {"actor_id": "actor-2"},
            {"tool_names": {"onecolleague_message_reply"}},
        )
        for mismatch in mismatches:
            with self.subTest(mismatch=mismatch):
                authority = self._issue()
                with self.assertRaises(MCPError):
                    self._consume(authority, **mismatch)
                with self.assertRaises(MCPError):
                    self._consume(authority)

    def test_failed_next_issue_invalidates_old_authority(self) -> None:
        from no1.ports.mcp.common import MCPError

        authority = self._issue()
        with self.assertRaises(MCPError):
            self._issue(actor_id="")
        with self.assertRaises(MCPError):
            self._consume(authority)

    def test_server_route_is_the_only_production_issuer_caller(self) -> None:
        root = Path(__file__).resolve().parents[1]
        callers = []
        for path in (root / "src" / "no1").rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    if node.func.id == "_issue_actor_message_authority":
                        callers.append((path.relative_to(root).as_posix(), node.lineno))
        self.assertEqual(len(callers), 1)
        self.assertEqual(callers[0][0], "src/no1/ports/mcp/server.py")


if __name__ == "__main__":
    unittest.main()
