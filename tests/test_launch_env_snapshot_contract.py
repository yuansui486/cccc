import inspect
import unittest

from no1.daemon.actors.actor_lifecycle_ops import handle_actor_restart
from no1.daemon.actors.actor_update_ops import handle_actor_update
from no1.daemon.group.bootstrap_actor_ops import autostart_running_groups
from no1.daemon.group.group_lifecycle_ops import handle_group_start


class TestLaunchEnvSnapshotContract(unittest.TestCase):
    def test_each_launch_path_builds_one_runtime_env_snapshot(self) -> None:
        for handler in (
            handle_group_start,
            autostart_running_groups,
            handle_actor_restart,
            handle_actor_update,
        ):
            with self.subTest(handler=handler.__name__):
                source = inspect.getsource(handler)
                self.assertEqual(source.count("prepare_runtime_mcp_env("), 1)
                self.assertNotIn("def _launch_env", source)
                self.assertGreaterEqual(source.count("dict(launch_env)"), 2)


if __name__ == "__main__":
    unittest.main()
