import base64
import dataclasses
import json
import multiprocessing
import os
import re
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import yaml
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


def _identity_worker(home: str, results) -> None:
    from no1.daemon.group_bridge.identity import get_group_bridge_identity

    try:
        identity = get_group_bridge_identity(home=Path(home))
        results.put((identity.peer_id, identity.public_key_b64))
    except Exception as exc:
        results.put((type(exc).__name__, ""))


def _address_worker(home: str, index: int, results) -> None:
    from no1.kernel.group_bridge.peer_addresses import record_peer_addresses

    try:
        result = record_peer_addresses(
            "peer-shared",
            [f"/ip4/127.0.0.1/tcp/{10000 + index}"],
            remote_group_id=f"remote-{index}",
            home=Path(home),
        )
        results.put(("ok", result["remote_group_id"]))
    except Exception as exc:
        results.put((type(exc).__name__, ""))


def _invite_worker(home: str, index: int, results) -> None:
    from no1.kernel.group_bridge.pairing import create_pairing_invite

    try:
        invite = create_pairing_invite(group_id=f"group-{index}", home=Path(home))
        results.put(("ok", invite["invite_id"]))
    except Exception as exc:
        results.put((type(exc).__name__, ""))


def _request_worker(home: str, code: str, nonce: str, results) -> None:
    from no1.kernel.group_bridge.pairing import create_pairing_request

    try:
        request = create_pairing_request(
            code,
            client_nonce=nonce,
            requester_group_id="remote-group",
            requester_group_title="Remote Group",
            requester_peer_id="remote-peer",
            requester_endpoint="https://remote.example/",
            requester_multiaddrs=["/dns4/remote.example/tcp/443"],
            home=Path(home),
        )
        results.put(("ok", request["request_id"]))
    except Exception as exc:
        results.put((type(exc).__name__, ""))


def _run_spawn(target, args_list: list[tuple]) -> list[tuple]:
    context = multiprocessing.get_context("spawn")
    results = context.Queue()
    processes = [context.Process(target=target, args=(*args, results)) for args in args_list]
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


class TestGroupBridgeIdentity(unittest.TestCase):
    def test_public_identity_never_projects_private_key_and_signs_internally(self) -> None:
        from no1.daemon.group_bridge.identity import get_group_bridge_identity, sign_group_bridge_payload

        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            identity = get_group_bridge_identity(home=home)
            persisted = yaml.safe_load((home / "group_bridge_identity_key.yaml").read_text(encoding="utf-8"))
            secret = persisted["private_key"]

            self.assertEqual(set(vars(identity)), {"peer_id", "public_key_b64"})
            self.assertEqual(set(dataclasses.asdict(identity)), {"peer_id", "public_key_b64"})
            self.assertNotIn("private", repr(identity).lower())
            self.assertNotIn(secret, repr(identity))
            self.assertNotIn(secret, json.dumps(dataclasses.asdict(identity), sort_keys=True))
            self.assertFalse(hasattr(identity, "sign"))

            payload = b"group-bridge-payload"
            signature = base64.b64decode(sign_group_bridge_payload(payload, home=home), validate=True)
            public_raw = base64.b64decode(identity.public_key_b64, validate=True)
            Ed25519PublicKey.from_public_bytes(public_raw).verify(signature, payload)

    @unittest.skipIf(os.name == "nt", "POSIX file modes are required")
    def test_identity_file_is_0600_and_existing_wide_mode_is_tightened(self) -> None:
        from no1.daemon.group_bridge.identity import get_group_bridge_identity, load_group_bridge_identity

        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            path = home / "group_bridge_identity_key.yaml"
            expected = get_group_bridge_identity(home=home)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            path.chmod(0o644)
            self.assertEqual(load_group_bridge_identity(home=home), expected)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_permission_failure_is_fixed_and_does_not_echo_key(self) -> None:
        from no1.daemon.group_bridge import identity as identity_module

        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            identity_module.get_group_bridge_identity(home=home)
            persisted = yaml.safe_load((home / "group_bridge_identity_key.yaml").read_text(encoding="utf-8"))
            with mock.patch.object(identity_module.os, "chmod", side_effect=OSError("secret=" + persisted["private_key"])):
                with self.assertRaisesRegex(
                    identity_module.GroupBridgeIdentityStoreError,
                    "^Group Bridge identity permissions could not be secured$",
                ) as ctx:
                    identity_module.get_group_bridge_identity(home=home)
            self.assertNotIn(persisted["private_key"], str(ctx.exception))

    def test_malformed_identity_is_not_overwritten(self) -> None:
        from no1.daemon.group_bridge.identity import (
            GroupBridgeIdentityStoreError,
            get_group_bridge_identity,
            load_group_bridge_identity,
            sign_group_bridge_payload,
        )

        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            path = home / "group_bridge_identity_key.yaml"
            path.write_bytes(b"private_key: attacker-secret\nprivate_key: duplicate\n")
            before = path.read_bytes()
            self.assertIsNone(load_group_bridge_identity(home=home))
            for operation in (
                lambda: get_group_bridge_identity(home=home),
                lambda: sign_group_bridge_payload(b"payload", home=home),
            ):
                with self.subTest(operation=operation):
                    with self.assertRaisesRegex(GroupBridgeIdentityStoreError, "^Group Bridge identity store is malformed$"):
                        operation()
                    self.assertEqual(path.read_bytes(), before)

    def test_peer_id_requires_exact_ed25519_public_key_length(self) -> None:
        from no1.daemon.group_bridge.identity import peer_id_from_public_key_b64

        self.assertTrue(peer_id_from_public_key_b64(base64.b64encode(b"x" * 32).decode("ascii")))
        for size in (31, 33):
            value = base64.b64encode(b"x" * size).decode("ascii")
            with self.subTest(size=size):
                with self.assertRaisesRegex(ValueError, "^Group Bridge public key must be a 32-byte Ed25519 key$"):
                    peer_id_from_public_key_b64(value)

    def test_concurrent_first_use_creates_one_identity(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            results = _run_spawn(_identity_worker, [(td,) for _ in range(8)])
            self.assertEqual(len(set(results)), 1)


class TestPeerAddresses(unittest.TestCase):
    def test_same_peer_keeps_exact_group_addresses_without_fallback(self) -> None:
        from no1.kernel.group_bridge.peer_addresses import (
            load_address_book,
            record_peer_addresses,
            resolve_peer_multiaddrs,
        )

        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            record_peer_addresses("peer-a", ["/dns4/a/tcp/1"], remote_group_id="group-a", home=home)
            record_peer_addresses("peer-a", ["/dns4/b/tcp/2"], remote_group_id="group-b", home=home)
            record_peer_addresses("peer-a", ["/dns4/a2/tcp/3"], remote_group_id="group-a", home=home)

            self.assertEqual(resolve_peer_multiaddrs("peer-a", remote_group_id="group-a", home=home), ("/dns4/a2/tcp/3",))
            self.assertEqual(resolve_peer_multiaddrs("peer-a", remote_group_id="group-b", home=home), ("/dns4/b/tcp/2",))
            self.assertEqual(resolve_peer_multiaddrs("peer-a", home=home), ())
            book = load_address_book(home=home)
            self.assertEqual(set(book["peer-a"]), {"group-a", "group-b"})
            book["peer-a"]["group-a"]["multiaddrs"].append("mutated")
            self.assertEqual(resolve_peer_multiaddrs("peer-a", remote_group_id="group-a", home=home), ("/dns4/a2/tcp/3",))

    def test_input_validation_does_not_clean_arbitrary_values(self) -> None:
        from no1.kernel.group_bridge.peer_addresses import record_peer_addresses

        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            invalid = (
                "address-is-not-an-address-list",
                ("/dns4/tuple/tcp/1",),
                {"/dns4/set/tcp/1"},
                {"address": "/dns4/dict/tcp/1"},
                (value for value in ["/dns4/generator/tcp/1"]),
                [""],
                [" padded "],
                [{"address": "value"}],
                [1],
            )
            for multiaddrs in invalid:
                with self.subTest(multiaddrs=multiaddrs):
                    with self.assertRaises(ValueError):
                        record_peer_addresses("peer", multiaddrs, remote_group_id="group", home=home)
            for remote_group_id in ("", " padded ", None):
                with self.subTest(remote_group_id=remote_group_id):
                    with self.assertRaises(ValueError):
                        record_peer_addresses("peer", [], remote_group_id=remote_group_id, home=home)

    def test_concurrent_same_peer_different_groups_preserves_all_records(self) -> None:
        from no1.kernel.group_bridge.peer_addresses import load_address_book

        with tempfile.TemporaryDirectory() as td:
            results = _run_spawn(_address_worker, [(td, index) for index in range(10)])
            self.assertEqual({state for state, _ in results}, {"ok"})
            book = load_address_book(home=Path(td))
            self.assertEqual(set(book["peer-shared"]), {f"remote-{index}" for index in range(10)})

    def test_semantic_corruption_fails_closed_and_preserves_bytes(self) -> None:
        from no1.kernel.group_bridge.peer_addresses import (
            PeerAddressStoreError,
            address_book_path,
            load_address_book,
            record_peer_addresses,
            resolve_peer_multiaddrs,
        )

        cases = (
            {"peers": {"peer": {"group": {"peer_id": "other", "remote_group_id": "group", "multiaddrs": [], "updated_at": "2026-01-01T00:00:00Z"}}}},
            {"peers": {"peer": {"group": {"peer_id": "peer", "remote_group_id": "other", "multiaddrs": [], "updated_at": "2026-01-01T00:00:00Z"}}}},
            {"peers": {"peer": {"group": "bad-entry"}}},
        )
        for raw in cases:
            with self.subTest(raw=raw), tempfile.TemporaryDirectory() as td:
                home = Path(td)
                path = address_book_path(home=home)
                path.parent.mkdir(parents=True)
                path.write_text(json.dumps(raw), encoding="utf-8")
                before = path.read_bytes()
                self.assertEqual(load_address_book(home=home), {})
                self.assertEqual(resolve_peer_multiaddrs("peer", remote_group_id="group", home=home), ())
                with self.assertRaisesRegex(PeerAddressStoreError, "^Group Bridge peer address store is malformed$"):
                    record_peer_addresses("peer", [], remote_group_id="group", home=home)
                self.assertEqual(path.read_bytes(), before)


class TestGroupBridgePairing(unittest.TestCase):
    def _request(
        self,
        home: Path,
        *,
        group_id: str = "local-group",
        remote_group_id: str = "remote-group",
        remote_peer_id: str = "remote-peer",
        endpoint: str = "https://remote.example/",
        nonce: str = "request_nonce_0123456789abcdef",
        multiaddrs: list[str] | None = None,
    ) -> tuple[dict, dict]:
        from no1.kernel.group_bridge.pairing import create_pairing_invite, create_pairing_request

        invite = create_pairing_invite(
            group_id=group_id,
            remote_group_id=remote_group_id,
            remote_peer_id=remote_peer_id,
            home=home,
        )
        request = create_pairing_request(
            invite["pairing_code"],
            client_nonce=nonce,
            requester_group_id=remote_group_id,
            requester_group_title="Remote Group",
            requester_peer_id=remote_peer_id,
            requester_endpoint=endpoint,
            requester_multiaddrs=list(multiaddrs or []),
            home=home,
        )
        return invite, request

    def _claim(self, home: Path, token: str, group_id: str = "local-group"):
        from no1.kernel.access_tokens import issue_access_token_principal_claim

        return issue_access_token_principal_claim(token, group_id=group_id, home=home)

    def test_pairing_code_is_high_entropy_restart_safe_and_never_persisted(self) -> None:
        from no1.kernel.group_bridge import pairing as pairing_module
        from no1.kernel.group_bridge.pairing import (
            create_pairing_invite,
            create_pairing_request,
            get_pairing_invite,
            get_pairing_invite_for_code,
        )

        generated = [pairing_module._new_code() for _ in range(512)]
        self.assertEqual(len(set(generated)), len(generated))
        self.assertTrue(all(re.fullmatch(r"[0-9A-F]{8}(?:-[0-9A-F]{8}){3}", code) for code in generated))

        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            invite = create_pairing_invite(group_id="local-group", home=home)
            raw_code = invite["pairing_code"]
            path = home / "group_bridge_pairing.yaml"
            persisted = path.read_text(encoding="utf-8")
            self.assertNotIn(raw_code, persisted)
            self.assertNotIn("pairing_code", get_pairing_invite(invite["invite_id"], home=home))
            self.assertEqual(get_pairing_invite_for_code(raw_code, home=home)["invite_id"], invite["invite_id"])

            request = create_pairing_request(
                raw_code,
                client_nonce="restart_nonce_0123456789abcdef",
                requester_group_id="remote-group",
                requester_peer_id="remote-peer",
                requester_endpoint="https://remote.example",
                home=home,
            )
            self.assertTrue(request["request_id"])
            self.assertNotIn(raw_code, path.read_text(encoding="utf-8"))

            for invalid in ("ABCD-EFGH", "A" * 32, " " + raw_code, raw_code + " ", object()):
                with self.subTest(invalid=invalid):
                    self.assertIsNone(get_pairing_invite_for_code(invalid, home=home))
                    before = path.read_bytes()
                    with self.assertRaisesRegex(ValueError, "^pairing_code format is invalid$") as ctx:
                        create_pairing_request(
                            invalid,
                            client_nonce="invalid_code_nonce_0123456789",
                            requester_group_id="remote-group",
                            requester_peer_id="remote-peer",
                            home=home,
                        )
                    self.assertNotIn(str(invalid), str(ctx.exception))
                    self.assertEqual(path.read_bytes(), before)

    def test_endpoint_normalization_is_lossless_closed_and_deterministic(self) -> None:
        from no1.kernel.group_bridge import pairing as pairing_module
        from no1.kernel.group_bridge.pairing import create_pairing_invite, create_pairing_request

        equivalents = (
            "HTTPS://REMOTE.EXAMPLE:443/path/",
            "https://remote.example/path",
        )
        self.assertEqual(
            {pairing_module._normalize_remote_endpoint(value) for value in equivalents},
            {"https://remote.example/path"},
        )
        self.assertEqual(
            pairing_module._normalize_remote_endpoint("https://[2001:0DB8:0:0::1]:443/path/"),
            "https://[2001:db8::1]/path",
        )

        invalid = (
            "https://user:pass@remote.example/path",
            "https://remote.example/path?x=1",
            "https://remote.example/path#fragment",
            "https://remote.example/path?",
            "https://remote.example:99999/path",
            "https://remote.example:/path",
            "https://remote.exa\nmple/path",
            "ftp://remote.example/path",
            "https://bad_host.example/path",
            "https://[v1.fe]",
            "https://[fe80::1%25eth0]",
            "https://[fe80::1%eth0]",
        )
        for index, endpoint in enumerate(invalid):
            with self.subTest(endpoint=endpoint), tempfile.TemporaryDirectory() as td:
                home = Path(td)
                invite = create_pairing_invite(group_id="local-group", home=home)
                path = home / "group_bridge_pairing.yaml"
                before = path.read_bytes()
                with self.assertRaisesRegex(ValueError, r"^remote_endpoint must be a normalized HTTP\(S\) endpoint$"):
                    create_pairing_request(
                        invite["pairing_code"],
                        client_nonce=f"endpoint_reject_nonce_{index:02d}_0123456789",
                        requester_group_id="remote-group",
                        requester_peer_id="remote-peer",
                        requester_endpoint=endpoint,
                        home=home,
                    )
                self.assertEqual(path.read_bytes(), before)

    def test_ipv6_request_approve_and_authorize_share_canonical_principal(self) -> None:
        from no1.kernel.access_tokens import create_access_token
        from no1.kernel.group_bridge.pairing import (
            approve_pairing_request,
            authorize_remote_principal,
        )
        from no1.kernel.group_bridge.registration import get_registration

        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            token = create_access_token("member", allowed_groups=["local-group"], home=home)["token"]
            _, request = self._request(home, endpoint="HTTPS://[2001:0DB8::1]:443/path/")
            approved = approve_pairing_request(request["request_id"], claim=self._claim(home, token), home=home)
            trust = approved["trust"]
            registration = get_registration(trust["registration_id"], home)
            self.assertEqual(request["remote_endpoint"], "https://[2001:db8::1]/path")
            self.assertEqual(registration["url"], "https://[2001:db8::1]/path")
            self.assertEqual(
                authorize_remote_principal(
                    group_id="local-group",
                    transport="group_bridge_session",
                    remote_endpoint="https://[2001:db8::1]:443/path/",
                    remote_group_id="remote-group",
                    remote_peer_id="remote-peer",
                    home=home,
                ),
                trust,
            )
            self.assertIsNone(
                authorize_remote_principal(
                    group_id="local-group",
                    transport="group_bridge_session",
                    remote_endpoint="https://[2001:db8::2]/path",
                    remote_group_id="remote-group",
                    remote_peer_id="remote-peer",
                    home=home,
                )
            )

    def test_invite_request_approve_binds_registration_and_defaults_messages(self) -> None:
        from no1.kernel.access_tokens import create_access_token
        from no1.kernel.group_bridge.pairing import approve_pairing_request, get_pairing_request, get_trust
        from no1.kernel.group_bridge.peer_addresses import resolve_peer_multiaddrs
        from no1.kernel.group_bridge.registration import get_registration

        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            token = create_access_token("member", allowed_groups=["local-group"], home=home)["token"]
            invite, request = self._request(
                home,
                endpoint="HTTPS://REMOTE.EXAMPLE:443/",
                multiaddrs=["/dns4/remote.example/tcp/443"],
            )
            persisted = (home / "group_bridge_pairing.yaml").read_text(encoding="utf-8")
            self.assertNotIn(invite["pairing_code"], persisted)
            self.assertIn("pairing_code_hash", persisted)

            approved = approve_pairing_request(request["request_id"], claim=self._claim(home, token), home=home)
            trust = approved["trust"]
            registration = approved["registration"]
            self.assertEqual(trust["access_level"], "messages")
            self.assertEqual(trust["revision"], 1)
            self.assertEqual(trust["approved_by"], "member")
            self.assertEqual(trust["remote_endpoint"], "https://remote.example")
            self.assertEqual(registration["url"], "https://remote.example")
            self.assertEqual(
                (registration["group_id"], registration["transport"], registration["url"], registration["remote_group_id"], registration["remote_peer_id"]),
                ("local-group", "group_bridge_session", "https://remote.example", "remote-group", "remote-peer"),
            )
            self.assertEqual(get_registration(registration["registration_id"], home), registration)
            self.assertEqual(resolve_peer_multiaddrs("remote-peer", remote_group_id="remote-group", home=home), ("/dns4/remote.example/tcp/443",))
            self.assertEqual(get_pairing_request(request["request_id"], home=home)["status"], "approved")
            self.assertEqual(get_trust(trust["trust_id"], home=home), trust)

            replay = approve_pairing_request(request["request_id"], claim=self._claim(home, token), home=home)
            self.assertEqual(replay["registration"]["registration_id"], registration["registration_id"])
            self.assertEqual(replay["trust"], trust)

    def test_endpointless_mixed_case_peer_identity_survives_approval_reload_and_authorize(self) -> None:
        from no1.kernel.access_tokens import create_access_token
        from no1.kernel.group_bridge.pairing import (
            approve_pairing_request,
            authorize_remote_principal,
            create_pairing_invite,
            create_pairing_request,
            get_trust,
        )
        from no1.kernel.group_bridge.registration import get_registration, get_registration_by_target

        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            remote_peer_id = "12D3KooWMixEdBase58Peer"
            registration_url = f"group-bridge-session://peer/{remote_peer_id}"
            token = create_access_token("member", allowed_groups=["local-group"], home=home)["token"]
            invite = create_pairing_invite(group_id="local-group", home=home)
            request = create_pairing_request(
                invite["pairing_code"],
                client_nonce="endpointless_mixed_case_nonce_012345",
                requester_group_id="remote-group",
                requester_peer_id=remote_peer_id,
                requester_endpoint="",
                home=home,
            )

            approved = approve_pairing_request(request["request_id"], claim=self._claim(home, token), home=home)
            trust = approved["trust"]
            registration = approved["registration"]
            self.assertEqual(registration["url"], registration_url)
            self.assertEqual(registration["remote_peer_id"], remote_peer_id)
            self.assertEqual(get_registration(registration["registration_id"], home), registration)
            self.assertEqual(
                get_registration_by_target(
                    registration_url,
                    "local-group",
                    home,
                    transport="group_bridge_session",
                    remote_group_id="remote-group",
                    remote_peer_id=remote_peer_id,
                ),
                registration,
            )
            self.assertEqual(get_trust(trust["trust_id"], home=home), trust)
            self.assertEqual(
                authorize_remote_principal(
                    group_id="local-group",
                    transport="group_bridge_session",
                    remote_endpoint="",
                    remote_group_id="remote-group",
                    remote_peer_id=remote_peer_id,
                    home=home,
                ),
                trust,
            )
            self.assertIsNone(
                authorize_remote_principal(
                    group_id="local-group",
                    transport="group_bridge_session",
                    remote_endpoint="",
                    remote_group_id="remote-group",
                    remote_peer_id=remote_peer_id.lower(),
                    home=home,
                )
            )

    def test_request_nonce_replays_exact_success_and_conflicts_without_leak(self) -> None:
        from no1.kernel.group_bridge.pairing import create_pairing_invite, create_pairing_request, list_pairing_requests

        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            nonce = "opaque_client_nonce_0123456789abcdef"
            invite = create_pairing_invite(group_id="local-group", home=home)
            facts = dict(
                client_nonce=nonce,
                requester_group_id="remote-group",
                requester_group_title="Remote Group",
                requester_peer_id="remote-peer",
                requester_endpoint="HTTPS://REMOTE.EXAMPLE:443/",
                requester_multiaddrs=["/dns4/remote.example/tcp/443"],
                home=home,
            )
            first = create_pairing_request(invite["pairing_code"], **facts)
            replay = create_pairing_request(invite["pairing_code"], **facts)
            self.assertEqual(replay, first)
            self.assertEqual(len(list_pairing_requests(home=home)), 1)
            persisted = (home / "group_bridge_pairing.yaml").read_text(encoding="utf-8")
            self.assertNotIn(nonce, persisted)
            self.assertNotIn(invite["pairing_code"], persisted)

            conflicts = (
                {**facts, "requester_endpoint": "https://other.example"},
                {**facts, "requester_peer_id": "other-peer"},
                {**facts, "requester_multiaddrs": ["/dns4/other.example/tcp/443"]},
                {**facts, "client_nonce": "different_client_nonce_0123456789"},
            )
            for conflict in conflicts:
                with self.subTest(conflict=conflict):
                    before = (home / "group_bridge_pairing.yaml").read_bytes()
                    with self.assertRaisesRegex(ValueError, "^Pairing request conflicts with existing replay facts$") as ctx:
                        create_pairing_request(invite["pairing_code"], **conflict)
                    self.assertNotIn(nonce, str(ctx.exception))
                    self.assertNotIn(invite["pairing_code"], str(ctx.exception))
                    self.assertEqual((home / "group_bridge_pairing.yaml").read_bytes(), before)

    def test_concurrent_same_nonce_creates_one_request(self) -> None:
        from no1.kernel.group_bridge.pairing import create_pairing_invite, list_pairing_requests

        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            invite = create_pairing_invite(group_id="local-group", home=home)
            nonce = "concurrent_nonce_0123456789abcdef"
            results = _run_spawn(_request_worker, [(td, invite["pairing_code"], nonce) for _ in range(10)])
            self.assertEqual({state for state, _ in results}, {"ok"})
            self.assertEqual(len({request_id for _, request_id in results}), 1)
            self.assertEqual(len(list_pairing_requests(home=home)), 1)
            path = home / "group_bridge_pairing.yaml"
            before = path.read_bytes()
            replay = _run_spawn(_request_worker, [(td, invite["pairing_code"], nonce) for _ in range(4)])
            self.assertEqual({request_id for _, request_id in replay}, {next(iter({request_id for _, request_id in results}))})
            self.assertEqual(path.read_bytes(), before)

    def test_concurrent_invites_preserve_all_records(self) -> None:
        from no1.kernel.group_bridge.pairing import get_pairing_invite

        with tempfile.TemporaryDirectory() as td:
            results = _run_spawn(_invite_worker, [(td, index) for index in range(12)])
            self.assertEqual({state for state, _ in results}, {"ok"})
            ids = {invite_id for _, invite_id in results}
            self.assertEqual(len(ids), 12)
            self.assertTrue(all(get_pairing_invite(invite_id, home=Path(td)) is not None for invite_id in ids))

    def test_exact_endpoint_is_part_of_remote_principal(self) -> None:
        from no1.kernel.group_bridge.pairing import create_pairing_invite, create_pairing_request, list_pairing_requests

        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            _, first = self._request(home, endpoint="https://one.example", nonce="endpoint_nonce_one_0123456789")
            _, second = self._request(home, endpoint="https://two.example", nonce="endpoint_nonce_two_0123456789")
            self.assertNotEqual(first["request_id"], second["request_id"])
            self.assertEqual(len(list_pairing_requests(home=home)), 2)

            self._request(
                home,
                remote_group_id="other-group",
                remote_peer_id="other-peer",
                endpoint="https://three.example",
                nonce="endpoint_nonce_three_01234567",
            )
            invite = create_pairing_invite(
                group_id="local-group",
                remote_group_id="other-group",
                remote_peer_id="other-peer",
                home=home,
            )
            with self.assertRaisesRegex(ValueError, "remote principal already has a pairing request"):
                create_pairing_request(
                    invite["pairing_code"],
                    client_nonce="endpoint_nonce_four_012345678",
                    requester_group_id="other-group",
                    requester_peer_id="other-peer",
                    requester_endpoint="https://three.example/",
                    home=home,
                )

    def test_live_claim_is_required_and_stale_or_wrong_group_has_zero_side_effects(self) -> None:
        from no1.kernel.access_tokens import AccessTokenClaimStaleError, create_access_token, update_access_token
        from no1.kernel.group_bridge.pairing import PairingAuthorizationError, approve_pairing_request
        from no1.kernel.group_bridge.registration import list_registrations

        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            _, request = self._request(home)
            pairing_path = home / "group_bridge_pairing.yaml"
            before = pairing_path.read_bytes()
            with self.assertRaisesRegex(PairingAuthorizationError, "^A live access token principal claim is required$"):
                approve_pairing_request(request["request_id"], claim={"user_id": "forged"}, home=home)
            self.assertEqual(pairing_path.read_bytes(), before)

            token = create_access_token("member", allowed_groups=["local-group"], home=home)["token"]
            stale = self._claim(home, token)
            update_access_token(token, allowed_groups=["local-group", "other"], home=home)
            with self.assertRaises(AccessTokenClaimStaleError):
                approve_pairing_request(request["request_id"], claim=stale, home=home)
            self.assertEqual(pairing_path.read_bytes(), before)

            admin = create_access_token("admin", is_admin=True, home=home)["token"]
            wrong_group = self._claim(home, admin, group_id="other-group")
            with self.assertRaises(PairingAuthorizationError):
                approve_pairing_request(request["request_id"], claim=wrong_group, home=home)
            self.assertEqual(pairing_path.read_bytes(), before)
            self.assertEqual(list_registrations(home), [])

    def test_claim_home_mismatch_rejects_before_any_pairing_or_registration_write(self) -> None:
        from no1.kernel.access_tokens import AccessTokenClaimHomeMismatchError, create_access_token
        from no1.kernel.group_bridge.pairing import reject_pairing_request

        with tempfile.TemporaryDirectory() as first_td, tempfile.TemporaryDirectory() as second_td:
            first_home = Path(first_td)
            second_home = Path(second_td)
            token = create_access_token("member", allowed_groups=["local-group"], home=first_home)["token"]
            _, request = self._request(second_home)
            first_pairing = first_home / "group_bridge_pairing.yaml"
            second_pairing = second_home / "group_bridge_pairing.yaml"
            before = (first_pairing.exists(), second_pairing.read_bytes())

            with self.assertRaisesRegex(
                AccessTokenClaimHomeMismatchError,
                "^Access token principal claim does not authorize this home$",
            ):
                reject_pairing_request(
                    request["request_id"],
                    claim=self._claim(first_home, token),
                    home=second_home,
                )
            self.assertEqual((first_pairing.exists(), second_pairing.read_bytes()), before)
            self.assertFalse((first_home / "group_bridge_registrations.yaml").exists())
            self.assertFalse((second_home / "group_bridge_registrations.yaml").exists())

    @unittest.skipIf(os.name == "nt", "symlink retargeting is POSIX-specific")
    def test_claim_canonical_home_survives_alias_retarget_during_approval(self) -> None:
        from no1.kernel.access_tokens import create_access_token
        from no1.kernel.group_bridge import pairing as pairing_module
        from no1.kernel.group_bridge.pairing import approve_pairing_request

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            first_home = root / "first"
            second_home = root / "second"
            first_home.mkdir()
            second_home.mkdir()
            alias = root / "active-home"
            alias.symlink_to(first_home, target_is_directory=True)
            token = create_access_token("member", allowed_groups=["local-group"], home=first_home)["token"]
            _, request = self._request(first_home)
            claim = self._claim(first_home, token)
            original = pairing_module._approve_pairing_request
            observed_homes: list[Path] = []

            def retarget_then_approve(request_id, principal, *, home):
                observed_homes.append(home)
                alias.unlink()
                alias.symlink_to(second_home, target_is_directory=True)
                return original(request_id, principal, home=home)

            with mock.patch.object(pairing_module, "_approve_pairing_request", side_effect=retarget_then_approve):
                approved = approve_pairing_request(request["request_id"], claim=claim, home=alias)

            self.assertEqual(observed_homes, [first_home.resolve()])
            self.assertEqual(approved["status"], "approved")
            self.assertTrue((first_home / "group_bridge_pairing.yaml").exists())
            self.assertTrue((first_home / "group_bridge_registrations.yaml").exists())
            self.assertFalse((second_home / "group_bridge_pairing.yaml").exists())
            self.assertFalse((second_home / "group_bridge_registrations.yaml").exists())
            self.assertFalse((second_home / "group_bridge_credentials.yaml").exists())

    def test_reject_is_claim_gated_terminal_and_creates_no_trust(self) -> None:
        from no1.kernel.access_tokens import create_access_token
        from no1.kernel.group_bridge.pairing import (
            approve_pairing_request,
            get_pairing_invite,
            list_trusts,
            reject_pairing_request,
        )
        from no1.kernel.group_bridge.registration import list_registrations

        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            token = create_access_token("member", allowed_groups=["local-group"], home=home)["token"]
            invite, request = self._request(home)
            rejected = reject_pairing_request(
                request["request_id"],
                claim=self._claim(home, token),
                reason="not approved",
                home=home,
            )
            self.assertEqual((rejected["status"], rejected["rejected_by"], rejected["rejection_reason"]), ("rejected", "member", "not approved"))
            self.assertEqual(list_trusts(home=home), [])
            self.assertEqual(list_registrations(home), [])
            self.assertEqual(get_pairing_invite(invite["invite_id"], home=home)["status"], "requested")
            replay = reject_pairing_request(request["request_id"], claim=self._claim(home, token), home=home)
            self.assertEqual(replay, rejected)
            with self.assertRaisesRegex(ValueError, "^pairing request is terminal$"):
                approve_pairing_request(request["request_id"], claim=self._claim(home, token), home=home)

    def test_request_expiry_blocks_approval_before_and_after_registration_phase(self) -> None:
        from no1.kernel.access_tokens import create_access_token
        from no1.kernel.group_bridge import pairing as pairing_module
        from no1.kernel.group_bridge.pairing import (
            approve_pairing_request,
            create_pairing_invite,
            create_pairing_request,
            get_pairing_invite,
            get_pairing_request,
            list_trusts,
        )
        from no1.kernel.group_bridge.peer_addresses import (
            address_book_path,
            record_peer_addresses,
            resolve_peer_multiaddrs,
        )
        from no1.kernel.group_bridge.registration import list_registrations

        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            token = create_access_token("member", allowed_groups=["local-group"], home=home)["token"]
            record_peer_addresses(
                "existing-peer",
                ["/dns4/existing.example/tcp/443"],
                remote_group_id="existing-group",
                home=home,
            )
            address_path = address_book_path(home=home)
            address_before = address_path.read_bytes()
            invite = create_pairing_invite(group_id="local-group", ttl_seconds=60, home=home)
            request = create_pairing_request(
                invite["pairing_code"],
                client_nonce="expiry_before_nonce_0123456789",
                requester_group_id="remote-group",
                requester_peer_id="remote-peer",
                requester_endpoint="https://remote.example",
                requester_multiaddrs=["/dns4/remote.example/tcp/443"],
                home=home,
            )
            expiry = pairing_module.parse_utc_iso(request["expires_at"])
            with mock.patch.object(pairing_module, "_now", return_value=expiry + pairing_module.timedelta(seconds=1)):
                self.assertEqual(get_pairing_request(request["request_id"], home=home)["status"], "expired")
                with self.assertRaisesRegex(ValueError, "^pairing request expired$"):
                    approve_pairing_request(request["request_id"], claim=self._claim(home, token), home=home)
            self.assertEqual(get_pairing_request(request["request_id"], home=home)["status"], "expired")
            self.assertEqual(get_pairing_invite(invite["invite_id"], home=home)["status"], "requested")
            self.assertEqual(list_trusts(home=home), [])
            self.assertEqual(list_registrations(home), [])
            self.assertEqual(address_path.read_bytes(), address_before)
            self.assertEqual(resolve_peer_multiaddrs("remote-peer", remote_group_id="remote-group", home=home), ())

        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            token = create_access_token("member", allowed_groups=["local-group"], home=home)["token"]
            remote_peer_id = "12D3KooWMixEdExpiryPeer"
            record_peer_addresses(
                "existing-peer",
                ["/dns4/existing.example/tcp/443"],
                remote_group_id="existing-group",
                home=home,
            )
            address_path = address_book_path(home=home)
            address_before = address_path.read_bytes()
            invite = create_pairing_invite(group_id="local-group", ttl_seconds=60, home=home)
            request = create_pairing_request(
                invite["pairing_code"],
                client_nonce="expiry_phase_nonce_0123456789ab",
                requester_group_id="remote-group",
                requester_peer_id=remote_peer_id,
                requester_endpoint="",
                requester_multiaddrs=["/dns4/remote.example/tcp/443"],
                home=home,
            )
            expiry = pairing_module.parse_utc_iso(request["expires_at"])
            clock = [expiry - pairing_module.timedelta(seconds=1)]
            original_upsert = pairing_module.upsert_registration

            def upsert_then_expire(*args, **kwargs):
                registration = original_upsert(*args, **kwargs)
                clock[0] = expiry + pairing_module.timedelta(seconds=1)
                return registration

            with mock.patch.object(pairing_module, "_now", side_effect=lambda: clock[0]), mock.patch.object(
                pairing_module,
                "upsert_registration",
                side_effect=upsert_then_expire,
            ):
                with self.assertRaisesRegex(ValueError, "^pairing request expired$"):
                    approve_pairing_request(request["request_id"], claim=self._claim(home, token), home=home)
            self.assertEqual(get_pairing_request(request["request_id"], home=home)["status"], "expired")
            self.assertEqual(list_trusts(home=home), [])
            self.assertEqual(list_registrations(home), [])
            self.assertEqual(address_path.read_bytes(), address_before)
            self.assertEqual(resolve_peer_multiaddrs(remote_peer_id, remote_group_id="remote-group", home=home), ())

    def test_cancel_and_request_race_has_one_terminal_winner(self) -> None:
        from no1.kernel.access_tokens import create_access_token
        from no1.kernel.group_bridge.pairing import (
            cancel_pairing_invite,
            create_pairing_invite,
            create_pairing_request,
            get_pairing_invite,
            list_pairing_requests,
        )

        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            token = create_access_token("member", allowed_groups=["local-group"], home=home)["token"]
            invite = create_pairing_invite(group_id="local-group", home=home)
            barrier = threading.Barrier(2)
            outcomes: list[str] = []

            def cancel() -> None:
                barrier.wait(timeout=5)
                try:
                    cancel_pairing_invite(invite["invite_id"], claim=self._claim(home, token), home=home)
                    outcomes.append("cancelled")
                except ValueError:
                    outcomes.append("cancel-conflict")

            def request() -> None:
                barrier.wait(timeout=5)
                try:
                    create_pairing_request(
                        invite["pairing_code"],
                        client_nonce="cancel_race_nonce_0123456789ab",
                        requester_group_id="remote-group",
                        requester_peer_id="remote-peer",
                        requester_endpoint="https://remote.example",
                        home=home,
                    )
                    outcomes.append("requested")
                except ValueError:
                    outcomes.append("request-conflict")

            threads = [threading.Thread(target=cancel), threading.Thread(target=request)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=10)
                self.assertFalse(thread.is_alive())
            self.assertEqual(sum(outcome in {"cancelled", "requested"} for outcome in outcomes), 1)
            final_invite = get_pairing_invite(invite["invite_id"], home=home)
            requests = list_pairing_requests(home=home)
            if "cancelled" in outcomes:
                self.assertEqual(final_invite["status"], "cancelled")
                self.assertEqual(requests, [])
            else:
                self.assertEqual(final_invite["status"], "requested")
                self.assertEqual(len(requests), 1)

    def test_access_level_uses_revision_cas_and_full_requires_admin(self) -> None:
        from no1.kernel.access_tokens import create_access_token
        from no1.kernel.group_bridge.pairing import (
            PairingAuthorizationError,
            PairingStoreError,
            approve_pairing_request,
            get_trust,
            update_trust_access_level,
        )

        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            member = create_access_token("member", allowed_groups=["local-group"], home=home)["token"]
            admin = create_access_token("admin", is_admin=True, home=home)["token"]
            _, request = self._request(home)
            trust = approve_pairing_request(request["request_id"], claim=self._claim(home, member), home=home)["trust"]
            updated = update_trust_access_level(
                trust["trust_id"],
                "read",
                expected_revision=trust["revision"],
                claim=self._claim(home, member),
                home=home,
            )
            self.assertEqual((updated["access_level"], updated["revision"]), ("read", 2))
            before = (home / "group_bridge_pairing.yaml").read_bytes()
            with self.assertRaisesRegex(PairingStoreError, "^Trust revision does not match$"):
                update_trust_access_level(
                    trust["trust_id"],
                    "messages",
                    expected_revision=1,
                    claim=self._claim(home, member),
                    home=home,
                )
            self.assertEqual((home / "group_bridge_pairing.yaml").read_bytes(), before)
            with self.assertRaises(PairingAuthorizationError):
                update_trust_access_level(
                    trust["trust_id"],
                    "full",
                    expected_revision=2,
                    claim=self._claim(home, member),
                    home=home,
                )
            full = update_trust_access_level(
                trust["trust_id"],
                "full",
                expected_revision=2,
                claim=self._claim(home, admin),
                home=home,
            )
            self.assertEqual((full["access_level"], full["revision"], full["access_updated_by"]), ("full", 3, "admin"))
            self.assertEqual(get_trust(trust["trust_id"], home=home), full)

    def test_authorize_requires_exact_active_principal_registration_and_access(self) -> None:
        from no1.kernel.access_tokens import create_access_token
        from no1.kernel.group_bridge.pairing import (
            approve_pairing_request,
            authorize_remote_principal,
            update_trust_access_level,
        )
        from no1.kernel.group_bridge.registration import delete_registration, upsert_registration

        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            member = create_access_token("member", allowed_groups=["local-group"], home=home)["token"]
            _, request = self._request(home)
            trust = approve_pairing_request(request["request_id"], claim=self._claim(home, member), home=home)["trust"]
            exact = dict(
                group_id="local-group",
                transport="group_bridge_session",
                remote_endpoint="HTTPS://REMOTE.EXAMPLE:443/",
                remote_group_id="remote-group",
                remote_peer_id="remote-peer",
                home=home,
            )

            authorized = authorize_remote_principal(**exact)
            self.assertEqual(authorized, trust)
            self.assertNotIn("registration_fingerprint", authorized)
            self.assertNotIn("credential", authorized)
            self.assertIsNone(authorize_remote_principal(**exact, required_access_level="read"))
            for changed in (
                {"group_id": "other-local"},
                {"transport": "registry_hub"},
                {"remote_endpoint": "https://other.example"},
                {"remote_group_id": "other-remote"},
                {"remote_peer_id": "other-peer"},
            ):
                with self.subTest(changed=changed):
                    self.assertIsNone(authorize_remote_principal(**{**exact, **changed}))

            read = update_trust_access_level(
                trust["trust_id"],
                "read",
                expected_revision=trust["revision"],
                claim=self._claim(home, member),
                home=home,
            )
            self.assertEqual(authorize_remote_principal(**exact, required_access_level="read"), read)
            self.assertIsNone(authorize_remote_principal(**exact, required_access_level="full"))

            delete_registration(trust["registration_id"], home)
            replacement = upsert_registration(
                "local-group",
                "https://remote.example",
                transport="group_bridge_session",
                remote_group_id="remote-group",
                remote_peer_id="remote-peer",
                status="active",
                home=home,
                _approved_by_pairing=True,
            )
            self.assertNotEqual(replacement["registration_id"], trust["registration_id"])
            self.assertIsNone(authorize_remote_principal(**exact))

            upsert_registration(
                "local-group",
                "https://remote.example",
                transport="group_bridge_session",
                remote_group_id="remote-group",
                remote_peer_id="remote-peer",
                status="revoked",
                home=home,
                _approved_by_pairing=True,
            )
            self.assertIsNone(authorize_remote_principal(**exact))

    def test_concurrent_same_revision_allows_one_access_transition(self) -> None:
        from no1.kernel.access_tokens import create_access_token
        from no1.kernel.group_bridge.pairing import approve_pairing_request, get_trust, update_trust_access_level

        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            token = create_access_token("member", allowed_groups=["local-group"], home=home)["token"]
            _, request = self._request(home)
            trust = approve_pairing_request(request["request_id"], claim=self._claim(home, token), home=home)["trust"]
            claims = [self._claim(home, token), self._claim(home, token)]
            barrier = threading.Barrier(2)
            results: list[str] = []

            def update(level: str, claim) -> None:
                barrier.wait(timeout=5)
                try:
                    update_trust_access_level(
                        trust["trust_id"],
                        level,
                        expected_revision=1,
                        claim=claim,
                        home=home,
                    )
                    results.append("ok")
                except Exception as exc:
                    results.append(type(exc).__name__)

            threads = [threading.Thread(target=update, args=(level, claim)) for level, claim in zip(("read", "messages"), claims)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=10)
                self.assertFalse(thread.is_alive())
            self.assertEqual(results.count("ok"), 1)
            self.assertEqual(results.count("PairingStoreError"), 1)
            self.assertEqual(get_trust(trust["trust_id"], home=home)["revision"], 2)

    def test_revoke_cuts_authority_and_cleans_credential_before_registration(self) -> None:
        from no1.kernel.access_tokens import create_access_token
        from no1.kernel.group_bridge import pairing as pairing_module
        from no1.kernel.group_bridge.credentials import get_group_bridge_credential, save_pairing_bearer_token
        from no1.kernel.group_bridge.pairing import (
            approve_pairing_request,
            authorize_remote_principal,
            get_pairing_invite,
            get_pairing_request,
            get_trust,
            revoke_trust,
            update_trust_access_level,
        )
        from no1.kernel.group_bridge.registration import get_registration, upsert_registration

        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            token = create_access_token("member", allowed_groups=["local-group"], home=home)["token"]
            invite, request = self._request(home)
            trust = approve_pairing_request(request["request_id"], claim=self._claim(home, token), home=home)["trust"]
            registration = get_registration(trust["registration_id"], home)
            ref = save_pairing_bearer_token(
                local_group_id="local-group",
                remote_group_id="remote-group",
                remote_endpoint=registration["url"],
                token="bearer-secret-material",
                home=home,
            )
            upsert_registration(
                "local-group",
                registration["url"],
                transport="group_bridge_session",
                remote_group_id="remote-group",
                remote_peer_id="remote-peer",
                credential_ref=ref,
                user_id="member",
                status="active",
                home=home,
                _approved_by_pairing=True,
            )
            order: list[str] = []
            delete_credential = pairing_module.delete_group_bridge_credential
            delete_registration = pairing_module.delete_registration

            with mock.patch.object(
                pairing_module,
                "delete_group_bridge_credential",
                side_effect=lambda credential_ref, home=None: (order.append("credential"), delete_credential(credential_ref, home=home))[1],
            ), mock.patch.object(
                pairing_module,
                "delete_registration",
                side_effect=lambda registration_id, home=None: (order.append("registration"), delete_registration(registration_id, home))[1],
            ):
                revoked = revoke_trust(
                    trust["trust_id"],
                    expected_revision=trust["revision"],
                    claim=self._claim(home, token),
                    home=home,
                )
            self.assertEqual(order, ["credential", "registration"])
            self.assertEqual((revoked["status"], revoked["revision"], revoked["revoked_by"]), ("revoked", 3, "member"))
            self.assertIsNone(get_registration(trust["registration_id"], home))
            self.assertIsNone(get_group_bridge_credential(ref, home=home))
            self.assertEqual(get_trust(trust["trust_id"], home=home), revoked)
            self.assertEqual(get_pairing_request(request["request_id"], home=home)["status"], "approved")
            self.assertEqual(get_pairing_invite(invite["invite_id"], home=home)["status"], "requested")
            self.assertIsNone(
                authorize_remote_principal(
                    group_id="local-group",
                    transport="group_bridge_session",
                    remote_endpoint="https://remote.example",
                    remote_group_id="remote-group",
                    remote_peer_id="remote-peer",
                    home=home,
                )
            )
            with self.assertRaises(ValueError):
                update_trust_access_level(
                    trust["trust_id"],
                    "read",
                    expected_revision=revoked["revision"],
                    claim=self._claim(home, token),
                    home=home,
                )
            replay = revoke_trust(
                trust["trust_id"],
                expected_revision=trust["revision"],
                claim=self._claim(home, token),
                home=home,
            )
            self.assertEqual(replay, revoked)

    def test_approval_phase_is_non_authorizing_and_retries_monotonically(self) -> None:
        from no1.kernel.access_tokens import create_access_token
        from no1.kernel.group_bridge import pairing as pairing_module
        from no1.kernel.group_bridge.pairing import approve_pairing_request, get_pairing_request, list_trusts

        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            initiator = create_access_token("initiator", allowed_groups=["local-group"], home=home)["token"]
            helper = create_access_token("helper", allowed_groups=["local-group"], home=home)["token"]
            _, request = self._request(home)
            original = pairing_module.upsert_registration
            with mock.patch.object(pairing_module, "upsert_registration", side_effect=RuntimeError("provider failed")):
                with self.assertRaisesRegex(RuntimeError, "provider failed"):
                    approve_pairing_request(request["request_id"], claim=self._claim(home, initiator), home=home)
            phase = get_pairing_request(request["request_id"], home=home)
            self.assertEqual((phase["status"], phase["approved_by"]), ("approving", "initiator"))
            self.assertEqual(list_trusts(home=home), [])
            with mock.patch.object(pairing_module, "upsert_registration", side_effect=original):
                approved = approve_pairing_request(request["request_id"], claim=self._claim(home, helper), home=home)
            self.assertEqual(approved["status"], "approved")
            self.assertEqual(approved["request"]["approved_by"], "initiator")
            self.assertEqual(approved["trust"]["approved_by"], "initiator")
            self.assertEqual(approved["registration"]["user_id"], "initiator")

    def test_revocation_cleanup_failure_stays_non_active_and_retries(self) -> None:
        from no1.kernel.access_tokens import create_access_token
        from no1.kernel.group_bridge import pairing as pairing_module
        from no1.kernel.group_bridge.credentials import get_group_bridge_credential, save_pairing_bearer_token
        from no1.kernel.group_bridge.pairing import PairingStoreError, approve_pairing_request, get_trust, revoke_trust
        from no1.kernel.group_bridge.registration import get_registration, upsert_registration

        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            initiator = create_access_token("initiator", allowed_groups=["local-group"], home=home)["token"]
            helper = create_access_token("helper", allowed_groups=["local-group"], home=home)["token"]
            _, request = self._request(home)
            trust = approve_pairing_request(request["request_id"], claim=self._claim(home, initiator), home=home)["trust"]
            registration = get_registration(trust["registration_id"], home)
            ref = save_pairing_bearer_token(
                local_group_id="local-group",
                remote_group_id="remote-group",
                remote_endpoint=registration["url"],
                token="cleanup-retry-secret",
                home=home,
            )
            other_ref = save_pairing_bearer_token(
                local_group_id="local-group",
                remote_group_id="remote-group",
                remote_endpoint=registration["url"],
                token="other-cleanup-secret",
                home=home,
            )
            upsert_registration(
                "local-group",
                registration["url"],
                transport="group_bridge_session",
                remote_group_id="remote-group",
                remote_peer_id="remote-peer",
                credential_ref=ref,
                user_id="initiator",
                home=home,
                _approved_by_pairing=True,
            )
            with mock.patch.object(pairing_module, "delete_group_bridge_credential", side_effect=RuntimeError("cleanup failed")):
                with self.assertRaisesRegex(RuntimeError, "cleanup failed"):
                    revoke_trust(
                        trust["trust_id"],
                        expected_revision=trust["revision"],
                        claim=self._claim(home, initiator),
                        home=home,
                    )
            phase = get_trust(trust["trust_id"], home=home)
            self.assertEqual((phase["status"], phase["revision"]), ("revoking", 2))
            self.assertEqual(phase["revoked_by"], "initiator")
            upsert_registration(
                "local-group",
                registration["url"],
                transport="group_bridge_session",
                remote_group_id="remote-group",
                remote_peer_id="remote-peer",
                credential_ref=other_ref,
                user_id="initiator",
                home=home,
                _approved_by_pairing=True,
            )
            pairing_before = (home / "group_bridge_pairing.yaml").read_bytes()
            with self.assertRaisesRegex(PairingStoreError, "^Trust cleanup facts changed during revocation$"):
                revoke_trust(
                    trust["trust_id"],
                    expected_revision=trust["revision"],
                    claim=self._claim(home, helper),
                    home=home,
                )
            self.assertEqual((home / "group_bridge_pairing.yaml").read_bytes(), pairing_before)
            self.assertIsNotNone(get_group_bridge_credential(ref, home=home))
            self.assertIsNotNone(get_group_bridge_credential(other_ref, home=home))
            upsert_registration(
                "local-group",
                registration["url"],
                transport="group_bridge_session",
                remote_group_id="remote-group",
                remote_peer_id="remote-peer",
                credential_ref=ref,
                user_id="initiator",
                home=home,
                _approved_by_pairing=True,
            )
            revoked = revoke_trust(
                trust["trust_id"],
                expected_revision=trust["revision"],
                claim=self._claim(home, helper),
                home=home,
            )
            self.assertEqual((revoked["status"], revoked["revision"]), ("revoked", 3))
            self.assertEqual(revoked["revoked_by"], "initiator")

    def test_revocation_phase_initiator_tampering_fails_closed(self) -> None:
        from no1.kernel.access_tokens import create_access_token
        from no1.kernel.group_bridge import pairing as pairing_module
        from no1.kernel.group_bridge.credentials import save_pairing_bearer_token
        from no1.kernel.group_bridge.pairing import (
            PairingStoreError,
            approve_pairing_request,
            create_pairing_invite,
            list_trusts,
            revoke_trust,
        )
        from no1.kernel.group_bridge.registration import get_registration, upsert_registration

        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            token = create_access_token("initiator", allowed_groups=["local-group"], home=home)["token"]
            _, request = self._request(home)
            trust = approve_pairing_request(request["request_id"], claim=self._claim(home, token), home=home)["trust"]
            registration = get_registration(trust["registration_id"], home)
            ref = save_pairing_bearer_token(
                local_group_id="local-group",
                remote_group_id="remote-group",
                remote_endpoint=registration["url"],
                token="phase-identity-secret",
                home=home,
            )
            upsert_registration(
                "local-group",
                registration["url"],
                transport="group_bridge_session",
                remote_group_id="remote-group",
                remote_peer_id="remote-peer",
                credential_ref=ref,
                user_id="initiator",
                home=home,
                _approved_by_pairing=True,
            )
            with mock.patch.object(pairing_module, "delete_group_bridge_credential", side_effect=RuntimeError("stop phase")):
                with self.assertRaisesRegex(RuntimeError, "^stop phase$"):
                    revoke_trust(
                        trust["trust_id"],
                        expected_revision=trust["revision"],
                        claim=self._claim(home, token),
                        home=home,
                    )

            path = home / "group_bridge_pairing.yaml"
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
            document["trusts"][trust["trust_id"]]["revoked_by"] = "replacement-user"
            path.write_text(yaml.safe_dump(document, sort_keys=True), encoding="utf-8")
            before = path.read_bytes()
            self.assertEqual(list_trusts(home=home), [])
            with self.assertRaisesRegex(PairingStoreError, "^Group Bridge pairing store is malformed$"):
                create_pairing_invite(group_id="other-local", home=home)
            self.assertEqual(path.read_bytes(), before)

    def test_revocation_rejects_registration_credential_for_other_principal(self) -> None:
        from no1.kernel.access_tokens import create_access_token
        from no1.kernel.group_bridge.credentials import get_group_bridge_credential, save_pairing_bearer_token
        from no1.kernel.group_bridge.pairing import PairingStoreError, approve_pairing_request, get_trust, revoke_trust
        from no1.kernel.group_bridge.registration import get_registration, upsert_registration

        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            token = create_access_token("initiator", allowed_groups=["local-group"], home=home)["token"]
            _, request = self._request(home)
            trust = approve_pairing_request(request["request_id"], claim=self._claim(home, token), home=home)["trust"]
            registration = get_registration(trust["registration_id"], home)
            wrong_ref = save_pairing_bearer_token(
                local_group_id="local-group",
                remote_group_id="other-remote-group",
                remote_endpoint=registration["url"],
                token="other-principal-secret",
                home=home,
            )
            upsert_registration(
                "local-group",
                registration["url"],
                transport="group_bridge_session",
                remote_group_id="remote-group",
                remote_peer_id="remote-peer",
                credential_ref=wrong_ref,
                user_id="initiator",
                home=home,
                _approved_by_pairing=True,
            )

            with self.assertRaisesRegex(PairingStoreError, "^Trust cleanup facts changed during revocation$"):
                revoke_trust(
                    trust["trust_id"],
                    expected_revision=trust["revision"],
                    claim=self._claim(home, token),
                    home=home,
                )
            phase = get_trust(trust["trust_id"], home=home)
            self.assertEqual(phase["status"], "revoking")
            pairing_before = (home / "group_bridge_pairing.yaml").read_bytes()
            registration_before = get_registration(trust["registration_id"], home)
            with self.assertRaisesRegex(PairingStoreError, "^Trust cleanup facts changed during revocation$"):
                revoke_trust(
                    trust["trust_id"],
                    expected_revision=trust["revision"],
                    claim=self._claim(home, token),
                    home=home,
                )
            self.assertEqual((home / "group_bridge_pairing.yaml").read_bytes(), pairing_before)
            self.assertEqual(get_registration(trust["registration_id"], home), registration_before)
            self.assertIsNotNone(get_group_bridge_credential(wrong_ref, home=home))

    def test_revocation_cleanup_ref_tampering_has_zero_external_deletes(self) -> None:
        from no1.kernel.access_tokens import create_access_token
        from no1.kernel.group_bridge import pairing as pairing_module
        from no1.kernel.group_bridge.credentials import get_group_bridge_credential, save_pairing_bearer_token
        from no1.kernel.group_bridge.pairing import PairingStoreError, approve_pairing_request, list_trusts, revoke_trust
        from no1.kernel.group_bridge.registration import get_registration, upsert_registration

        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            token = create_access_token("initiator", allowed_groups=["local-group"], home=home)["token"]
            _, request = self._request(home)
            trust = approve_pairing_request(request["request_id"], claim=self._claim(home, token), home=home)["trust"]
            registration = get_registration(trust["registration_id"], home)
            intended_ref = save_pairing_bearer_token(
                local_group_id="local-group",
                remote_group_id="remote-group",
                remote_endpoint=registration["url"],
                token="intended-cleanup-secret",
                home=home,
            )
            other_ref = save_pairing_bearer_token(
                local_group_id="local-group",
                remote_group_id="remote-group",
                remote_endpoint=registration["url"],
                token="unrelated-cleanup-secret",
                home=home,
            )
            upsert_registration(
                "local-group",
                registration["url"],
                transport="group_bridge_session",
                remote_group_id="remote-group",
                remote_peer_id="remote-peer",
                credential_ref=intended_ref,
                user_id="initiator",
                home=home,
                _approved_by_pairing=True,
            )
            with mock.patch.object(pairing_module, "delete_group_bridge_credential", side_effect=RuntimeError("stop phase")):
                with self.assertRaisesRegex(RuntimeError, "^stop phase$"):
                    revoke_trust(
                        trust["trust_id"],
                        expected_revision=trust["revision"],
                        claim=self._claim(home, token),
                        home=home,
                    )

            path = home / "group_bridge_pairing.yaml"
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
            document["trusts"][trust["trust_id"]]["cleanup_credential_ref"] = other_ref
            path.write_text(yaml.safe_dump(document, sort_keys=True), encoding="utf-8")
            before = path.read_bytes()
            registration_before = get_registration(trust["registration_id"], home)
            self.assertEqual(list_trusts(home=home), [])
            with self.assertRaisesRegex(PairingStoreError, "^Group Bridge pairing store is malformed$") as ctx:
                revoke_trust(
                    trust["trust_id"],
                    expected_revision=trust["revision"],
                    claim=self._claim(home, token),
                    home=home,
                )
            self.assertNotIn(intended_ref, str(ctx.exception))
            self.assertNotIn(other_ref, str(ctx.exception))
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(get_registration(trust["registration_id"], home), registration_before)
            self.assertIsNotNone(get_group_bridge_credential(intended_ref, home=home))
            self.assertIsNotNone(get_group_bridge_credential(other_ref, home=home))

    def test_revocation_final_response_loss_replays_original_cas_without_writes(self) -> None:
        from no1.kernel.access_tokens import create_access_token
        from no1.kernel.group_bridge import pairing as pairing_module
        from no1.kernel.group_bridge.pairing import approve_pairing_request, revoke_trust
        from no1.kernel.group_bridge.registration import list_registrations

        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            token = create_access_token("member", allowed_groups=["local-group"], home=home)["token"]
            _, request = self._request(home)
            trust = approve_pairing_request(request["request_id"], claim=self._claim(home, token), home=home)["trust"]
            original_project = pairing_module._project_trust

            def lose_terminal_response(record):
                if record["status"] == "revoked":
                    raise RuntimeError("response lost")
                return original_project(record)

            with mock.patch.object(pairing_module, "_project_trust", side_effect=lose_terminal_response):
                with self.assertRaisesRegex(RuntimeError, "^response lost$"):
                    revoke_trust(
                        trust["trust_id"],
                        expected_revision=trust["revision"],
                        claim=self._claim(home, token),
                        home=home,
                    )

            pairing_path = home / "group_bridge_pairing.yaml"
            before = pairing_path.read_bytes()
            self.assertEqual(list_registrations(home), [])
            replay = revoke_trust(
                trust["trust_id"],
                expected_revision=trust["revision"],
                claim=self._claim(home, token),
                home=home,
            )
            self.assertEqual((replay["status"], replay["revision"]), ("revoked", trust["revision"] + 2))
            self.assertEqual(pairing_path.read_bytes(), before)
            self.assertEqual(list_registrations(home), [])

    def test_access_and_revoke_same_revision_have_one_legal_winner(self) -> None:
        from no1.kernel.access_tokens import create_access_token
        from no1.kernel.group_bridge.pairing import approve_pairing_request, get_trust, revoke_trust, update_trust_access_level

        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            token = create_access_token("member", allowed_groups=["local-group"], home=home)["token"]
            _, request = self._request(home)
            trust = approve_pairing_request(request["request_id"], claim=self._claim(home, token), home=home)["trust"]
            claims = [self._claim(home, token), self._claim(home, token)]
            barrier = threading.Barrier(2)
            results: list[str] = []

            def update() -> None:
                barrier.wait(timeout=5)
                try:
                    update_trust_access_level(
                        trust["trust_id"],
                        "read",
                        expected_revision=1,
                        claim=claims[0],
                        home=home,
                    )
                    results.append("access")
                except Exception as exc:
                    results.append(type(exc).__name__)

            def revoke() -> None:
                barrier.wait(timeout=5)
                try:
                    revoke_trust(
                        trust["trust_id"],
                        expected_revision=1,
                        claim=claims[1],
                        home=home,
                    )
                    results.append("revoke")
                except Exception as exc:
                    results.append(type(exc).__name__)

            threads = [threading.Thread(target=update), threading.Thread(target=revoke)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=10)
                self.assertFalse(thread.is_alive())
            self.assertEqual(sum(result in {"access", "revoke"} for result in results), 1)
            current = get_trust(trust["trust_id"], home=home)
            self.assertIn(current["status"], {"active", "revoked"})
            self.assertNotEqual(current["revision"], 1)

    def test_public_inputs_require_closed_strings_lists_and_ttl(self) -> None:
        from no1.kernel.group_bridge.pairing import create_pairing_invite, create_pairing_request

        invalid_invites = (
            {"group_id": {"group": "id"}},
            {"group_id": "local", "remote_group_id": 1},
            {"group_id": "local", "remote_peer_id": object()},
            {"group_id": "local", "multiaddrs": "address"},
            {"group_id": "local", "multiaddrs": [1]},
            {"group_id": "local", "ttl_seconds": "600"},
            {"group_id": "local", "ttl_seconds": 59},
        )
        for kwargs in invalid_invites:
            with self.subTest(kwargs=kwargs), tempfile.TemporaryDirectory() as td:
                home = Path(td)
                with self.assertRaises(ValueError):
                    create_pairing_invite(home=home, **kwargs)
                self.assertFalse((home / "group_bridge_pairing.yaml").exists())

        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            invite = create_pairing_invite(group_id="local", home=home)
            before = (home / "group_bridge_pairing.yaml").read_bytes()
            invalid_requests = (
                {"client_nonce": "short", "requester_group_id": "remote", "requester_peer_id": "peer"},
                {"client_nonce": "n" * 24, "requester_group_id": {}, "requester_peer_id": "peer"},
                {"client_nonce": "n" * 24, "requester_group_id": "remote", "requester_peer_id": object()},
                {"client_nonce": "n" * 24, "requester_group_id": "remote", "requester_peer_id": "peer", "requester_endpoint": {}},
                {"client_nonce": "n" * 24, "requester_group_id": "remote", "requester_peer_id": "peer", "requester_multiaddrs": "address"},
                {"client_nonce": "n" * 24, "requester_group_id": "remote", "requester_peer_id": "peer", "requester_multiaddrs": [1]},
            )
            for kwargs in invalid_requests:
                with self.subTest(kwargs=kwargs):
                    with self.assertRaises(ValueError):
                        create_pairing_request(invite["pairing_code"], home=home, **kwargs)
                    self.assertEqual((home / "group_bridge_pairing.yaml").read_bytes(), before)

    def test_pairing_store_corruption_fails_closed_and_mutations_preserve_bytes(self) -> None:
        from no1.kernel.group_bridge.pairing import PairingStoreError, create_pairing_invite, list_pairing_requests, list_trusts

        corrupt = (
            b"version: 1\ninvites: {}\ninvites: {}\nrequests: {}\ntrusts: {}\n",
            b"version: 1\ninvites:\n  1: {}\nrequests: {}\ntrusts: {}\n",
            b"version: 1\ninvites:\n  bad: not-a-record\nrequests: {}\ntrusts: {}\n",
        )
        for raw in corrupt:
            with self.subTest(raw=raw), tempfile.TemporaryDirectory() as td:
                home = Path(td)
                path = home / "group_bridge_pairing.yaml"
                path.write_bytes(raw)
                self.assertEqual(list_pairing_requests(home=home), [])
                self.assertEqual(list_trusts(home=home), [])
                with self.assertRaisesRegex(PairingStoreError, "^Group Bridge pairing store is malformed$"):
                    create_pairing_invite(group_id="local", home=home)
                self.assertEqual(path.read_bytes(), raw)

    def test_persisted_version_ttl_and_nonce_invariants_are_closed(self) -> None:
        from no1.kernel.group_bridge import pairing as pairing_module
        from no1.kernel.group_bridge.pairing import PairingStoreError, create_pairing_invite, create_pairing_request, list_pairing_requests

        def assert_rejected(home: Path, document: dict) -> None:
            path = home / "group_bridge_pairing.yaml"
            path.write_text(yaml.safe_dump(document, sort_keys=True), encoding="utf-8")
            before = path.read_bytes()
            self.assertEqual(list_pairing_requests(home=home), [])
            with self.assertRaisesRegex(PairingStoreError, "^Group Bridge pairing store is malformed$"):
                create_pairing_invite(group_id="other-local", home=home)
            self.assertEqual(path.read_bytes(), before)

        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            invite = create_pairing_invite(group_id="local", home=home)
            valid = yaml.safe_load((home / "group_bridge_pairing.yaml").read_text(encoding="utf-8"))
            version_bool = json.loads(json.dumps(valid))
            version_bool["version"] = True
            assert_rejected(home, version_bool)

            ttl_short = json.loads(json.dumps(valid))
            record = ttl_short["invites"][invite["invite_id"]]
            created = pairing_module.parse_utc_iso(record["created_at"])
            record["expires_at"] = pairing_module._timestamp(created + pairing_module.timedelta(seconds=1))
            assert_rejected(home, ttl_short)

            orphaned = json.loads(json.dumps(valid))
            orphaned_invite = orphaned["invites"][invite["invite_id"]]
            orphaned_invite["status"] = "requested"
            orphaned_invite["request_id"] = "preq_0123456789abcdef"
            assert_rejected(home, orphaned)

        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            first_invite, _ = self._request(
                home,
                remote_group_id="remote-a",
                remote_peer_id="peer-a",
                nonce="nonce_unique_first_0123456789",
            )
            _ = first_invite
            self._request(
                home,
                remote_group_id="remote-b",
                remote_peer_id="peer-b",
                nonce="nonce_unique_second_012345678",
            )
            valid = yaml.safe_load((home / "group_bridge_pairing.yaml").read_text(encoding="utf-8"))
            request_ids = list(valid["requests"])
            duplicate = json.loads(json.dumps(valid))
            first, second = (duplicate["requests"][request_id] for request_id in request_ids)
            second["client_nonce_hash"] = first["client_nonce_hash"]
            second["request_fingerprint"] = pairing_module._request_fingerprint(second)
            assert_rejected(home, duplicate)

            mismatch = json.loads(json.dumps(valid))
            first, second = (mismatch["requests"][request_id] for request_id in request_ids)
            first["client_nonce_hash"], second["client_nonce_hash"] = second["client_nonce_hash"], first["client_nonce_hash"]
            assert_rejected(home, mismatch)


if __name__ == "__main__":
    unittest.main()
