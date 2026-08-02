import ast
import multiprocessing
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


def _facts(event_id: str = "event-1") -> dict:
    return {
        "src_group_id": "source-group",
        "source_event_id": event_id,
        "reply_to_remote_event_id": "remote-parent",
        "group_bridge_thread": "thread-1",
        "payload": {"text": "hello", "to": ["peer"]},
    }


def _receipt_worker(home: str, index: int, same_key: bool, results) -> None:
    from no1.kernel.group_bridge.receipts import record_receipt

    key = "shared" if same_key else f"key-{index}"
    facts = _facts() if same_key else _facts(f"event-{index}")
    try:
        _, created = record_receipt(
            "reg_1",
            key,
            {"status": "sent", "remote_event_id": f"remote-{index}"},
            Path(home),
            request_facts=facts,
        )
        results.put(("ok", created))
    except Exception as exc:
        results.put(("error", type(exc).__name__))


def _run_receipt_processes(home: Path, *, count: int, same_key: bool):
    context = multiprocessing.get_context("spawn")
    results = context.Queue()
    processes = [context.Process(target=_receipt_worker, args=(str(home), index, same_key, results)) for index in range(count)]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=20)
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
        if process.exitcode != 0:
            raise AssertionError(f"worker exited with code {process.exitcode}")
    return [results.get(timeout=5) for _ in processes]


class TestGroupBridgeReceipts(unittest.TestCase):
    _ATOMIC_KEY = "gbs_" + "a" * 32

    @staticmethod
    def _queued_request(key: str = _ATOMIC_KEY, event_id: str = "event-1") -> dict:
        return {
            "src_group_id": "source-group",
            "registration_id": "reg_1",
            "idempotency_key": key,
            "payload": {
                "text": f"hello from {event_id}",
                "format": "plain",
                "priority": "normal",
                "reply_required": False,
            },
        }

    def _record_after_admission(self, home: Path, callback, *, event_id: str = "event-1"):
        from no1.kernel.group_bridge.receipts import record_receipt_after_admission

        return record_receipt_after_admission(
            "reg_1",
            self._ATOMIC_KEY,
            {"status": "queued", "transport": "group_bridge_session"},
            home,
            request_facts=_facts(event_id),
            queued_request=self._queued_request(event_id=event_id),
            admit_new=callback,
        )

    def test_atomic_admission_exact_replay_skips_callback(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            calls = []
            first, created = self._record_after_admission(home, lambda: calls.append("new"))
            replay, replay_created = self._record_after_admission(home, lambda: calls.append("replay"))
            self.assertTrue(created)
            self.assertFalse(replay_created)
            self.assertEqual(replay, first)
            self.assertEqual(calls, ["new"])

    def test_atomic_admission_conflict_skips_callback(self) -> None:
        from no1.kernel.group_bridge.receipts import ReceiptConflictError

        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            calls = []
            self._record_after_admission(home, lambda: calls.append("new"))
            with self.assertRaises(ReceiptConflictError):
                self._record_after_admission(home, lambda: calls.append("conflict"), event_id="event-2")
            self.assertEqual(calls, ["new"])

    def test_atomic_admission_runs_once_before_save(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            path = home / "group_bridge_receipts.yaml"
            observations = []
            receipt, created = self._record_after_admission(home, lambda: observations.append(path.exists()))
            self.assertTrue(created)
            self.assertEqual(observations, [False])
            self.assertEqual(receipt["status"], "queued")

    def test_atomic_admission_exception_does_not_write(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            path = home / "group_bridge_receipts.yaml"

            def reject() -> None:
                raise PermissionError("not admitted")

            with self.assertRaises(PermissionError):
                self._record_after_admission(home, reject)
            self.assertFalse(path.exists())

    def test_atomic_admission_serializes_two_threads(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            count = 0
            count_lock = threading.Lock()

            def admit() -> None:
                nonlocal count
                with count_lock:
                    count += 1

            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(lambda _: self._record_after_admission(home, admit), range(2)))
            self.assertEqual(count, 1)
            self.assertEqual(sorted(created for _, created in results), [False, True])

    def test_atomic_admission_malformed_store_skips_callback_and_write(self) -> None:
        from no1.kernel.group_bridge.receipts import ReceiptStoreError

        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            path = home / "group_bridge_receipts.yaml"
            original = b"receipts: [\n"
            path.write_bytes(original)
            calls = []
            with self.assertRaises(ReceiptStoreError):
                self._record_after_admission(home, lambda: calls.append("called"))
            self.assertEqual(calls, [])
            self.assertEqual(path.read_bytes(), original)

    def test_atomic_admission_has_one_production_caller_and_no_replay_preread(self) -> None:
        root = Path(__file__).resolve().parents[1]
        production = root / "src" / "no1"
        callers = []
        for path in production.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    if node.func.id == "record_receipt_after_admission":
                        callers.append(path.relative_to(root).as_posix())
        self.assertEqual(
            callers,
            ["src/no1/daemon/group_bridge/remote_dispatch.py"],
        )
        source = (production / "daemon" / "group_bridge" / "remote_dispatch.py").read_text(encoding="utf-8")
        enqueue = source[source.index("def enqueue_remote_send(") : source.index("def remote_delivery_status(")]
        self.assertNotIn("get_receipt_strict(", enqueue)
        self.assertNotIn("request_fingerprint(", enqueue)

    def test_same_key_and_request_facts_replay_first_result(self) -> None:
        from no1.kernel.group_bridge.receipts import get_receipt, record_receipt

        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            first, created = record_receipt(
                "reg_1", "key-1", {"status": "sent", "remote_event_id": "remote-1"}, home, request_facts=_facts()
            )
            replay, replay_created = record_receipt(
                "reg_1", "key-1", {"status": "sent", "remote_event_id": "remote-2"}, home, request_facts=_facts()
            )
            self.assertTrue(created)
            self.assertFalse(replay_created)
            self.assertEqual(replay["remote_event_id"], "remote-1")
            self.assertEqual(first, get_receipt("reg_1", "key-1", home))

    def test_same_key_with_different_request_facts_conflicts_without_overwrite(self) -> None:
        from no1.kernel.group_bridge.receipts import ReceiptConflictError, get_receipt, record_receipt

        secret = "gbrs_never_echo_this_secret"
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            record_receipt(
                "reg_1", "key-1", {"status": "sent", "remote_event_id": "remote-1"}, home, request_facts=_facts()
            )
            conflicting = _facts(secret)
            with self.assertRaises(ReceiptConflictError) as ctx:
                record_receipt(
                    "reg_1", "key-1", {"status": "sent", "remote_event_id": "remote-2"}, home, request_facts=conflicting
                )
            self.assertNotIn(secret, str(ctx.exception))
            self.assertEqual(get_receipt("reg_1", "key-1", home)["remote_event_id"], "remote-1")
            self.assertNotIn(secret, (home / "group_bridge_receipts.yaml").read_text(encoding="utf-8"))

    def test_concurrent_same_fingerprint_creates_once(self) -> None:
        from no1.kernel.group_bridge.receipts import record_receipt

        with tempfile.TemporaryDirectory() as td:
            home = Path(td)

            results = _run_receipt_processes(home, count=12, same_key=True)
            self.assertEqual({state for state, _ in results}, {"ok"})
            created = [value for _, value in results]
            self.assertEqual(created.count(True), 1)

    def test_concurrent_distinct_keys_preserve_all_receipts(self) -> None:
        from no1.kernel.group_bridge.receipts import load_receipts, record_receipt

        with tempfile.TemporaryDirectory() as td:
            home = Path(td)

            results = _run_receipt_processes(home, count=12, same_key=False)
            self.assertEqual({state for state, _ in results}, {"ok"})
            self.assertEqual(len(load_receipts(home)), 12)

    def test_public_reads_are_deep_copies(self) -> None:
        from no1.kernel.group_bridge.receipts import get_receipt, load_receipts, record_receipt

        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            record_receipt(
                "reg_1", "key-1", {"status": "failed", "error": {"code": "failed", "message": "public"}}, home
            )
            loaded = load_receipts(home)
            fetched = get_receipt("reg_1", "key-1", home)
            loaded["reg_1::key-1"]["error"]["message"] = "mutated-a"
            fetched["error"]["message"] = "mutated-b"
            self.assertEqual(
                get_receipt("reg_1", "key-1", home)["error"]["message"],
                "Remote delivery failed.",
            )

    def test_safe_error_projection_masks_secrets_and_drops_unknown(self) -> None:
        from no1.kernel.group_bridge.receipts import safe_error_projection

        raw = "gbrs_deadbeefdeadbeef"
        projected = safe_error_projection(
            {
                "code": "malicious_code_with_cookie",
                "message": f"auth failed for {raw}",
                "retriable": False,
                "transport": "registry_hub",
                "http_status": 401,
                "internal": {"secret": raw},
            }
        )
        self.assertNotIn(raw, str(projected))
        self.assertNotIn("internal", projected)
        self.assertEqual(projected["code"], "remote_delivery_failed")
        self.assertEqual(projected["message"], "Remote delivery failed.")
        self.assertFalse(projected["retriable"])

    def test_record_and_update_sanitize_errors_and_close_generic_fields(self) -> None:
        from no1.kernel.group_bridge.receipts import get_receipt, record_receipt, update_receipt

        raw = "acc_deadbeefdeadbeef"
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            record_receipt(
                "reg_1",
                "key-1",
                {"status": "failed", "error": {"code": "auth", "message": f"bad {raw}", "internal": raw}},
                home,
            )
            self.assertNotIn(raw, str(get_receipt("reg_1", "key-1", home)))
            with self.assertRaises(ValueError):
                update_receipt("reg_1", "key-1", home, internal_secret=raw)
            with self.assertRaises(ValueError):
                update_receipt("reg_1", "key-1", home, request_fingerprint="replacement")
            self.assertNotIn(raw, (home / "group_bridge_receipts.yaml").read_text(encoding="utf-8"))

    def test_update_transitions_status_under_lock(self) -> None:
        from no1.kernel.group_bridge.receipts import record_receipt, update_receipt

        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            record_receipt("reg_1", "key-1", {"status": "queued"}, home)
            updated = update_receipt("reg_1", "key-1", home, status="sent", remote_event_id="remote-9")
            self.assertEqual(updated["status"], "sent")
            self.assertEqual(updated["remote_event_id"], "remote-9")
            with self.assertRaises(ValueError):
                update_receipt("reg_1", "key-1", home, status="queued")
            self.assertEqual(update_receipt("reg_1", "key-1", home, status="sent")["status"], "sent")

            record_receipt("reg_1", "failed", {"status": "failed"}, home)
            with self.assertRaises(ValueError):
                update_receipt("reg_1", "failed", home, status="retrying")

    def test_create_and_update_reject_invalid_attempt_counts_without_writing(self) -> None:
        from no1.kernel.group_bridge.receipts import ReceiptStoreError, get_receipt, record_receipt, update_receipt

        invalid_creates = (
            {"attempt": -1},
            {"max_attempts": 0},
            {"attempt": 6, "max_attempts": 5},
        )
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            record_receipt("reg_1", "valid", {"status": "queued", "attempt": 2, "max_attempts": 5}, home)
            path = home / "group_bridge_receipts.yaml"
            original = path.read_bytes()
            for index, fields in enumerate(invalid_creates):
                with self.subTest(operation="create", fields=fields):
                    with self.assertRaises(ReceiptStoreError):
                        record_receipt("reg_1", f"invalid-{index}", {"status": "queued", **fields}, home)
                    self.assertEqual(path.read_bytes(), original)

            invalid_updates = (
                {"attempt": -1},
                {"max_attempts": 0},
                {"attempt": 6},
                {"max_attempts": 1},
            )
            for fields in invalid_updates:
                with self.subTest(operation="update", fields=fields):
                    with self.assertRaises(ReceiptStoreError):
                        update_receipt("reg_1", "valid", home, **fields)
                    self.assertEqual(path.read_bytes(), original)
                    self.assertEqual(get_receipt("reg_1", "valid", home)["attempt"], 2)

    def test_request_facts_require_strict_finite_json(self) -> None:
        from no1.kernel.group_bridge.receipts import load_receipts, record_receipt

        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            invalid_payloads = (
                {"value": object()},
                {"value": float("nan")},
                {"value": float("inf")},
                {"value": ("tuple",)},
                {1: "non-string-key"},
            )
            for index, payload in enumerate(invalid_payloads):
                with self.subTest(index=index):
                    with self.assertRaises(ValueError):
                        record_receipt(
                            "reg_1", f"key-{index}", {"status": "queued"}, home, request_facts={"payload": payload}
                        )
            self.assertEqual(load_receipts(home), {})
            with self.assertRaises(ValueError):
                record_receipt("reg_1", "bad-facts", {"status": "queued"}, home, request_facts=["not-an-object"])

    def test_malformed_receipt_store_is_not_overwritten(self) -> None:
        from no1.kernel.group_bridge.receipts import ReceiptStoreError, record_receipt

        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            path = home / "group_bridge_receipts.yaml"
            original = b"receipts: [\n"
            path.write_bytes(original)
            with self.assertRaises(ReceiptStoreError):
                record_receipt("reg_1", "key-1", {"status": "queued"}, home)
            self.assertEqual(path.read_bytes(), original)

    def test_semantically_corrupt_receipt_store_is_not_overwritten(self) -> None:
        from no1.kernel.group_bridge.receipts import ReceiptStoreError, get_receipt, load_receipts, record_receipt

        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            record_receipt("reg_1", "key-1", {"status": "queued"}, home)
            path = home / "group_bridge_receipts.yaml"
            corrupt = path.read_bytes().replace(b"registration_id: reg_1", b"registration_id: wrong", 1)
            path.write_bytes(corrupt)
            with self.assertRaises(ReceiptStoreError):
                record_receipt("reg_2", "key-2", {"status": "queued"}, home)
            self.assertEqual(path.read_bytes(), corrupt)
            self.assertEqual(load_receipts(home), {})
            self.assertIsNone(get_receipt("reg_1", "key-1", home))


if __name__ == "__main__":
    unittest.main()
