import logging
import os
import tempfile
import unittest
from pathlib import Path


class TestObservabilityLogging(unittest.TestCase):
    def _with_home(self):
        old_home = os.environ.get("CCCC_HOME")
        td_ctx = tempfile.TemporaryDirectory()
        td = td_ctx.__enter__()
        os.environ["CCCC_HOME"] = td

        def cleanup() -> None:
            td_ctx.__exit__(None, None, None)
            if old_home is None:
                os.environ.pop("CCCC_HOME", None)
            else:
                os.environ["CCCC_HOME"] = old_home

        return Path(td), cleanup

    def test_apply_logger_levels_resets_removed_overrides(self) -> None:
        from no1.util.obslog import apply_logger_levels

        target = logging.getLogger("no1.test.override")
        original = target.level
        parent = logging.getLogger("no1.test")
        parent_original = parent.level
        try:
            apply_logger_levels({"no1.test": "DEBUG", "no1.test.override": "INFO"})
            self.assertEqual(parent.level, logging.DEBUG)
            self.assertEqual(target.level, logging.INFO)

            apply_logger_levels({"no1.test": "WARNING"})
            self.assertEqual(parent.level, logging.WARNING)
            self.assertEqual(target.level, logging.NOTSET)
        finally:
            target.setLevel(original)
            parent.setLevel(parent_original)
            apply_logger_levels({})

    def test_daemon_apply_observability_uses_targeted_debug(self) -> None:
        from no1.daemon.server import _apply_observability_settings

        home, cleanup = self._with_home()
        root = logging.getLogger()
        onecolleague_logger = logging.getLogger("onecolleague")
        delivery_logger = logging.getLogger("no1.delivery")
        asyncio_logger = logging.getLogger("asyncio")
        original_levels = {
            "root": root.level,
            "onecolleague": onecolleague_logger.level,
            "delivery": delivery_logger.level,
            "asyncio": asyncio_logger.level,
        }
        try:
            _apply_observability_settings(
                home,
                {
                    "developer_mode": True,
                    "log_level": "INFO",
                    "logger_levels": {"no1.daemon.group_space_ops": "DEBUG"},
                },
            )
            self.assertEqual(root.level, logging.INFO)
            self.assertEqual(onecolleague_logger.level, logging.DEBUG)
            self.assertEqual(delivery_logger.level, logging.INFO)
            self.assertEqual(asyncio_logger.level, logging.INFO)
            self.assertEqual(logging.getLogger("no1.daemon.group_space_ops").level, logging.DEBUG)
        finally:
            root.setLevel(original_levels["root"])
            onecolleague_logger.setLevel(original_levels["onecolleague"])
            delivery_logger.setLevel(original_levels["delivery"])
            asyncio_logger.setLevel(original_levels["asyncio"])
            cleanup()
