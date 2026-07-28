import unittest
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import no1.computer_control.observation as observation_module
from no1.computer_control.lease import ComputerControlLease
from no1.computer_control.elements import normalize_snapshot
from no1.computer_control.observation import NativeWindowObserver, desktop_session_available, merge_observations, window_names_match
from no1.computer_control.recording import RecordingStore
from no1.computer_control.requests import ComputerRequestStore
from no1.computer_control.storage import WorkflowStore
from no1.kernel.group import create_group
from no1.kernel.registry import load_registry


class TestComputerControlObservation(unittest.TestCase):
    def test_desktop_session_probe_closes_the_input_desktop(self) -> None:
        calls = []

        class FakeFunction:
            def __init__(self, value, name):
                self.value = value
                self.name = name
                self.restype = None
                self.argtypes = None

            def __call__(self, *args):
                calls.append((self.name, args))
                return self.value

        user32 = SimpleNamespace(
            OpenInputDesktop=FakeFunction(123, "open"),
            SwitchDesktop=FakeFunction(1, "switch"),
            CloseDesktop=FakeFunction(1, "close"),
        )
        with patch.object(observation_module.sys, "platform", "win32"), patch.object(
            observation_module.ctypes,
            "windll",
            SimpleNamespace(user32=user32),
        ):
            self.assertTrue(desktop_session_available())

        self.assertEqual([name for name, _args in calls], ["open", "switch", "close"])

    def test_window_name_matching_accepts_decorated_titles(self) -> None:
        self.assertTrue(window_names_match("订单", "订单 - 企业系统"))
        self.assertTrue(window_names_match("WeChat", "WeChat"))
        self.assertFalse(window_names_match("订单", "库存"))
        self.assertFalse(window_names_match("App", "Other App"))

    def test_native_fallback_replaces_only_target_window_elements(self) -> None:
        primary = {
            "provider": "windows_mcp",
            "focused_window": "订单",
            "elements": [
                {"window_name": "Desktop", "name": "回收站", "mcp_label": 0},
                {"window_name": "订单", "name": "旧提交", "mcp_label": 1},
            ],
            "count": 2,
            "window_element_counts": {"Desktop": 1, "订单": 1},
            "diagnostics": {"code": "snapshot_window_context_invalid"},
            "warnings": [],
        }
        fallback = {
            "provider": "native_uia",
            "target_window": "订单",
            "elements": [{"window_name": "订单", "name": "提交", "control_type": "Button"}],
            "count": 1,
            "diagnostics": {"code": "ok"},
            "warnings": [],
        }
        merged = merge_observations(primary, fallback)
        self.assertTrue(merged["fallback_used"])
        self.assertEqual(merged["provider"], "windows_mcp+native_uia")
        self.assertEqual([item["name"] for item in merged["elements"]], ["回收站", "提交"])
        self.assertNotIn("mcp_label", merged["elements"][1])
        self.assertEqual(merged["target_window_element_count"], 1)

    def test_native_observer_reports_platform_unavailable_without_side_effects(self) -> None:
        with patch("no1.computer_control.observation.sys.platform", "linux"):
            result = NativeWindowObserver().observe(target_window="订单")
        self.assertEqual(result["count"], 0)
        self.assertEqual(result["diagnostics"]["code"], "native_uia_unavailable")

    def test_comtypes_rectangle_is_xy_width_height(self) -> None:
        class Element:
            def GetCurrentPropertyValue(self, property_id):
                return [348, 705, 524, 79] if property_id == 30001 else None

        self.assertEqual(
            NativeWindowObserver._bounds(Element()),
            {"x": 348.0, "y": 705.0, "width": 524.0, "height": 79.0},
        )

    def test_image_artifact_is_not_counted_as_a_ui_element(self) -> None:
        result = normalize_snapshot({"content": [{"type": "image_artifact", "path": "artifacts/evidence.png"}]})
        self.assertEqual(result["count"], 0)

    def test_recording_uses_ephemeral_integer_label_but_does_not_persist_it(self) -> None:
        tree = (
            "Focused Window:\nName\n---------\n订单  0  Normal  800 600 123\n\n"
            "UI Tree:\n"
            'desktop "Desktop 1"\n'
            '└── window "订单"\n'
            '    └── (100,200) Edit "输入框" [action: type]\n'
        )

        class Session:
            transport_restarts = 0

            def __init__(self) -> None:
                self.calls = []

            def catalog_sync(self):
                return [
                    {"name": "Snapshot", "inputSchema": {"type": "object"}},
                    {
                        "name": "Type",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"label": {"type": "integer"}, "loc": {"type": "array"}, "text": {"type": "string"}},
                            "required": ["text"],
                        },
                    },
                ]

            def call_tool_sync(self, name, arguments, *, timeout):
                self.calls.append((name, dict(arguments)))
                if name == "Snapshot":
                    return {"content": [{"type": "text", "text": tree}]}
                return {"content": [{"type": "text", "text": "typed"}]}

        class Provider:
            def __init__(self):
                self.foreground = "订单"

            @staticmethod
            def enhance_sync(snapshot, _locator):
                return snapshot

            def current_foreground_window(self):
                return self.foreground

        with tempfile.TemporaryDirectory() as td, patch.dict("os.environ", {"CCCC_HOME": td}):
            group = create_group(load_registry(), title="record-label")
            store = WorkflowStore(Path(td))
            requests = ComputerRequestStore(store)
            requests.append(
                group.group_id,
                {
                    "request_id": "req-label",
                    "actor_id": "foreman",
                    "mode": "create_and_run",
                    "status": "accepted",
                    "created_ts": time.time(),
                    "allow_high_risk": True,
                },
            )
            session = Session()
            provider = Provider()
            recordings = RecordingStore(
                Path(td),
                store,
                requests,
                ComputerControlLease(Path(td)),
                session,
                observation_provider=provider,
            )
            recording = recordings.start(group.group_id, actor_id="foreman", request_id="req-label", name="label")
            result = recordings.call(
                group.group_id,
                recording["recording_id"],
                actor_id="foreman",
                tool="Type",
                arguments={"label": "输入框", "text": "你好"},
                target={
                    "window_name": "订单",
                    "control_type": "Edit",
                    "name": "输入框",
                    "match": "exact",
                    "fallback_policy": "never",
                },
                record=True,
            )
            type_call = next(arguments for name, arguments in session.calls if name == "Type")
            self.assertEqual(type_call["label"], 0)
            self.assertIsInstance(result, dict)
            step = recordings.get(group.group_id, recording["recording_id"])["steps"][0]
            self.assertNotIn("label", step["arguments"])
            self.assertEqual(step["arguments"]["text"], "你好")
            self.assertEqual(step["target"]["name"], "输入框")

            recordings.call(
                group.group_id,
                recording["recording_id"],
                actor_id="foreman",
                tool="Type",
                arguments={"label": 0, "text": "再见"},
                record=True,
            )
            integer_label_step = recordings.get(group.group_id, recording["recording_id"])["steps"][1]
            self.assertNotIn("label", integer_label_step["arguments"])
            self.assertEqual(integer_label_step["target"]["name"], "输入框")

            with self.assertRaisesRegex(ValueError, "显式启用"):
                recordings.call(
                    group.group_id,
                    recording["recording_id"],
                    actor_id="foreman",
                    tool="Type",
                    arguments={"loc": [700, 500], "text": "不会发送"},
                    record=True,
                )

            recordings.call(
                group.group_id,
                recording["recording_id"],
                actor_id="foreman",
                tool="Type",
                arguments={"loc": [700, 500], "text": "显式位置兜底", "coordinate_fallback": True},
                record=True,
            )
            coordinate_step = recordings.get(group.group_id, recording["recording_id"])["steps"][2]
            self.assertEqual(coordinate_step["arguments"]["loc"], [700, 500])
            self.assertTrue(coordinate_step["arguments"]["coordinate_fallback"])
            self.assertIsNone(coordinate_step["target"])

            recordings.call(
                group.group_id,
                recording["recording_id"],
                actor_id="foreman",
                tool="Type",
                arguments={"loc": [700, 500], "text": "目标锚点兜底"},
                target={
                    "window_name": "订单",
                    "control_type": "Edit",
                    "name": "动态输入框",
                    "match": "exact",
                    "position_anchor": {"x": 700, "y": 500},
                    "fallback_policy": "controlled",
                },
                record=True,
            )
            anchored_step = recordings.get(group.group_id, recording["recording_id"])["steps"][3]
            self.assertNotIn("loc", anchored_step["arguments"])
            self.assertNotIn("x", anchored_step["arguments"])
            self.assertNotIn("y", anchored_step["arguments"])
            self.assertEqual(anchored_step["target"]["position_anchor"], {"x": 700, "y": 500})
            self.assertEqual(anchored_step["stability"], "low")

            type_call_count = sum(1 for name, _arguments in session.calls if name == "Type")
            provider.foreground = ""
            with self.assertRaisesRegex(ValueError, "无法确认"):
                recordings.call(
                    group.group_id,
                    recording["recording_id"],
                    actor_id="foreman",
                    tool="Type",
                    arguments={"label": 0, "text": "不会误发"},
                    record=True,
                )
            self.assertEqual(sum(1 for name, _arguments in session.calls if name == "Type"), type_call_count)


if __name__ == "__main__":
    unittest.main()
