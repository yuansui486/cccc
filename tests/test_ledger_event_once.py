from __future__ import annotations

import hashlib
import gzip
import json
import multiprocessing
import os
import shutil
import threading
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch


def _append_from_process(args: tuple[str, str]) -> tuple[bool, str]:
    ledger_path, event_id = args
    try:
        from no1.kernel.ledger import append_event_once

        _, replayed = append_event_once(
            Path(ledger_path),
            event_id=event_id,
            kind="chat.message",
            group_id="g_test",
            scope_key="",
            by="system",
            data={"text": "process", "to": ["user"]},
        )
        return replayed, ""
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


class TestLedgerEventOnce(unittest.TestCase):
    def setUp(self) -> None:
        from no1.kernel import ledger

        self._tmp = tempfile.TemporaryDirectory()
        self.group = Path(self._tmp.name)
        self.ledger_path = self.group / "ledger.jsonl"
        ledger._EVENT_ONCE_RUNTIME.clear()

    def tearDown(self) -> None:
        from no1.kernel import ledger

        ledger._EVENT_ONCE_RUNTIME.clear()
        self._tmp.cleanup()

    def _append(self, event_id: str, *, text: str = "hello"):
        from no1.kernel.ledger import append_event_once

        return append_event_once(
            self.ledger_path,
            event_id=event_id,
            kind="chat.message",
            group_id="g_test",
            scope_key="",
            by="system",
            data={"text": text, "to": ["user"]},
        )

    def _journal_path(self) -> Path:
        return self.group / "state" / "ledger" / "event-once.jsonl"

    def test_reserve_commit_hash_chain_and_replay(self) -> None:
        first, replayed = self._append("gbs_" + ("a" * 32))
        self.assertFalse(replayed)
        original_source = self.ledger_path.read_bytes()
        journal_lines = self._journal_path().read_bytes().splitlines()
        self.assertEqual(len(journal_lines), 2)
        previous = ""
        for sequence, raw in enumerate(journal_lines, start=1):
            record = json.loads(raw)
            self.assertEqual(record["seq"], sequence)
            self.assertEqual(record["prev_hash"], previous)
            body = {key: value for key, value in record.items() if key != "record_hash"}
            expected = hashlib.sha256(
                json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            self.assertEqual(record["record_hash"], expected)
            previous = record["record_hash"]
        replay, was_replayed = self._append("gbs_" + ("a" * 32))
        self.assertTrue(was_replayed)
        self.assertEqual(replay, first)
        self.assertEqual(self.ledger_path.read_bytes(), original_source)

    def test_missing_partial_and_same_size_journal_rebuild_from_source(self) -> None:
        event_id = "gbs_" + ("b" * 32)
        first, _ = self._append(event_id)
        journal_path = self._journal_path()
        for mode in ("missing", "partial", "same-size", "inode", "rollback"):
            with self.subTest(mode=mode):
                if mode == "missing":
                    journal_path.unlink()
                elif mode == "partial":
                    journal_path.write_bytes(journal_path.read_bytes() + b"partial")
                elif mode == "same-size":
                    broken = bytearray(journal_path.read_bytes())
                    broken[len(broken) // 2] = ord("0") if broken[len(broken) // 2] != ord("0") else ord("1")
                    journal_stat = journal_path.stat()
                    journal_path.write_bytes(bytes(broken))
                    os.utime(journal_path, ns=(journal_stat.st_atime_ns, journal_stat.st_mtime_ns))
                elif mode == "inode":
                    replacement = journal_path.with_suffix(".replacement")
                    replacement.write_bytes(journal_path.read_bytes())
                    os.replace(replacement, journal_path)
                else:
                    journal_path.write_bytes(journal_path.read_bytes().splitlines()[0] + b"\n")
                from no1.kernel import ledger

                ledger._EVENT_ONCE_RUNTIME.clear()
                replay, was_replayed = self._append(event_id)
                self.assertTrue(was_replayed)
                self.assertEqual(replay, first)
                self.assertEqual(len(self.ledger_path.read_bytes().splitlines()), 1)

    def test_live_partial_journal_rebuilds_without_duplicate_append(self) -> None:
        from no1.kernel import ledger

        event_id = "gbs_" + ("2" * 32)
        first, _ = self._append(event_id)
        journal_path = self._journal_path()
        journal_path.write_bytes(journal_path.read_bytes() + b"partial")
        with patch.object(
            ledger,
            "_event_once_rebuild_journal_locked",
            wraps=ledger._event_once_rebuild_journal_locked,
        ) as rebuild:
            replay, was_replayed = self._append(event_id)
        self.assertTrue(was_replayed)
        self.assertEqual(replay, first)
        rebuild.assert_called_once()
        self.assertEqual(len(self.ledger_path.read_bytes().splitlines()), 1)

    def test_live_same_stat_journal_tamper_rebuilds_from_source(self) -> None:
        from no1.kernel import ledger

        event_id = "gbs_" + ("1" * 32)
        first, _ = self._append(event_id)
        journal_path = self._journal_path()
        journal_stat = journal_path.stat()
        broken = bytearray(journal_path.read_bytes())
        broken[len(broken) // 2] = ord("0") if broken[len(broken) // 2] != ord("0") else ord("1")
        journal_path.write_bytes(bytes(broken))
        os.utime(journal_path, ns=(journal_stat.st_atime_ns, journal_stat.st_mtime_ns))

        with patch.object(
            ledger,
            "_event_once_rebuild_journal_locked",
            wraps=ledger._event_once_rebuild_journal_locked,
        ) as rebuild:
            replay, was_replayed = self._append(event_id)
        self.assertTrue(was_replayed)
        self.assertEqual(replay, first)
        rebuild.assert_called_once()
        self.assertEqual(len(self.ledger_path.read_bytes().splitlines()), 1)

    def test_reserve_prefix_recovers_without_duplicate_append(self) -> None:
        from no1.kernel import ledger

        event_id = "gbs_" + ("c" * 32)
        with patch.object(ledger, "_append_line_locked", side_effect=RuntimeError("simulated process crash")):
            with self.assertRaises(RuntimeError):
                self._append(event_id)
        self.assertEqual(self.ledger_path.read_bytes() if self.ledger_path.exists() else b"", b"")
        self.assertEqual(len(self._journal_path().read_bytes().splitlines()), 1)
        ledger._EVENT_ONCE_RUNTIME.clear()
        event, replayed = self._append(event_id)
        self.assertFalse(replayed)
        self.assertEqual(event["id"], event_id)
        self.assertEqual(len(self.ledger_path.read_bytes().splitlines()), 1)

    def test_commit_prefix_recovers_after_source_append_before_commit(self) -> None:
        from no1.kernel import ledger

        event_id = "gbs_" + ("3" * 32)
        original = ledger._event_once_append_journal_locked

        def crash_on_commit(*args, **kwargs):
            if kwargs.get("operation") == "commit":
                raise RuntimeError("simulated crash after source append")
            return original(*args, **kwargs)

        with patch.object(ledger, "_event_once_append_journal_locked", side_effect=crash_on_commit):
            with self.assertRaises(RuntimeError):
                self._append(event_id)
        self.assertEqual(len(self.ledger_path.read_bytes().splitlines()), 1)
        self.assertEqual(len(self._journal_path().read_bytes().splitlines()), 1)
        ledger._EVENT_ONCE_RUNTIME.clear()
        replay, was_replayed = self._append(event_id)
        self.assertTrue(was_replayed)
        self.assertEqual(replay["id"], event_id)
        self.assertEqual(len(self.ledger_path.read_bytes().splitlines()), 1)

    def test_commit_prefix_recovers_after_process_restart_without_duplicate(self) -> None:
        from no1.kernel import ledger

        event_id = "gbs_" + ("2" * 32)
        original = ledger._event_once_append_journal_locked

        def crash_after_commit(*args, **kwargs):
            original(*args, **kwargs)
            if kwargs.get("operation") == "commit":
                raise RuntimeError("simulated process crash after commit")

        with patch.object(ledger, "_event_once_append_journal_locked", side_effect=crash_after_commit):
            with self.assertRaises(RuntimeError):
                self._append(event_id)
        source_before = self.ledger_path.read_bytes()
        ledger._EVENT_ONCE_RUNTIME.clear()
        event, replayed = self._append(event_id)
        self.assertTrue(replayed)
        self.assertEqual(event["id"], event_id)
        self.assertEqual(self.ledger_path.read_bytes(), source_before)
        self.assertEqual(len(self.ledger_path.read_bytes().splitlines()), 1)

    def test_partial_journal_suffix_rebuilds_from_source_without_append(self) -> None:
        event_id = "gbs_" + ("3" * 32)
        first, replayed = self._append(event_id)
        self.assertFalse(replayed)
        journal_path = self._journal_path()
        journal_path.write_bytes(journal_path.read_bytes() + b"partial")
        source_before = self.ledger_path.read_bytes()
        replay, replayed = self._append(event_id)
        self.assertTrue(replayed)
        self.assertEqual(replay, first)
        self.assertEqual(self.ledger_path.read_bytes(), source_before)

    def test_healthy_process_uses_active_delta_after_ordinary_append(self) -> None:
        from no1.kernel import ledger
        from no1.kernel.ledger import append_event

        self._append("gbs_" + ("d" * 32))
        append_event(
            self.ledger_path,
            kind="group.note",
            group_id="g_test",
            scope_key="",
            by="system",
            data={"text": "ordinary"},
        )
        with patch.object(
            ledger,
            "_event_once_rebuild_journal_locked",
            wraps=ledger._event_once_rebuild_journal_locked,
        ) as rebuild:
            event, replayed = self._append("gbs_" + ("e" * 32))
        self.assertFalse(replayed)
        self.assertEqual(event["id"], "gbs_" + ("e" * 32))
        rebuild.assert_not_called()

    def test_ordinary_append_advances_frontier_without_delta_scan(self) -> None:
        from no1.kernel import ledger
        from no1.kernel.ledger import append_event

        self._append("gbs_" + ("7" * 32))
        append_event(
            self.ledger_path,
            kind="group.note",
            group_id="g_test",
            scope_key="",
            by="system",
            data={"text": "ordinary"},
        )
        with patch.object(ledger, "_scan_event_once_sources", wraps=ledger._scan_event_once_sources) as scan:
            event, replayed = self._append("gbs_" + ("6" * 32))
        self.assertFalse(replayed)
        self.assertEqual(event["id"], "gbs_" + ("6" * 32))
        scan.assert_not_called()

    def test_healthy_replay_uses_committed_locator_without_rebuild(self) -> None:
        from no1.kernel import ledger

        event_id = "gbs_" + ("9" * 32)
        first, replayed = self._append(event_id)
        self.assertFalse(replayed)
        with patch.object(
            ledger,
            "_event_once_rebuild_journal_locked",
            side_effect=AssertionError("healthy replay must not rebuild the full journal"),
        ):
            second, replayed = self._append(event_id)
        self.assertTrue(replayed)
        self.assertEqual(second, first)

    def test_healthy_replay_does_not_catch_up_derived_index(self) -> None:
        from no1.kernel import ledger

        event_id = "gbs_" + ("8" * 32)
        first, replayed = self._append(event_id)
        self.assertFalse(replayed)
        with patch.object(ledger, "_repair_event_once_index") as repair:
            second, replayed = self._append(event_id)
        self.assertTrue(replayed)
        self.assertEqual(second, first)
        repair.assert_not_called()

    def test_same_id_thread_competition_writes_one_source_fact(self) -> None:
        event_id = "gbs_" + ("4" * 32)
        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(lambda _index: self._append(event_id), range(4)))
        self.assertEqual(sum(1 for _, replayed in results if not replayed), 1)
        self.assertEqual(sum(1 for _, replayed in results if replayed), 3)
        self.assertEqual(len(self.ledger_path.read_bytes().splitlines()), 1)

    def test_same_id_process_competition_writes_one_source_fact(self) -> None:
        if "fork" not in multiprocessing.get_all_start_methods():
            self.skipTest("process competition test requires fork")
        event_id = "gbs_" + ("5" * 32)
        context = multiprocessing.get_context("fork")
        with context.Pool(2) as pool:
            results = pool.map(_append_from_process, [(str(self.ledger_path), event_id)] * 2)
        self.assertEqual([error for _, error in results], ["", ""])
        self.assertEqual(sorted(replayed for replayed, _ in results), [False, True])
        self.assertEqual(len(self.ledger_path.read_bytes().splitlines()), 1)

    def test_warm_parent_catches_up_child_commit_without_duplicate(self) -> None:
        if "fork" not in multiprocessing.get_all_start_methods():
            self.skipTest("process catch-up test requires fork")
        from no1.kernel import ledger
        from no1.kernel.ledger import append_event_once

        self._append("gbs_" + ("a" * 32))
        child_id = "gbs_" + ("b" * 32)
        context = multiprocessing.get_context("fork")
        with context.Pool(1) as pool:
            results = pool.map(_append_from_process, [(str(self.ledger_path), child_id)])
        self.assertEqual(results, [(False, "")])

        with patch.object(
            ledger,
            "_event_once_rebuild_journal_locked",
            side_effect=AssertionError("warm cross-process catch-up must use suffix and delta"),
        ):
            event, replayed = append_event_once(
                self.ledger_path,
                event_id=child_id,
                kind="chat.message",
                group_id="g_test",
                scope_key="",
                by="system",
                data={"text": "process", "to": ["user"]},
            )
        self.assertTrue(replayed)
        self.assertEqual(event["id"], child_id)
        self.assertEqual(len(self.ledger_path.read_bytes().splitlines()), 2)

    def test_same_id_different_facts_never_appends(self) -> None:
        from no1.kernel import ledger

        event_id = "gbs_" + ("6" * 32)
        first, _ = self._append(event_id, text="first")
        source_before = self.ledger_path.read_bytes()
        with self.assertRaises(ledger.LedgerEventConflictError):
            self._append(event_id, text="second")
        self.assertEqual(self.ledger_path.read_bytes(), source_before)
        self.assertEqual(first["data"]["text"], "first")

    def test_strict_plain_and_gzip_source_failures_are_bounded_and_zero_write(self) -> None:
        from no1.kernel import ledger

        bad_records = (
            b"not-json\n",
            b"[]\n",
            b'{"id":"gbs_' + (b"a" * 32) + b'","id":"gbs_' + (b"b" * 32) + b'"}\n',
            b'{"value":NaN}\n',
        )
        for compressed in (False, True):
            for index, source_bytes in enumerate(bad_records):
                with self.subTest(compressed=compressed, index=index):
                    ledger._EVENT_ONCE_RUNTIME.clear()
                    shutil.rmtree(self.group / "state", ignore_errors=True)
                    self.ledger_path.unlink(missing_ok=True)
                    self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
                    source_path = self.ledger_path
                    if compressed:
                        segment_dir = self.group / "state" / "ledger" / "segments"
                        segment_dir.mkdir(parents=True, exist_ok=True)
                        source_path = segment_dir / "ledger.20260731T000000Z.000001.jsonl.gz"
                        source_path.write_bytes(gzip.compress(source_bytes))
                        self.ledger_path.touch()
                    else:
                        source_path.write_bytes(source_bytes)
                    source_before = source_path.read_bytes()
                    active_before = self.ledger_path.read_bytes()
                    with self.assertRaises(ledger.LedgerEventSourceError):
                        self._append(f"gbs_{index + (100 if compressed else 0):032x}")
                    self.assertEqual(source_path.read_bytes(), source_before)
                    self.assertEqual(self.ledger_path.read_bytes(), active_before)

    def test_rotation_and_gzip_force_rebuild_then_replay(self) -> None:
        from no1.kernel import ledger
        from no1.kernel.ledger_segments import compress_sealed_segments, rotate_active_ledger

        event_id = "gbs_" + ("f" * 32)
        first, _ = self._append(event_id)
        self.assertTrue(rotate_active_ledger(self.group, reason="event-once-test")["rotated"])
        compress_sealed_segments(self.group, keep_recent=0, force=True)
        ledger._EVENT_ONCE_RUNTIME.clear()
        replay, was_replayed = self._append(event_id)
        self.assertTrue(was_replayed)
        self.assertEqual(replay, first)

    def test_rotation_and_compression_wait_for_ledger_lock(self) -> None:
        from no1.kernel import ledger
        from no1.kernel.ledger import append_event
        from no1.kernel.ledger_segments import compress_sealed_segments, rotate_active_ledger

        append_event(
            self.ledger_path,
            kind="group.note",
            group_id="g_test",
            scope_key="",
            by="system",
            data={"text": "ordinary"},
        )
        lock = ledger._lock_path(self.ledger_path)
        handle = ledger.acquire_lockfile(lock, blocking=True)
        results: list[object] = []
        try:
            thread = threading.Thread(
                target=lambda: results.append(rotate_active_ledger(self.group, reason="locked-test"))
            )
            thread.start()
            thread.join(timeout=0.2)
            self.assertTrue(thread.is_alive())
        finally:
            ledger.release_lockfile(handle)
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.assertTrue(results and results[0].get("rotated"))

        handle = ledger.acquire_lockfile(lock, blocking=True)
        results.clear()
        try:
            thread = threading.Thread(
                target=lambda: results.append(compress_sealed_segments(self.group, keep_recent=0, force=True))
            )
            thread.start()
            thread.join(timeout=0.2)
            self.assertTrue(thread.is_alive())
        finally:
            ledger.release_lockfile(handle)
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.assertTrue(results and results[0].get("count") == 1)

    def test_healthy_gzip_replay_does_not_scan_compressed_segment(self) -> None:
        from no1.kernel import ledger
        from no1.kernel.ledger import append_event
        from no1.kernel.ledger_segments import compress_sealed_segments, rotate_active_ledger

        for index in range(20):
            append_event(
                self.ledger_path,
                kind="group.note",
                group_id="g_test",
                scope_key="",
                by="system",
                data={"text": f"ordinary-{index}"},
            )
        event_id = "gbs_" + ("0" * 32)
        first, _ = self._append(event_id)
        self.assertTrue(rotate_active_ledger(self.group, reason="event-once-test")["rotated"])
        compress_sealed_segments(self.group, keep_recent=0, force=True)
        ledger._EVENT_ONCE_RUNTIME.clear()
        replay, was_replayed = self._append(event_id)
        self.assertTrue(was_replayed)
        self.assertEqual(replay, first)

        with patch.object(
            ledger,
            "_iter_bounded_source_records",
            side_effect=AssertionError("healthy gzip replay must not scan the segment"),
        ):
            replay, was_replayed = self._append(event_id)
        self.assertTrue(was_replayed)
        self.assertEqual(replay, first)

    def test_gzip_replacement_after_snapshot_fails_closed(self) -> None:
        from no1.kernel import ledger
        from no1.kernel.ledger_segments import compress_sealed_segments, rotate_active_ledger

        event_id = "gbs_" + ("e" * 32)
        first, _ = self._append(event_id, text="first")
        self.assertTrue(rotate_active_ledger(self.group, reason="event-once-test")["rotated"])
        compress_sealed_segments(self.group, keep_recent=0, force=True)
        ledger._EVENT_ONCE_RUNTIME.clear()
        replay, was_replayed = self._append(event_id, text="first")
        self.assertTrue(was_replayed)
        self.assertEqual(replay, first)

        gzip_path = next((self.group / "state" / "ledger" / "segments").glob("*.jsonl.gz"))
        replacement = gzip.compress(gzip.decompress(gzip_path.read_bytes()).replace(b"first", b"other"))
        original_snapshot = ledger._source_snapshot
        replaced = False

        def replace_after_snapshot(path: Path):
            nonlocal replaced
            snapshot = original_snapshot(path)
            if not replaced:
                replaced = True
                temporary = gzip_path.with_suffix(".replacement")
                temporary.write_bytes(replacement)
                os.replace(temporary, gzip_path)
            return snapshot

        source_before = self.ledger_path.read_bytes()
        with patch.object(ledger, "_source_snapshot", side_effect=replace_after_snapshot):
            with self.assertRaises(ledger.LedgerEventConflictError):
                self._append(event_id, text="first")
        self.assertTrue(replaced)
        self.assertEqual(self.ledger_path.read_bytes(), source_before)

    def test_same_stat_source_tamper_forces_strict_recovery(self) -> None:
        from no1.kernel import ledger
        from no1.kernel.ledger import append_event

        append_event(
            self.ledger_path,
            kind="group.note",
            group_id="g_test",
            scope_key="",
            by="system",
            data={"text": "ordinary"},
        )
        self._append("gbs_" + ("a" * 32))
        source_stat = self.ledger_path.stat()
        source = bytearray(self.ledger_path.read_bytes())
        source[0] = ord("[")
        self.ledger_path.write_bytes(bytes(source))
        os.utime(self.ledger_path, ns=(source_stat.st_atime_ns, source_stat.st_mtime_ns))

        source_before = self.ledger_path.read_bytes()
        with self.assertRaises(ledger.LedgerEventSourceError):
            self._append("gbs_" + ("b" * 32))
        self.assertEqual(self.ledger_path.read_bytes(), source_before)

    def test_prefix_tamper_plus_unjournaled_growth_forces_full_recovery(self) -> None:
        from no1.kernel import ledger
        from no1.kernel.ledger import append_event

        append_event(
            self.ledger_path,
            kind="group.note",
            group_id="g_test",
            scope_key="",
            by="system",
            data={"text": "ordinary"},
        )
        self._append("gbs_" + ("c" * 32))
        source = bytearray(self.ledger_path.read_bytes())
        source[0] = ord("[")
        self.ledger_path.write_bytes(bytes(source))
        with self.ledger_path.open("ab") as handle:
            handle.write(b'{"id":"ordinary-external"}\n')

        source_before = self.ledger_path.read_bytes()
        with self.assertRaises(ledger.LedgerEventSourceError):
            self._append("gbs_" + ("d" * 32))
        self.assertEqual(self.ledger_path.read_bytes(), source_before)

    def test_duplicate_source_and_locator_tamper_fail_closed(self) -> None:
        from no1.kernel import ledger

        event_id = "gbs_" + ("1" * 32)
        first, _ = self._append(event_id)
        raw = self.ledger_path.read_bytes()
        with self.ledger_path.open("ab") as handle:
            handle.write(raw)
        ledger._EVENT_ONCE_RUNTIME.clear()
        with self.assertRaises(ledger.LedgerEventConflictError):
            self._append(event_id)
        self.assertEqual(len(self.ledger_path.read_bytes().splitlines()), 2)

        self.ledger_path.write_bytes(self.ledger_path.read_bytes().splitlines()[0] + b"\n")
        ledger._EVENT_ONCE_RUNTIME.clear()
        tampered = self.ledger_path.read_bytes().replace(b"hello", b"world", 1)
        self.ledger_path.write_bytes(tampered)
        with self.assertRaises(ledger.LedgerEventConflictError):
            self._append(event_id)
        self.assertEqual(self.ledger_path.read_bytes(), tampered)
        self.assertEqual(first["id"], event_id)

    def test_namespace_is_closed(self) -> None:
        from no1.kernel.ledger import append_event_once

        with self.assertRaises(ValueError):
            append_event_once(
                self.ledger_path,
                event_id="event-1",
                kind="group.note",
                group_id="g_test",
                scope_key="",
                by="system",
                data={"text": "bad"},
            )


if __name__ == "__main__":
    unittest.main()
