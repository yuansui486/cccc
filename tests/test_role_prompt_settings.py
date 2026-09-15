import os
import tempfile
import unittest


class TestRolePromptSettings(unittest.TestCase):
    def setUp(self) -> None:
        self._old_home = os.environ.get("CCCC_HOME")
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["CCCC_HOME"] = self._tmp.name

    def tearDown(self) -> None:
        self._tmp.cleanup()
        if self._old_home is None:
            os.environ.pop("CCCC_HOME", None)
        else:
            os.environ["CCCC_HOME"] = self._old_home

    def _group(self):
        from no1.kernel.group import create_group
        from no1.kernel.registry import load_registry

        return create_group(load_registry(), title="role-prompt", topic="")

    def test_settings_default_and_bounds(self) -> None:
        from no1.contracts.v1 import DaemonRequest
        from no1.daemon.group.group_settings_ops import _settings_payload
        from no1.daemon.server import handle_request
        from no1.kernel.group import load_group

        group = self._group()
        self.assertEqual(_settings_payload(group)["role_prompt_every_messages"], 5)

        response, _ = handle_request(
            DaemonRequest.model_validate(
                {
                    "op": "group_settings_update",
                    "args": {
                        "group_id": group.group_id,
                        "patch": {"role_prompt_every_messages": 0},
                        "by": "user",
                    },
                }
            )
        )
        self.assertTrue(response.ok, getattr(response, "error", None))
        loaded = load_group(group.group_id)
        assert loaded is not None
        self.assertEqual(_settings_payload(loaded)["role_prompt_every_messages"], 0)

        response, _ = handle_request(
            DaemonRequest.model_validate(
                {
                    "op": "group_settings_update",
                    "args": {
                        "group_id": group.group_id,
                        "patch": {"role_prompt_every_messages": 1001},
                        "by": "user",
                    },
                }
            )
        )
        self.assertFalse(response.ok)
        self.assertEqual(getattr(response.error, "code", ""), "invalid_patch")

    def test_template_round_trip_preserves_setting(self) -> None:
        from no1.daemon.ops.template_ops import _apply_settings_replace
        from no1.kernel.group import load_group
        from no1.kernel.group_template import (
            build_group_template_from_group,
            dump_group_template,
            parse_group_template,
        )

        group = self._group()
        group.doc["messaging"] = {"role_prompt_every_messages": 37}
        group.save()
        loaded = load_group(group.group_id)
        assert loaded is not None

        template = build_group_template_from_group(loaded)
        self.assertEqual(template.settings.role_prompt_every_messages, 37)
        parsed = parse_group_template(dump_group_template(template))
        _apply_settings_replace(loaded, parsed.settings.model_dump())
        self.assertEqual(loaded.doc["messaging"]["role_prompt_every_messages"], 37)


if __name__ == "__main__":
    unittest.main()
