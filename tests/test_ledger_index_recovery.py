import json
import os
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch


class TestLedgerIndexRecovery(unittest.TestCase):
    def _ledger(self, root: Path) -> tuple[Path, dict]:
        ledger_path = root / "ledger.jsonl"
        event = {
            "v": 1,
            "id": "event-1",
            "ts": "2026-01-01T00:00:00Z",
            "kind": "chat.message",
            "by": "user",
            "data": {"text": "source of truth"},
        }
        ledger_path.write_text(json.dumps(event) + "\n", encoding="utf-8")
        return ledger_path, event

    def test_lookup_rebuilds_malformed_derived_index(self) -> None:
        from no1.kernel import ledger_index

        with tempfile.TemporaryDirectory() as td:
            ledger_path, expected = self._ledger(Path(td))
            index_path = ledger_index._index_path_for_ledger(ledger_path)
            index_path.parent.mkdir(parents=True)
            index_path.write_bytes(b"not a sqlite database")

            event = ledger_index.lookup_event_by_id(ledger_path, "event-1")

            self.assertEqual(event, expected)
            conn = sqlite3.connect(str(index_path))
            try:
                self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            finally:
                conn.close()

    def test_query_rebuilds_once_for_corruption(self) -> None:
        from no1.kernel import ledger_index

        with tempfile.TemporaryDirectory() as td:
            ledger_path, _ = self._ledger(Path(td))
            calls = 0

            def query(conn: sqlite3.Connection) -> int:
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise sqlite3.DatabaseError("database disk image is malformed")
                return int(conn.execute("SELECT COUNT(*) FROM events").fetchone()[0])

            self.assertEqual(ledger_index._query_ledger_index(ledger_path, query), 1)
            self.assertEqual(calls, 2)

    def test_non_corruption_error_does_not_discard_index(self) -> None:
        from no1.kernel import ledger_index

        with tempfile.TemporaryDirectory() as td:
            ledger_path, _ = self._ledger(Path(td))

            with patch.object(ledger_index, "_discard_index_files") as discard:
                with self.assertRaisesRegex(sqlite3.OperationalError, "database is locked"):
                    ledger_index._query_ledger_index(
                        ledger_path,
                        lambda _conn: (_ for _ in ()).throw(sqlite3.OperationalError("database is locked")),
                    )

            discard.assert_not_called()

    def test_plain_source_rebuilds_when_index_rows_exceed_source_state(self) -> None:
        from no1.kernel import ledger_index

        with tempfile.TemporaryDirectory() as td:
            ledger_path, _ = self._ledger(Path(td))
            ledger_index.catch_up_ledger_index(ledger_path)
            index_path = ledger_index._index_path_for_ledger(ledger_path)
            conn = sqlite3.connect(str(index_path))
            try:
                conn.execute(
                    """
                    INSERT INTO events(
                        event_id, ts, kind, by_actor, reply_to,
                        source_seq, source_path, line_no, offset_bytes
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    ("phantom", "2026-01-02T00:00:00Z", "chat.message", "user", "", 1_000_000_000, "ledger.jsonl", 99, 0),
                )
                conn.commit()
            finally:
                conn.close()

            ledger_index.catch_up_ledger_index(ledger_path)

            conn = sqlite3.connect(str(index_path))
            try:
                self.assertIsNone(conn.execute("SELECT 1 FROM events WHERE event_id = 'phantom'").fetchone())
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM events").fetchone()[0], 1)
            finally:
                conn.close()

    def test_invalid_source_state_reindexes_against_ledger_path(self) -> None:
        from no1.kernel import ledger_index

        with tempfile.TemporaryDirectory() as td:
            ledger_path, _ = self._ledger(Path(td))
            ledger_index.catch_up_ledger_index(ledger_path)
            index_path = ledger_index._index_path_for_ledger(ledger_path)
            conn = sqlite3.connect(str(index_path))
            try:
                conn.execute(
                    "UPDATE source_state SET last_offset_bytes = ? WHERE source_path = 'ledger.jsonl'",
                    ("invalid",),
                )
                conn.commit()
            finally:
                conn.close()

            original = ledger_index._reindex_source
            with patch.object(ledger_index, "_reindex_source", wraps=original) as reindex:
                ledger_index.catch_up_ledger_index(ledger_path)

            self.assertTrue(reindex.called)
            self.assertTrue(all(call.args[1] == ledger_path for call in reindex.call_args_list))

    def test_ensure_layout_does_not_touch_existing_active_ledger(self) -> None:
        from no1.kernel.ledger_segments import active_ledger_path, ensure_ledger_layout

        with tempfile.TemporaryDirectory() as td:
            group_path = Path(td)
            ensure_ledger_layout(group_path)
            active = active_ledger_path(group_path)
            fixed_ns = 1_700_000_000_123_456_789
            os.utime(active, ns=(fixed_ns, fixed_ns))

            ensure_ledger_layout(group_path)

            self.assertEqual(active.stat().st_mtime_ns, fixed_ns)

    def test_catch_up_ledger_index_waits_for_index_lock(self) -> None:
        from no1.kernel import ledger_index
        from no1.util.file_lock import acquire_lockfile, release_lockfile

        with tempfile.TemporaryDirectory() as td:
            ledger_path, _ = self._ledger(Path(td))
            lock_handle = acquire_lockfile(ledger_index._index_lock_path_for_ledger(ledger_path), blocking=True)
            attempted = threading.Event()
            finished = threading.Event()
            errors: list[BaseException] = []
            real_acquire = ledger_index.acquire_lockfile

            def observed_acquire(path, *, blocking: bool = True):
                attempted.set()
                return real_acquire(path, blocking=blocking)

            def run_catch_up() -> None:
                try:
                    ledger_index.catch_up_ledger_index(ledger_path)
                except BaseException as exc:
                    errors.append(exc)
                finally:
                    finished.set()

            try:
                with patch.object(ledger_index, "acquire_lockfile", side_effect=observed_acquire):
                    thread = threading.Thread(target=run_catch_up)
                    thread.start()
                    self.assertTrue(attempted.wait(timeout=1.0))
                    self.assertFalse(finished.wait(timeout=0.15))
                    release_lockfile(lock_handle)
                    lock_handle = None
                    thread.join(timeout=2.0)

                self.assertTrue(finished.is_set())
                self.assertEqual(errors, [])
            finally:
                if lock_handle is not None:
                    release_lockfile(lock_handle)

    def test_append_event_to_index_skips_when_index_lock_busy(self) -> None:
        from no1.kernel import ledger_index
        from no1.util.file_lock import acquire_lockfile, release_lockfile

        with tempfile.TemporaryDirectory() as td:
            ledger_path, event = self._ledger(Path(td))
            lock_handle = acquire_lockfile(ledger_index._index_lock_path_for_ledger(ledger_path), blocking=True)
            try:
                with patch.object(ledger_index, "_connect", side_effect=AssertionError("busy append must skip")):
                    ledger_index.append_event_to_index(
                        ledger_path,
                        event,
                        next_offset_bytes=ledger_path.stat().st_size,
                    )
            finally:
                release_lockfile(lock_handle)

    def test_catch_up_restores_missing_event_row_without_source_change(self) -> None:
        from no1.kernel import ledger_index

        with tempfile.TemporaryDirectory() as td:
            ledger_path, expected = self._ledger(Path(td))
            ledger_index.catch_up_ledger_index(ledger_path)
            index_path = ledger_index._index_path_for_ledger(ledger_path)
            conn = sqlite3.connect(str(index_path))
            try:
                conn.execute("DELETE FROM event_search WHERE event_id = ?", (expected["id"],))
                conn.execute("DELETE FROM events WHERE event_id = ?", (expected["id"],))
                conn.commit()
            finally:
                conn.close()

            ledger_index.catch_up_ledger_index(ledger_path)

            self.assertEqual(ledger_index.lookup_event_by_id(ledger_path, expected["id"]), expected)

    def test_catch_up_restores_missing_event_search_row_without_source_change(self) -> None:
        from no1.kernel import ledger_index

        with tempfile.TemporaryDirectory() as td:
            ledger_path, expected = self._ledger(Path(td))
            ledger_index.catch_up_ledger_index(ledger_path)
            index_path = ledger_index._index_path_for_ledger(ledger_path)
            conn = sqlite3.connect(str(index_path))
            try:
                conn.execute("DELETE FROM event_search WHERE event_id = ?", (expected["id"],))
                conn.commit()
            finally:
                conn.close()

            ledger_index.catch_up_ledger_index(ledger_path)

            conn = sqlite3.connect(str(index_path))
            try:
                row = conn.execute(
                    "SELECT searchable_text FROM event_search WHERE event_id = ?",
                    (expected["id"],),
                ).fetchone()
            finally:
                conn.close()
            self.assertIsNotNone(row)
            self.assertIn("source of truth", str((row or [""])[0]))

    def test_blank_and_invalid_lines_do_not_cause_repeated_rebuild(self) -> None:
        from no1.kernel import ledger_index

        with tempfile.TemporaryDirectory() as td:
            ledger_path, _ = self._ledger(Path(td))
            with ledger_path.open("a", encoding="utf-8") as handle:
                handle.write("\nnot-json\n")
            ledger_index.catch_up_ledger_index(ledger_path)

            with patch.object(ledger_index, "_reindex_source", wraps=ledger_index._reindex_source) as reindex:
                ledger_index.catch_up_ledger_index(ledger_path)

            reindex.assert_not_called()


if __name__ == "__main__":
    unittest.main()
