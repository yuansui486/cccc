from __future__ import annotations

import unittest
from argparse import Namespace
from types import SimpleNamespace
from unittest.mock import patch


class TestCliOpenClaw(unittest.TestCase):
    def test_parser_accepts_actor_model_options(self) -> None:
        from no1.cli.main import build_parser

        add_args = build_parser().parse_args(["actor", "add", "peer", "--runtime", "openclaw", "--model", "provider/model"])
        update_args = build_parser().parse_args(["actor", "update", "peer", "--model", "provider/other"])

        self.assertEqual(add_args.model, "provider/model")
        self.assertEqual(update_args.model, "provider/other")

    def test_openclaw_models_uses_selected_profile(self) -> None:
        from no1 import cli

        args = Namespace(openclaw_action="models", profile="work", dev=False, refresh=True)
        catalog = [{"key": "provider/model", "available": True}]
        with patch("no1.daemon.openclaw_runtime.list_openclaw_models", return_value=catalog) as list_models, patch.object(
            cli, "_print_json"
        ) as print_json:
            code = cli.cmd_runtime_openclaw(args)

        self.assertEqual(code, 0)
        list_models.assert_called_once()
        call = list_models.call_args
        self.assertEqual(call.args[0], ["openclaw", "--profile", "work"])
        self.assertTrue(call.kwargs["refresh"])
        print_json.assert_called_once_with({"ok": True, "result": {"models": catalog}})

    def test_openclaw_models_reports_discovery_failure(self) -> None:
        from no1 import cli

        args = Namespace(openclaw_action="models", profile="", dev=True, refresh=False)
        with patch(
            "no1.daemon.openclaw_runtime.list_openclaw_models",
            side_effect=RuntimeError("catalog unavailable"),
        ), patch.object(cli, "_print_json") as print_json:
            code = cli.cmd_runtime_openclaw(args)

        self.assertEqual(code, 2)
        self.assertEqual(print_json.call_args.args[0]["error"]["code"], "openclaw_model_discovery_failed")

    def test_actor_add_sends_selected_model_as_runtime_options(self) -> None:
        from no1 import cli

        args = Namespace(
            group="group-a",
            actor_id="peer1",
            title="Peer 1",
            by="user",
            submit="enter",
            runner="pty",
            runtime="openclaw",
            model="provider/model",
            command="",
            env=[],
            scope="",
        )
        response = {"ok": True, "result": {}}
        with patch.object(cli, "load_group", return_value=SimpleNamespace(doc={})), patch.object(
            cli, "_ensure_daemon_running", return_value=True
        ), patch.object(cli, "call_daemon", return_value=response) as call_daemon, patch.object(cli, "_print_json"):
            code = cli.cmd_actor_add(args)

        self.assertEqual(code, 0)
        request = call_daemon.call_args.args[0]
        self.assertEqual(request["args"]["runtime_options"], {"selected_model": "provider/model"})

    def test_actor_update_model_preserves_other_runtime_options(self) -> None:
        from no1 import cli

        args = Namespace(
            group="group-a",
            actor_id="peer1",
            by="user",
            title=None,
            role=None,
            command=None,
            env=[],
            scope="",
            submit=None,
            runner=None,
            runtime=None,
            model="provider/model",
            runtime_state_source=None,
            enabled=None,
        )
        group = SimpleNamespace(doc={})
        response = {"ok": True, "result": {}}
        actor = {"runtime_options": {"opencode": {"default_variant": "max"}}}
        with patch.object(cli, "load_group", return_value=group), patch(
            "no1.kernel.actors.find_actor", return_value=actor
        ), patch.object(cli, "_ensure_daemon_running", return_value=True), patch.object(
            cli, "call_daemon", return_value=response
        ) as call_daemon, patch.object(cli, "_print_json"):
            code = cli.cmd_actor_update(args)

        self.assertEqual(code, 0)
        patch_payload = call_daemon.call_args.args[0]["args"]["patch"]
        self.assertEqual(
            patch_payload["runtime_options"],
            {"opencode": {"default_variant": "max"}, "selected_model": "provider/model"},
        )


if __name__ == "__main__":
    unittest.main()
