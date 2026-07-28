import asyncio
import tempfile
import unittest
from pathlib import Path

from no1.computer_control.elements import normalize_snapshot, resolve_locator
from no1.computer_control.mcp import ComputerControlArgumentError, validate_arguments_against_schema
from no1.computer_control.models import ElementLocator, WorkflowNode
from no1.computer_control.runtime import ElementResolutionError, WorkflowRunner


class TestComputerControlRpaContract(unittest.TestCase):
    def test_ui_tree_and_nested_json_are_normalized_with_ephemeral_labels(self):
        tree = (
            'Focused Window:\nName\n---------\n订单  1  Normal  100 100 1\n\n'
            '├── (10,20) Button "提交" [action: click]\n'
            '├── (30,40) Edit "数量" [action: type] [value: 2]'
        )
        result = normalize_snapshot({"structuredContent": {"content": [{"type": "text", "text": tree}]}})
        self.assertEqual(result["count"], 2)
        self.assertEqual([item["mcp_label"] for item in result["elements"]], [0, 1])
        self.assertEqual(result["focused_window"], "订单")
        self.assertEqual(resolve_locator(result, {"name": "提交", "control_type": "Button"})["status"], "unique")

    def test_locator_does_not_persist_snapshot_label_and_defaults_element_only(self):
        locator = ElementLocator(name="提交", control_type="Button")
        self.assertEqual(locator.fallback_policy, "never")
        self.assertFalse("mcp_label" in locator.model_dump())

    def test_invalid_name_in_loc_is_rejected_before_rpc(self):
        with self.assertRaises(ComputerControlArgumentError):
            validate_arguments_against_schema("Click", {"loc": "提交"}, {"type": "object"})
        with self.assertRaises(ComputerControlArgumentError):
            validate_arguments_against_schema("Click", {"label": "提交"}, {"type": "object"})

    def test_ui_tree_desktop_elements_do_not_inherit_focused_window(self):
        tree = (
            'Focused Window:\nName\n---------\n订单  1  Normal  100 100 1\n\n'
            'desktop\n'
            '├── (10,10) Button "开始" [action: click]\n'
            '├── window "订单"\n'
            '│   ├── (20,20) Edit "数量" [action: type]\n'
            '│   └── (30,30) Button "提交" [action: click]\n'
            '└── (40,40) Button "任务栏" [action: click]'
        )
        result = normalize_snapshot({"provider": "Windows-MCP", "content": [{"type": "text", "text": tree}]})
        by_name = {item["name"]: item for item in result["elements"]}
        self.assertEqual(by_name["数量"]["window_name"], "订单")
        self.assertEqual(by_name["任务栏"]["window_name"], "")
        self.assertEqual(by_name["任务栏"]["desktop_name"], "Desktop")
        self.assertEqual(result["window_element_counts"], {"订单": 2})
        self.assertEqual(result["desktop_element_count"], 2)
        self.assertEqual(result["provider"], "Windows-MCP")
        diagnostics = resolve_locator(result, {"name": "提交", "window_name": "订单"})["diagnostics"]
        self.assertEqual(diagnostics["provider"], "Windows-MCP")
        self.assertEqual(diagnostics["window_element_counts"], {"订单": 2})

    def test_structured_desktop_does_not_inherit_focused_window(self):
        result = normalize_snapshot({
            "focused_window": "订单",
            "elements": [
                {
                    "name": "Desktop 1",
                    "control_type": "Desktop",
                    "children": [{"name": "任务栏", "control_type": "Button", "bounds": [0, 0, 20, 20]}],
                }
            ],
        })
        taskbar = next(item for item in result["elements"] if item["name"] == "任务栏")
        self.assertEqual(taskbar["window_name"], "")
        self.assertEqual(taskbar["desktop_name"], "Desktop 1")


class TestObservationContext(unittest.TestCase):
    class Session:
        def __init__(self, tree: str):
            self.tree = tree

        async def call_tool(self, tool, arguments, timeout=None):
            _ = arguments, timeout
            if tool != "Snapshot":
                raise AssertionError(tool)
            return {"content": [{"type": "text", "text": self.tree}]}

        async def catalog(self):
            return [
                {
                    "name": "Type",
                    "inputSchema": {
                        "type": "object",
                        "properties": {"label": {"type": "integer"}, "loc": {"type": "array"}, "text": {"type": "string"}},
                    },
                }
            ]

    class Provider:
        def __init__(self):
            self.foreground = "订单"
            self.enhance_calls = []
            self.counter = 0

        async def enhance(self, snapshot, locator):
            self.counter += 1
            self.enhance_calls.append(dict(locator))
            return {**snapshot, "provider": "onecolleague-uia", "observation_id": f"obs_native_{self.counter}", "captured_at": 123.0 + self.counter}

        def current_foreground_window(self):
            return self.foreground

    def _runner(self):
        tree = (
            'Focused Window:\nName\n---------\n订单  1  Normal  100 100 1\n\n'
            'desktop "Desktop 1"\n└── window "订单"\n    └── (20,20) Edit "数量" [action: type]'
        )
        provider = self.Provider()
        runner = WorkflowRunner(Path(tempfile.mkdtemp()), object(), object(), self.Session(tree), observation_provider=provider)
        return runner, provider

    def test_observation_is_one_shot_and_reuses_provider_id(self):
        async def exercise():
            runner, provider = self._runner()
            node = WorkflowNode(
                id="type",
                type="action",
                tool="Type",
                arguments={"text": "2"},
                target=ElementLocator(window_name="订单", name="数量", control_type="Edit"),
            )
            event = {}
            arguments = await runner._resolve_element_action(node, {"text": "2"}, event)
            self.assertEqual(arguments["label"], 0)
            self.assertEqual(event["observation_context"]["observation_id"], "obs_native_1")
            self.assertEqual(event["observation_context"]["provider"], "onecolleague-uia")
            self.assertNotIn("mcp_label", event["observation_context"])
            await runner._validate_observation_before_call(event, "Type")
            runner._consume_observation_context(event, "consumed_success")
            with self.assertRaisesRegex(ElementResolutionError, "已经被使用"):
                await runner._validate_observation_before_call(event, "Type")
            second_event = {}
            await runner._resolve_element_action(node, {"text": "2"}, second_event)
            self.assertEqual(second_event["observation_context"]["observation_id"], "obs_native_2")
            self.assertEqual(len(provider.enhance_calls), 2)

        asyncio.run(exercise())

    def test_type_rejects_foreground_change_after_observation(self):
        async def exercise():
            runner, provider = self._runner()
            node = WorkflowNode(
                id="type",
                type="action",
                tool="Type",
                arguments={"text": "2"},
                target=ElementLocator(window_name="订单", name="数量", control_type="Edit"),
            )
            event = {}
            await runner._resolve_element_action(node, {"text": "2"}, event)
            provider.foreground = "其他窗口"
            with self.assertRaisesRegex(ElementResolutionError, "前台窗口"):
                await runner._validate_observation_before_call(event, "Type")
            self.assertEqual(event["observation_context"]["status"], "rejected_foreground_changed")

        asyncio.run(exercise())

    def test_type_rejects_when_live_foreground_is_unknown(self):
        async def exercise():
            runner, provider = self._runner()
            node = WorkflowNode(
                id="type",
                type="action",
                tool="Type",
                arguments={"text": "2"},
                target=ElementLocator(window_name="订单", name="数量", control_type="Edit"),
            )
            event = {}
            await runner._resolve_element_action(node, {"text": "2"}, event)
            provider.foreground = ""
            with self.assertRaisesRegex(ElementResolutionError, "无法确认"):
                await runner._validate_observation_before_call(event, "Type")
            self.assertEqual(event["observation_context"]["status"], "rejected_focus_unknown")

        asyncio.run(exercise())

    def test_click_requires_its_target_window_to_be_foreground(self):
        async def exercise():
            runner, provider = self._runner()
            node = WorkflowNode(
                id="click",
                type="action",
                tool="Click",
                arguments={},
                target=ElementLocator(window_name="订单", name="数量", control_type="Edit"),
            )
            event = {}
            await runner._resolve_element_action(node, {}, event)
            provider.foreground = "其他窗口"
            with self.assertRaisesRegex(ElementResolutionError, "点击目标窗口"):
                await runner._validate_observation_before_call(event, "Click")
            self.assertEqual(event["observation_context"]["status"], "rejected_foreground_changed")

        asyncio.run(exercise())

    def test_window_title_matching_requires_a_title_boundary(self):
        self.assertTrue(WorkflowRunner._window_matches("订单 - 企业系统", "订单"))
        self.assertFalse(WorkflowRunner._window_matches("Other App", "App"))


if __name__ == "__main__":
    unittest.main()
