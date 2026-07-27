import unittest

from no1.computer_control.elements import normalize_snapshot, resolve_locator
from no1.computer_control.mcp import ComputerControlArgumentError, validate_arguments_against_schema
from no1.computer_control.models import ElementLocator


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


if __name__ == "__main__":
    unittest.main()
