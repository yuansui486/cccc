import unittest


class TestRuntimeCommandDefaults(unittest.TestCase):
    def test_kimi_runtime_uses_yolo_flags_for_launch(self) -> None:
        from cccc.kernel.runtime import get_runtime_command_with_flags

        self.assertEqual(get_runtime_command_with_flags("kimi"), ["kimi", "--yolo"])


if __name__ == "__main__":
    unittest.main()
