import multiprocessing
import tempfile
import unittest
from pathlib import Path

import yaml


def _registration_worker(home: str, index: int, same_principal: bool, results) -> None:
    from no1.kernel.group_bridge.registration import upsert_registration

    suffix = "same" if same_principal else str(index)
    try:
        record = upsert_registration(
            "local",
            "https://remote.example",
            transport="group_bridge_session",
            remote_group_id=f"remote-{suffix}",
            remote_peer_id=f"peer-{suffix}",
            credential_ref="gbsec_remote_send_0123456789abcdef01234567",
            home=Path(home),
            _approved_by_pairing=True,
        )
        results.put(("ok", record["registration_id"]))
    except Exception as exc:
        results.put(("error", type(exc).__name__))


def _credential_worker(home: str, index: int, same_principal: bool, results) -> None:
    from no1.kernel.group_bridge.credentials import create_pairing_remote_send_credential

    request_id = "same" if same_principal else f"request-{index}"
    try:
        created = create_pairing_remote_send_credential(
            group_id="local",
            remote_group_id="remote",
            remote_peer_id="peer",
            request_id=request_id,
            home=Path(home),
        )
        results.put(("ok", created))
    except Exception as exc:
        results.put(("error", {"type": type(exc).__name__}))


def _run_processes(target, home: Path, *, count: int, same_principal: bool):
    context = multiprocessing.get_context("spawn")
    results = context.Queue()
    processes = [context.Process(target=target, args=(str(home), index, same_principal, results)) for index in range(count)]
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


class TestGroupBridgeRegistration(unittest.TestCase):
    def test_normalize_url(self) -> None:
        from no1.kernel.group_bridge.registration import normalize_url

        self.assertEqual(normalize_url("HTTPS://Hub.Example.com:443/api/"), "https://hub.example.com/api")
        self.assertEqual(normalize_url("http://hub.example.com:80/"), "http://hub.example.com")
        self.assertEqual(
            normalize_url("HTTPS://[2001:0DB8:0:0:0:0:0:1]:443/api/"),
            "https://[2001:db8::1]/api",
        )
        self.assertEqual(
            normalize_url("http://[2001:db8::1]:80/"),
            "http://[2001:db8::1]",
        )
        self.assertEqual(
            normalize_url("https://[2001:db8::1]:8443/api/"),
            "https://[2001:db8::1]:8443/api",
        )
        self.assertNotEqual(
            normalize_url("https://[2001:db8::1]:8443/"),
            normalize_url("https://[2001:db8::1:8443]/"),
        )

    def test_ipv6_session_upsert_reload_and_target_lookup_share_one_natural_key(self) -> None:
        from no1.kernel.group_bridge.registration import (
            get_registration_by_target,
            load_registrations,
            upsert_registration,
        )

        common = dict(
            transport="group_bridge_session",
            remote_group_id="remote-group",
            remote_peer_id="remote-peer",
            _approved_by_pairing=True,
        )
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            first = upsert_registration(
                "local-group",
                "HTTPS://[2001:0DB8:0:0:0:0:0:1]:443/api/",
                home=home,
                **common,
            )
            replay = upsert_registration(
                "local-group",
                "https://[2001:db8::1]/api",
                home=home,
                **common,
            )
            other = upsert_registration(
                "local-group",
                "https://[2001:db8::1:8443]/api",
                home=home,
                **common,
            )

            self.assertEqual(first["registration_id"], replay["registration_id"])
            self.assertEqual(first["url"], "https://[2001:db8::1]/api")
            self.assertNotEqual(first["registration_id"], other["registration_id"])
            loaded = load_registrations(home)
            self.assertEqual(set(loaded), {first["registration_id"], other["registration_id"]})
            found = get_registration_by_target(
                "https://[2001:0db8::1]:443/api/",
                "local-group",
                home,
                transport="group_bridge_session",
                remote_group_id="remote-group",
                remote_peer_id="remote-peer",
            )
            self.assertEqual(found, replay)

    def test_rejects_raw_secret_without_echo_or_file(self) -> None:
        from no1.kernel.group_bridge.registration import list_registrations, upsert_registration

        secrets = (
            "acc_deadbeefdeadbeef",
            "ghp_1234567890abcdef1234567890abcdef123456",
            "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1c2VyIn0.signature",
            "bearer-token-like-long-secret",
            "gbsec_" + "x" * 4096,
            "gbsec_pairing_0123456789abcdef0123456!",
        )
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            for raw in secrets:
                with self.subTest(raw=raw):
                    with self.assertRaises(ValueError) as ctx:
                        upsert_registration("g1", "https://hub.example", credential_ref=raw, home=home)
                    self.assertNotIn(raw, str(ctx.exception))
            self.assertEqual(list_registrations(home), [])
            path = home / "group_bridge_registrations.yaml"
            if path.exists():
                content = path.read_text(encoding="utf-8")
                for raw in secrets:
                    self.assertNotIn(raw, content)

    def test_credential_ref_accepts_generated_and_bounded_legacy_shapes(self) -> None:
        from no1.kernel.group_bridge.registration import is_valid_credential_ref

        self.assertTrue(is_valid_credential_ref("gbsec_pairing_0123456789abcdef01234567"))
        self.assertTrue(is_valid_credential_ref("gbsec_remote_send_0123456789abcdef01234567"))
        self.assertTrue(is_valid_credential_ref("sec_existing-reference_1"))
        self.assertTrue(is_valid_credential_ref("fsec_pairing_existing"))
        self.assertFalse(is_valid_credential_ref("sec_" + "x" * 97))
        self.assertFalse(is_valid_credential_ref("gbsec_remote_send_0123456789abcdef0123456!"))

    def test_session_natural_key_binds_complete_remote_principal(self) -> None:
        from no1.kernel.group_bridge.registration import list_registrations, upsert_registration

        common = dict(
            transport="group_bridge_session",
            credential_ref="gbsec_remote_send_0123456789abcdef01234567",
            _approved_by_pairing=True,
        )
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            first = upsert_registration(
                "local", "https://remote.example", remote_group_id="remote-a", remote_peer_id="peer-a", home=home, **common
            )
            replay = upsert_registration(
                "local", "HTTPS://REMOTE.EXAMPLE/", remote_group_id="remote-a", remote_peer_id="peer-a", home=home, **common
            )
            other_group = upsert_registration(
                "local", "https://remote.example", remote_group_id="remote-b", remote_peer_id="peer-a", home=home, **common
            )
            other_peer = upsert_registration(
                "local", "https://remote.example", remote_group_id="remote-a", remote_peer_id="peer-b", home=home, **common
            )
            self.assertEqual(first["registration_id"], replay["registration_id"])
            self.assertEqual(len({first["registration_id"], other_group["registration_id"], other_peer["registration_id"]}), 3)
            self.assertEqual(len(list_registrations(home)), 3)

    def test_concurrent_exact_session_upsert_creates_one_record(self) -> None:
        from no1.kernel.group_bridge.registration import list_registrations, upsert_registration

        with tempfile.TemporaryDirectory() as td:
            home = Path(td)

            results = _run_processes(_registration_worker, home, count=12, same_principal=True)
            self.assertEqual({state for state, _ in results}, {"ok"})
            ids = [registration_id for _, registration_id in results]
            self.assertEqual(len(set(ids)), 1)
            self.assertEqual(len(list_registrations(home)), 1)

    def test_concurrent_distinct_principals_preserve_every_record(self) -> None:
        from no1.kernel.group_bridge.registration import list_registrations, upsert_registration

        with tempfile.TemporaryDirectory() as td:
            home = Path(td)

            results = _run_processes(_registration_worker, home, count=12, same_principal=False)
            self.assertEqual({state for state, _ in results}, {"ok"})
            ids = [registration_id for _, registration_id in results]
            self.assertEqual(len(set(ids)), 12)
            self.assertEqual(len(list_registrations(home)), 12)

    def test_same_registry_key_with_different_immutable_facts_conflicts(self) -> None:
        from no1.kernel.group_bridge.registration import RegistrationConflictError, get_registration, upsert_registration

        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            original = upsert_registration(
                "g1", "https://hub.example", remote_group_id="remote-a", credential_ref="sec_a", home=home
            )
            with self.assertRaises(RegistrationConflictError) as ctx:
                upsert_registration(
                    "g1", "https://hub.example", remote_group_id="remote-b", credential_ref="sec_b", home=home
                )
            self.assertNotIn("remote-a", str(ctx.exception))
            self.assertNotIn("remote-b", str(ctx.exception))
            self.assertEqual(get_registration(original["registration_id"], home)["credential_ref"], "sec_a")

    def test_public_reads_are_deep_copies(self) -> None:
        from no1.kernel.group_bridge.registration import get_registration, list_registrations, load_registrations, upsert_registration

        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            record = upsert_registration("g1", "https://hub.example", multiaddrs=["addr-1"], home=home)
            rid = record["registration_id"]
            loaded = load_registrations(home)
            listed = list_registrations(home)
            fetched = get_registration(rid, home)
            loaded[rid]["multiaddrs"].append("mutated-a")
            listed[0]["multiaddrs"].append("mutated-b")
            fetched["multiaddrs"].append("mutated-c")
            self.assertEqual(get_registration(rid, home)["multiaddrs"], ["addr-1"])

    def test_generated_credential_token_is_returned_once_and_safe_reads_hide_it(self) -> None:
        from no1.kernel.group_bridge.credentials import (
            create_pairing_remote_send_credential,
            get_group_bridge_credential,
            list_group_bridge_credentials,
            lookup_pairing_remote_send_credential,
            resolve_pairing_remote_send_token,
        )

        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            facts = dict(group_id="g1", remote_group_id="g2", remote_peer_id="peer", request_id="req", home=home)
            created = create_pairing_remote_send_credential(**facts)
            self.assertTrue(created["created"])
            token = created["token"]
            ref = created["credential_ref"]
            replay = create_pairing_remote_send_credential(**facts)
            self.assertEqual(replay, {"credential_ref": ref, "created": False})
            self.assertEqual(
                resolve_pairing_remote_send_token(
                    ref,
                    expected_group_id="g1",
                    expected_remote_group_id="g2",
                    expected_remote_peer_id="peer",
                    expected_request_id="req",
                    home=home,
                ),
                token,
            )
            self.assertEqual(
                resolve_pairing_remote_send_token(
                    ref,
                    expected_group_id="other",
                    expected_remote_group_id="g2",
                    expected_remote_peer_id="peer",
                    expected_request_id="req",
                    home=home,
                ),
                "",
            )
            projections = [get_group_bridge_credential(ref, home=home), lookup_pairing_remote_send_credential(token, home=home)]
            projections.extend(list_group_bridge_credentials(home=home))
            for projection in projections:
                self.assertNotIn("token", projection)
                self.assertNotIn(token, str(projection))

    def test_pairing_bearer_requires_and_resolves_exact_principal(self) -> None:
        from no1.kernel.group_bridge.credentials import resolve_group_bridge_credential, save_pairing_bearer_token

        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            with self.assertRaises(ValueError):
                save_pairing_bearer_token(
                    local_group_id="", remote_group_id="remote", remote_endpoint="https://remote", token="secret", home=home
                )
            ref = save_pairing_bearer_token(
                local_group_id="local",
                remote_group_id="remote",
                remote_endpoint="https://remote",
                token="secret",
                home=home,
            )
            expected = dict(
                expected_local_group_id="local",
                expected_remote_group_id="remote",
                expected_remote_endpoint="https://remote",
                home=home,
            )
            self.assertEqual(resolve_group_bridge_credential(ref, **expected), "secret")
            self.assertEqual(resolve_group_bridge_credential(ref, **{**expected, "expected_remote_group_id": "other"}), "")

    def test_pairing_resolver_rejects_semantically_corrupt_records(self) -> None:
        from no1.kernel.group_bridge.credentials import (
            CredentialStoreError,
            get_group_bridge_credential,
            list_group_bridge_credentials,
            resolve_group_bridge_credential,
            save_pairing_bearer_token,
        )

        cases = ("mismatched_ref", "unknown_kind", "mismatched_field")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as td:
                home = Path(td)
                ref = save_pairing_bearer_token(
                    local_group_id="local",
                    remote_group_id="remote",
                    remote_endpoint="https://remote",
                    token="attacker-controlled-secret",
                    home=home,
                )
                expected = dict(
                    expected_local_group_id="local",
                    expected_remote_group_id="remote",
                    expected_remote_endpoint="https://remote",
                    home=home,
                )
                path = home / "group_bridge_credentials.yaml"
                content = path.read_text(encoding="utf-8")
                candidate_ref = ref
                if case == "mismatched_ref":
                    candidate_ref = "gbsec_pairing_" + "0" * 24
                    if candidate_ref == ref:
                        candidate_ref = "gbsec_pairing_" + "1" * 24
                    content = content.replace(ref, candidate_ref)
                elif case == "unknown_kind":
                    content = content.replace("kind: bearer", "kind: unknown")
                else:
                    content = content.replace("remote_group_id: remote", "remote_group_id: other")
                    expected["expected_remote_group_id"] = "other"
                path.write_text(content, encoding="utf-8")
                corrupt = path.read_bytes()
                self.assertEqual(resolve_group_bridge_credential(candidate_ref, **expected), "")
                self.assertIsNone(get_group_bridge_credential(candidate_ref, home=home))
                self.assertEqual(list_group_bridge_credentials(home=home), [])
                with self.assertRaises(CredentialStoreError):
                    save_pairing_bearer_token(
                        local_group_id="new-local",
                        remote_group_id="new-remote",
                        remote_endpoint="https://new-remote",
                        token="new-secret",
                        home=home,
                    )
                self.assertEqual(path.read_bytes(), corrupt)

    def test_remote_send_resolver_rejects_semantically_corrupt_records(self) -> None:
        from no1.kernel.group_bridge.credentials import (
            create_pairing_remote_send_credential,
            get_group_bridge_credential,
            list_group_bridge_credentials,
            lookup_pairing_remote_send_credential,
            resolve_pairing_remote_send_token,
        )

        cases = ("mismatched_ref", "unknown_kind", "mismatched_field")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as td:
                home = Path(td)
                created = create_pairing_remote_send_credential(
                    group_id="local", remote_group_id="remote", remote_peer_id="peer", request_id="request", home=home
                )
                ref = created["credential_ref"]
                token = created["token"]
                candidate_ref = ref
                expected_remote_group_id = "remote"
                path = home / "group_bridge_credentials.yaml"
                content = path.read_text(encoding="utf-8")
                if case == "mismatched_ref":
                    candidate_ref = "gbsec_remote_send_" + "0" * 24
                    if candidate_ref == ref:
                        candidate_ref = "gbsec_remote_send_" + "1" * 24
                    content = content.replace(ref, candidate_ref)
                elif case == "unknown_kind":
                    content = content.replace("kind: remote_send", "kind: unknown")
                else:
                    content = content.replace("remote_group_id: remote", "remote_group_id: other")
                    expected_remote_group_id = "other"
                path.write_text(content, encoding="utf-8")
                self.assertEqual(
                    resolve_pairing_remote_send_token(
                        candidate_ref,
                        expected_group_id="local",
                        expected_remote_group_id=expected_remote_group_id,
                        expected_remote_peer_id="peer",
                        expected_request_id="request",
                        home=home,
                    ),
                    "",
                )
                self.assertIsNone(lookup_pairing_remote_send_credential(token, home=home))
                self.assertIsNone(get_group_bridge_credential(candidate_ref, home=home))
                self.assertEqual(list_group_bridge_credentials(home=home), [])

    def test_credential_cross_process_rmw_and_one_time_token(self) -> None:
        from no1.kernel.group_bridge.credentials import list_group_bridge_credentials

        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            same = _run_processes(_credential_worker, home, count=8, same_principal=True)
            self.assertEqual({state for state, _ in same}, {"ok"})
            created = [result for _, result in same]
            self.assertEqual(sum(bool(result["created"]) for result in created), 1)
            self.assertEqual(sum("token" in result for result in created), 1)
            self.assertEqual(len(list_group_bridge_credentials(home=home)), 1)

            distinct = _run_processes(_credential_worker, home, count=8, same_principal=False)
            self.assertEqual({state for state, _ in distinct}, {"ok"})
            self.assertEqual(len(list_group_bridge_credentials(home=home)), 9)

    def test_malformed_registration_store_is_not_overwritten(self) -> None:
        from no1.kernel.group_bridge.registration import RegistrationStoreError, upsert_registration

        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            path = home / "group_bridge_registrations.yaml"
            original = b"registrations: [\n"
            path.write_bytes(original)
            with self.assertRaises(RegistrationStoreError):
                upsert_registration("g1", "https://hub.example", home=home)
            self.assertEqual(path.read_bytes(), original)

    def test_semantically_corrupt_registration_store_is_not_overwritten(self) -> None:
        from no1.kernel.group_bridge.registration import (
            RegistrationStoreError,
            get_registration,
            list_registrations,
            load_registrations,
            upsert_registration,
        )

        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            record = upsert_registration("g1", "https://hub.example", home=home)
            path = home / "group_bridge_registrations.yaml"
            corrupt = path.read_bytes().replace(b"registration_id: reg_", b"registration_id: wrong_", 1)
            path.write_bytes(corrupt)
            with self.assertRaises(RegistrationStoreError):
                upsert_registration("g2", "https://other.example", home=home)
            self.assertEqual(path.read_bytes(), corrupt)
            self.assertEqual(load_registrations(home), {})
            self.assertEqual(list_registrations(home), [])
            self.assertIsNone(get_registration(record["registration_id"], home))

    def test_malformed_credential_store_is_not_overwritten(self) -> None:
        from no1.kernel.group_bridge.credentials import CredentialStoreError, create_pairing_remote_send_credential

        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            path = home / "group_bridge_credentials.yaml"
            original = b"credentials: [\n"
            path.write_bytes(original)
            with self.assertRaises(CredentialStoreError):
                create_pairing_remote_send_credential(
                    group_id="g1", remote_group_id="g2", remote_peer_id="peer", request_id="req", home=home
                )
            self.assertEqual(path.read_bytes(), original)

    def test_semantically_corrupt_credential_store_is_not_overwritten(self) -> None:
        from no1.kernel.group_bridge.credentials import (
            CredentialStoreError,
            create_pairing_remote_send_credential,
            get_group_bridge_credential,
            list_group_bridge_credentials,
        )

        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            created = create_pairing_remote_send_credential(
                group_id="g1", remote_group_id="g2", remote_peer_id="peer", request_id="req", home=home
            )
            path = home / "group_bridge_credentials.yaml"
            corrupt = path.read_bytes().replace(b"kind: remote_send", b"kind: unknown", 1)
            path.write_bytes(corrupt)
            with self.assertRaises(CredentialStoreError):
                create_pairing_remote_send_credential(
                    group_id="g1", remote_group_id="g3", remote_peer_id="peer", request_id="other", home=home
                )
            self.assertEqual(path.read_bytes(), corrupt)
            self.assertIsNone(get_group_bridge_credential(created["credential_ref"], home=home))
            self.assertEqual(list_group_bridge_credentials(home=home), [])

    def test_public_reads_reject_mixed_invalid_records_and_key_collisions(self) -> None:
        from no1.kernel.group_bridge.credentials import (
            _load_unlocked as load_credentials_unlocked,
            create_pairing_remote_send_credential,
            list_group_bridge_credentials,
        )
        from no1.kernel.group_bridge.receipts import load_receipts, record_receipt
        from no1.kernel.group_bridge.registration import list_registrations, upsert_registration

        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            create_pairing_remote_send_credential(
                group_id="g1", remote_group_id="g2", remote_peer_id="peer", request_id="request", home=home
            )
            credential_path = home / "group_bridge_credentials.yaml"
            credential_path.write_text(
                credential_path.read_text(encoding="utf-8") + "  invalid_record: 1\n",
                encoding="utf-8",
            )
            self.assertEqual(list_group_bridge_credentials(home=home), [])

            upsert_registration("g1", "https://hub.example", home=home)
            registration_path = home / "group_bridge_registrations.yaml"
            registration_path.write_text(
                registration_path.read_text(encoding="utf-8") + "  invalid_record: 1\n",
                encoding="utf-8",
            )
            self.assertEqual(list_registrations(home), [])

            record_receipt("reg_1", "key-1", {"status": "queued"}, home)
            receipt_path = home / "group_bridge_receipts.yaml"
            receipt_path.write_text(
                receipt_path.read_text(encoding="utf-8") + "  invalid_record: 1\n",
                encoding="utf-8",
            )
            self.assertEqual(load_receipts(home), {})

        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            create_pairing_remote_send_credential(
                group_id="g1", remote_group_id="g2", remote_peer_id="peer", request_id="request", home=home
            )
            credential_path = home / "group_bridge_credentials.yaml"
            raw = yaml.safe_load(credential_path.read_text(encoding="utf-8"))
            record = next(iter(raw["credentials"].values()))
            credential_path.write_text(
                yaml.safe_dump({"credentials": {1: record, "1": record}}, sort_keys=False),
                encoding="utf-8",
            )
            self.assertEqual(load_credentials_unlocked(home), {})
            self.assertEqual(list_group_bridge_credentials(home=home), [])


if __name__ == "__main__":
    unittest.main()
