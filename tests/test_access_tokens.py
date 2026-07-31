import copy
import json
import multiprocessing
import os
import pickle
import tempfile
import threading
import unittest
from pathlib import Path

import yaml


_STAMP = "2026-01-01T00:00:00Z"


def _persisted_entry(
    user_id: str,
    *,
    groups: list[str] | None = None,
    is_admin: bool = False,
    created_at: str = _STAMP,
    updated_at: str = _STAMP,
) -> dict:
    return {
        "user_id": user_id,
        "allowed_groups": [] if is_admin else list(groups or []),
        "is_admin": is_admin,
        "created_at": created_at,
        "updated_at": updated_at,
    }


def _create_worker(home: str, index: int, custom_token: str, results) -> None:
    from no1.kernel.access_tokens import create_access_token

    try:
        entry = create_access_token(
            f"user-{index}",
            allowed_groups=[f"group-{index}"],
            custom_token=custom_token or None,
            home=Path(home),
        )
        results.put(("ok", entry["token"]))
    except Exception as exc:
        results.put((type(exc).__name__, ""))


def _update_worker(home: str, token: str, group_id: str, results) -> None:
    from no1.kernel.access_tokens import update_access_token

    try:
        entry = update_access_token(token, allowed_groups=[group_id], home=Path(home))
        results.put(("ok", entry["allowed_groups"] if entry else None))
    except Exception as exc:
        results.put((type(exc).__name__, None))


def _delete_worker(home: str, token: str, results) -> None:
    from no1.kernel.access_tokens import delete_access_token

    try:
        results.put(("ok", delete_access_token(token, home=Path(home))))
    except Exception as exc:
        results.put((type(exc).__name__, False))


def _gated_delete_worker(home: str, token: str, gate, attempted, done, results) -> None:
    from no1.kernel.access_tokens import delete_access_token

    gate.wait(10)
    attempted.set()
    try:
        results.put(("ok", delete_access_token(token, home=Path(home))))
    except Exception as exc:
        results.put((type(exc).__name__, False))
    finally:
        done.set()


def _gated_update_worker(home: str, token: str, gate, attempted, done, results) -> None:
    from no1.kernel.access_tokens import update_access_token

    gate.wait(10)
    attempted.set()
    try:
        entry = update_access_token(token, allowed_groups=["group-b"], home=Path(home))
        results.put(("ok", entry["allowed_groups"] if entry else None))
    except Exception as exc:
        results.put((type(exc).__name__, None))
    finally:
        done.set()


def _run_processes(target, args_list: list[tuple]) -> list[tuple]:
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


class TestAccessTokens(unittest.TestCase):
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

    def test_create_lookup_list_delete_access_token(self) -> None:
        from no1.kernel.access_tokens import (
            create_access_token,
            delete_access_token,
            list_access_tokens,
            lookup_access_token,
        )

        _, cleanup = self._with_home()
        try:
            created = create_access_token("user-a", allowed_groups=["g1", "g1", "g2"], is_admin=False)
            token = str(created.get("token") or "")

            self.assertTrue(token.startswith("acc_"))
            self.assertEqual(str(created.get("user_id") or ""), "user-a")
            self.assertEqual(created.get("allowed_groups"), ["g1", "g2"])

            looked_up = lookup_access_token(token)
            self.assertIsNotNone(looked_up)
            assert looked_up is not None
            self.assertEqual(str(looked_up.get("user_id") or ""), "user-a")
            self.assertEqual(looked_up.get("allowed_groups"), ["g1", "g2"])

            listed = list_access_tokens()
            self.assertEqual(len(listed), 1)
            self.assertEqual(str(listed[0].get("token") or ""), token)

            self.assertTrue(delete_access_token(token))
            self.assertIsNone(lookup_access_token(token))
            self.assertEqual(list_access_tokens(), [])
        finally:
            cleanup()

    def test_create_access_token_requires_user_id(self) -> None:
        from no1.kernel.access_tokens import create_access_token

        _, cleanup = self._with_home()
        try:
            with self.assertRaises(ValueError):
                create_access_token("")
        finally:
            cleanup()

    def test_public_reads_are_deep_copies(self) -> None:
        from no1.kernel.access_tokens import create_access_token, list_access_tokens, load_access_tokens, lookup_access_token

        home, cleanup = self._with_home()
        try:
            token = create_access_token("user-a", allowed_groups=["g1"], home=home)["token"]
            loaded = load_access_tokens(home)
            listed = list_access_tokens(home)
            looked_up = lookup_access_token(token, home)
            loaded[token]["allowed_groups"].append("bad-a")
            listed[0]["allowed_groups"].append("bad-b")
            assert looked_up is not None
            looked_up["allowed_groups"].append("bad-c")
            self.assertEqual(lookup_access_token(token, home)["allowed_groups"], ["g1"])
        finally:
            cleanup()

    def test_wrapper_and_legacy_raw_map_are_deterministic(self) -> None:
        from no1.kernel.access_tokens import load_access_tokens

        home, cleanup = self._with_home()
        try:
            path = home / "access_tokens.yaml"
            entry = _persisted_entry("user-a", groups=["g1"])
            path.write_text(yaml.safe_dump({"tokens": {"custom": entry}}, sort_keys=False), encoding="utf-8")
            wrapped = load_access_tokens(home)
            self.assertEqual(wrapped, load_access_tokens(home))
            self.assertEqual(wrapped["custom"]["created_at"], _STAMP)

            # cd704b48's first access-token writer emitted these same five
            # persisted fields; legacy readers also accepted the map without
            # the later canonical {tokens: ...} wrapper.
            path.write_text(yaml.safe_dump({"custom": entry}, sort_keys=False), encoding="utf-8")
            self.assertEqual(load_access_tokens(home), wrapped)

            legacy_admin = _persisted_entry("legacy-admin", is_admin=True)
            legacy_admin["allowed_groups"] = ["historically-ignored"]
            path.write_text(yaml.safe_dump({"legacy-admin-token": legacy_admin}, sort_keys=False), encoding="utf-8")
            loaded_admin = load_access_tokens(home)["legacy-admin-token"]
            self.assertTrue(loaded_admin["is_admin"])
            self.assertEqual(loaded_admin["allowed_groups"], [])

            missing_time = _persisted_entry("user-a", groups=["g1"])
            missing_time.pop("updated_at")
            path.write_text(yaml.safe_dump({"tokens": {"custom": missing_time}}, sort_keys=False), encoding="utf-8")
            self.assertEqual(load_access_tokens(home), {})
        finally:
            cleanup()

    def test_tokens_key_is_structurally_disambiguated_without_becoming_reserved(self) -> None:
        from no1.kernel.access_tokens import load_access_tokens, lookup_access_token, update_access_token

        home, cleanup = self._with_home()
        try:
            path = home / "access_tokens.yaml"
            entry = _persisted_entry("legacy-user", groups=["g1"])

            path.write_text(yaml.safe_dump({"tokens": entry}, sort_keys=False), encoding="utf-8")
            self.assertEqual(set(load_access_tokens(home)), {"tokens"})
            self.assertEqual(lookup_access_token("tokens", home)["user_id"], "legacy-user")
            self.assertEqual(update_access_token("tokens", allowed_groups=["g2"], home=home)["allowed_groups"], ["g2"])

            path.write_text(yaml.safe_dump({"tokens": {"tokens": entry}}, sort_keys=False), encoding="utf-8")
            self.assertEqual(set(load_access_tokens(home)), {"tokens"})
            self.assertEqual(lookup_access_token("tokens", home)["user_id"], "legacy-user")
        finally:
            cleanup()

    def test_damaged_tokens_container_is_not_interpreted_as_either_schema(self) -> None:
        from no1.kernel.access_tokens import (
            AccessTokenStoreError,
            create_access_token,
            delete_access_token,
            load_access_tokens,
            lookup_access_token,
            save_access_tokens,
            update_access_token,
        )

        home, cleanup = self._with_home()
        try:
            path = home / "access_tokens.yaml"
            damaged_values = (
                {"user_id": "user-a", "allowed_groups": ["g1"]},
                {
                    **_persisted_entry("user-a", groups=["g1"]),
                    "nested-token": _persisted_entry("user-b", groups=["g2"]),
                },
                {"valid-token": _persisted_entry("user-a", groups=["g1"]), "broken-token": "not-a-record"},
            )
            replacement = {
                "replacement": {
                    "token": "replacement",
                    "kind": "access",
                    **_persisted_entry("replacement-user", groups=["g1"]),
                }
            }
            for damaged in damaged_values:
                with self.subTest(keys=list(damaged)):
                    path.write_text(yaml.safe_dump({"tokens": damaged}, sort_keys=False), encoding="utf-8")
                    before = path.read_bytes()
                    self.assertEqual(load_access_tokens(home), {})
                    self.assertIsNone(lookup_access_token("tokens", home))
                    mutations = (
                        lambda: create_access_token("new-user", allowed_groups=["g1"], home=home),
                        lambda: update_access_token("tokens", allowed_groups=["g2"], home=home),
                        lambda: delete_access_token("tokens", home),
                        lambda: save_access_tokens(replacement, home),
                    )
                    for mutation in mutations:
                        with self.assertRaisesRegex(AccessTokenStoreError, "^Access token store is malformed$"):
                            mutation()
                        self.assertEqual(path.read_bytes(), before)
        finally:
            cleanup()

    def test_reads_fail_closed_for_every_structural_corruption(self) -> None:
        from no1.kernel.access_tokens import load_access_tokens

        home, cleanup = self._with_home()
        try:
            path = home / "access_tokens.yaml"
            valid = """tokens:\n  valid:\n    user_id: user-a\n    allowed_groups: [g1]\n    is_admin: false\n    created_at: '2026-01-01T00:00:00Z'\n    updated_at: '2026-01-01T00:00:00Z'\n"""
            corruptions = {
                "invalid_yaml": "tokens: [",
                "duplicate_root": valid + "tokens: {}\n",
                "duplicate_entry_key": valid.replace("    user_id: user-a\n", "    user_id: user-a\n    user_id: user-b\n"),
                "non_string_key": "tokens:\n  1:\n    user_id: user-a\n",
                "string_collision": "tokens:\n  1: {}\n  '1': {}\n",
                "mixed_non_dict": valid + "  broken: nope\n",
                "wrapper_extra": valid + "version: 1\n",
                "public_projection_on_disk": valid.replace(
                    "    user_id: user-a\n",
                    "    token: valid\n    kind: access\n    user_id: user-a\n",
                ),
                "reversed_timestamp": valid.replace(
                    "    updated_at: '2026-01-01T00:00:00Z'",
                    "    updated_at: '2025-01-01T00:00:00Z'",
                ),
            }
            for name, raw in corruptions.items():
                with self.subTest(name=name):
                    path.write_text(raw, encoding="utf-8")
                    self.assertEqual(load_access_tokens(home), {})
        finally:
            cleanup()

    def test_mutation_rejects_corrupt_store_without_changing_bytes(self) -> None:
        from no1.kernel.access_tokens import AccessTokenStoreError, create_access_token, save_access_tokens

        home, cleanup = self._with_home()
        try:
            path = home / "access_tokens.yaml"
            corruptions = (
                "tokens: [",
                "tokens:\n  bad:\n    user_id: user-a\n",
                "tokens:\n  valid: 1\n",
                "tokens:\n  duplicate: {}\n  duplicate: {}\n",
                """tokens:\n  reversed:\n    user_id: user-a\n    allowed_groups: [g1]\n    is_admin: false\n    created_at: '2026-01-02T00:00:00Z'\n    updated_at: '2026-01-01T00:00:00Z'\n""",
            )
            valid_candidate = {
                "replacement": {
                    "token": "replacement",
                    "kind": "access",
                    **_persisted_entry("replacement-user", groups=["g1"]),
                }
            }
            for raw in corruptions:
                with self.subTest(raw=raw):
                    path.write_text(raw, encoding="utf-8")
                    before = path.read_bytes()
                    with self.assertRaisesRegex(AccessTokenStoreError, "^Access token store is malformed$"):
                        create_access_token("user-b", allowed_groups=["g1"], home=home)
                    self.assertEqual(path.read_bytes(), before)
                    with self.assertRaisesRegex(AccessTokenStoreError, "^Access token store is malformed$"):
                        save_access_tokens(valid_candidate, home)
                    self.assertEqual(path.read_bytes(), before)
        finally:
            cleanup()

    def test_save_validates_complete_candidate_before_atomic_replace(self) -> None:
        from no1.kernel.access_tokens import AccessTokenStoreError, create_access_token, load_access_tokens, save_access_tokens

        home, cleanup = self._with_home()
        try:
            token = create_access_token("user-a", allowed_groups=["g1"], home=home)["token"]
            path = home / "access_tokens.yaml"
            before = path.read_bytes()
            candidate = load_access_tokens(home)
            candidate[token]["allowed_groups"] = ["g1", "g1"]
            with self.assertRaisesRegex(AccessTokenStoreError, "^Access token store is malformed$"):
                save_access_tokens(candidate, home)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(load_access_tokens(home)[token]["allowed_groups"], ["g1"])

            candidate = load_access_tokens(home)
            candidate[token]["updated_at"] = "2025-01-01T00:00:00Z"
            with self.assertRaisesRegex(AccessTokenStoreError, "^Access token store is malformed$"):
                save_access_tokens(candidate, home)
            self.assertEqual(path.read_bytes(), before)
        finally:
            cleanup()

    def test_custom_token_preserves_legacy_unbounded_strip_contract(self) -> None:
        from no1.kernel.access_tokens import create_access_token

        home, cleanup = self._with_home()
        try:
            long_token = "x" * 4096
            control_token = "secret\nnext-line"
            self.assertEqual(create_access_token("user-a", custom_token=long_token, home=home)["token"], long_token)
            self.assertEqual(
                create_access_token("user-b", custom_token=f"  {control_token}  ", home=home)["token"],
                control_token,
            )
        finally:
            cleanup()

    def test_spawn_distinct_creates_preserve_every_record(self) -> None:
        from no1.kernel.access_tokens import load_access_tokens

        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            results = _run_processes(_create_worker, [(str(home), index, "") for index in range(8)])
            self.assertEqual({state for state, _ in results}, {"ok"})
            self.assertEqual(len({token for _, token in results}), 8)
            self.assertEqual(len(load_access_tokens(home)), 8)

    def test_spawn_same_custom_token_creates_once_without_overwrite(self) -> None:
        from no1.kernel.access_tokens import load_access_tokens

        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            results = _run_processes(_create_worker, [(str(home), index, "shared-token") for index in range(8)])
            self.assertEqual([state for state, _ in results].count("ok"), 1)
            self.assertEqual([state for state, _ in results].count("ValueError"), 7)
            stored = load_access_tokens(home)
            self.assertEqual(set(stored), {"shared-token"})
            self.assertTrue(stored["shared-token"]["user_id"].startswith("user-"))

    def test_spawn_distinct_updates_preserve_every_record(self) -> None:
        from no1.kernel.access_tokens import create_access_token, load_access_tokens

        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            tokens = [create_access_token(f"user-{index}", allowed_groups=["old"], home=home)["token"] for index in range(8)]
            results = _run_processes(
                _update_worker,
                [(str(home), token, f"group-{index}") for index, token in enumerate(tokens)],
            )
            self.assertEqual({state for state, _ in results}, {"ok"})
            stored = load_access_tokens(home)
            for index, token in enumerate(tokens):
                self.assertEqual(stored[token]["allowed_groups"], [f"group-{index}"])

    def test_spawn_delete_update_race_never_resurrects_token(self) -> None:
        from no1.kernel.access_tokens import create_access_token, lookup_access_token

        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            token = create_access_token("user-a", allowed_groups=["old"], home=home)["token"]
            context = multiprocessing.get_context("spawn")
            results = context.Queue()
            processes = [
                context.Process(target=_delete_worker, args=(str(home), token, results)),
                context.Process(target=_update_worker, args=(str(home), token, "new", results)),
            ]
            for process in processes:
                process.start()
            for process in processes:
                process.join(timeout=20)
                self.assertEqual(process.exitcode, 0)
            outcomes = [results.get(timeout=5), results.get(timeout=5)]
            self.assertEqual({state for state, _ in outcomes}, {"ok"})
            self.assertIsNone(lookup_access_token(token, home))

    def test_claim_authorization_and_safe_principal_projection(self) -> None:
        from no1.kernel.access_tokens import AccessTokenClaimError, create_access_token, issue_access_token_principal_claim

        home, cleanup = self._with_home()
        try:
            secret = "custom-secret-value"
            create_access_token("member", allowed_groups=["g1"], custom_token=secret, home=home)
            member_claim = issue_access_token_principal_claim(secret, group_id="g1", home=home)
            principal = member_claim.principal
            self.assertEqual((principal.user_id, principal.group_id, principal.is_admin), ("member", "g1", False))
            self.assertEqual(principal.allowed_groups, ("g1",))
            self.assertNotIn(secret, repr(member_claim))
            self.assertNotIn(secret, repr(principal))
            self.assertEqual(member_claim.consume_current(lambda current: current.user_id), "member")

            with self.assertRaises(AccessTokenClaimError):
                issue_access_token_principal_claim(secret, group_id="g2", home=home)
            with self.assertRaises(AccessTokenClaimError):
                issue_access_token_principal_claim(secret, group_id="g1", require_admin=True, home=home)

            admin = create_access_token("admin", is_admin=True, home=home)["token"]
            admin_claim = issue_access_token_principal_claim(admin, group_id="any-group", require_admin=True, home=home)
            self.assertTrue(admin_claim.consume_current(lambda current: current.is_admin))
        finally:
            cleanup()

    def test_claim_cannot_be_constructed_copied_or_serialized(self) -> None:
        from no1.kernel.access_tokens import (
            AccessTokenClaimError,
            AccessTokenPrincipalClaim,
            create_access_token,
            issue_access_token_principal_claim,
        )

        home, cleanup = self._with_home()
        try:
            token = create_access_token("member", allowed_groups=["g1"], home=home)["token"]
            claim = issue_access_token_principal_claim(token, group_id="g1", home=home)
            with self.assertRaises(TypeError):
                AccessTokenPrincipalClaim(_seal=object())
            for operation in (lambda: copy.copy(claim), lambda: copy.deepcopy(claim), lambda: pickle.dumps(claim)):
                with self.subTest(operation=operation):
                    with self.assertRaises(TypeError):
                        operation()
            with self.assertRaises(TypeError):
                json.dumps(claim)
            with self.assertRaises(AccessTokenClaimError):
                AccessTokenPrincipalClaim.consume_current({"token": token}, lambda principal: principal)
            with self.assertRaises(AccessTokenClaimError):
                AccessTokenPrincipalClaim.consume_current_for_home(
                    {"token": token},
                    home,
                    lambda principal, canonical_home: principal,
                )
            forged = object.__new__(AccessTokenPrincipalClaim)
            with self.assertRaises(AccessTokenClaimError):
                _ = forged.principal
            with self.assertRaises(AccessTokenClaimError):
                forged.consume_current(lambda principal: principal)
            with self.assertRaises(AccessTokenClaimError):
                forged.consume_current_for_home(home, lambda principal, canonical_home: principal)
        finally:
            cleanup()

    def test_complete_entry_fingerprint_invalidates_old_claim(self) -> None:
        from no1.kernel.access_tokens import (
            AccessTokenClaimStaleError,
            create_access_token,
            issue_access_token_principal_claim,
            load_access_tokens,
            save_access_tokens,
            update_access_token,
        )

        home, cleanup = self._with_home()
        try:
            changes = ("groups", "admin", "user", "updated_at")
            for change in changes:
                with self.subTest(change=change):
                    token = create_access_token(f"user-{change}", allowed_groups=["g1"], home=home)["token"]
                    claim = issue_access_token_principal_claim(token, group_id="g1", home=home)
                    if change == "groups":
                        update_access_token(token, allowed_groups=["g2"], home=home)
                    elif change == "admin":
                        update_access_token(token, is_admin=True, home=home)
                    else:
                        candidate = load_access_tokens(home)
                        if change == "user":
                            candidate[token]["user_id"] = "replacement-user"
                        else:
                            candidate[token]["updated_at"] = "2099-01-02T00:00:00Z"
                        save_access_tokens(candidate, home)
                    called = []
                    with self.assertRaises(AccessTokenClaimStaleError):
                        claim.consume_current(lambda principal: called.append(principal))
                    self.assertEqual(called, [])
        finally:
            cleanup()

    def test_callback_failure_preserves_token_bytes_and_spends_claim(self) -> None:
        from no1.kernel.access_tokens import (
            AccessTokenClaimStaleError,
            create_access_token,
            issue_access_token_principal_claim,
        )

        home, cleanup = self._with_home()
        try:
            token = create_access_token("member", allowed_groups=["g1"], home=home)["token"]
            claim = issue_access_token_principal_claim(token, group_id="g1", home=home)
            path = home / "access_tokens.yaml"
            before = path.read_bytes()

            def fail(_principal):
                raise RuntimeError("pairing mutation failed")

            with self.assertRaisesRegex(RuntimeError, "pairing mutation failed"):
                claim.consume_current(fail)
            self.assertEqual(path.read_bytes(), before)
            with self.assertRaises(AccessTokenClaimStaleError):
                claim.consume_current(lambda principal: principal)
        finally:
            cleanup()

    def test_home_bound_consume_rejects_cross_home_without_leaking_and_spends_claim(self) -> None:
        from no1.kernel.access_tokens import (
            AccessTokenClaimHomeMismatchError,
            AccessTokenClaimStaleError,
            create_access_token,
            issue_access_token_principal_claim,
        )

        with tempfile.TemporaryDirectory() as first_td, tempfile.TemporaryDirectory() as second_td:
            first_home = Path(first_td)
            second_home = Path(second_td)
            token = create_access_token("member-a", allowed_groups=["g1"], home=first_home)["token"]
            create_access_token("member-b", allowed_groups=["g1"], home=second_home)
            claim = issue_access_token_principal_claim(token, group_id="g1", home=first_home)
            first_path = first_home / "access_tokens.yaml"
            second_path = second_home / "access_tokens.yaml"
            before = (first_path.read_bytes(), second_path.read_bytes())
            called = []

            with self.assertRaisesRegex(
                AccessTokenClaimHomeMismatchError,
                "^Access token principal claim does not authorize this home$",
            ) as ctx:
                claim.consume_current_for_home(second_home, lambda principal, canonical_home: called.append((principal, canonical_home)))

            self.assertEqual(called, [])
            self.assertNotIn(str(first_home), str(ctx.exception))
            self.assertNotIn(str(second_home), str(ctx.exception))
            self.assertNotIn(token, str(ctx.exception))
            self.assertEqual((first_path.read_bytes(), second_path.read_bytes()), before)
            with self.assertRaises(AccessTokenClaimStaleError):
                claim.consume_current(lambda principal: principal)

    def test_home_bound_consume_accepts_relative_resolved_and_symlink_aliases(self) -> None:
        from no1.kernel.access_tokens import create_access_token, issue_access_token_principal_claim

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            home = root / "home"
            home.mkdir()
            token = create_access_token("member", allowed_groups=["g1"], home=home)["token"]

            old_cwd = Path.cwd()
            try:
                os.chdir(root)
                relative_claim = issue_access_token_principal_claim(token, group_id="g1", home=Path("home"))
                principal = relative_claim.consume_current_for_home(home.resolve(), lambda current, canonical_home: current)
            finally:
                os.chdir(old_cwd)
            self.assertEqual((principal.user_id, principal.group_id), ("member", "g1"))

            if os.name != "nt":
                alias = root / "home-alias"
                alias.symlink_to(home, target_is_directory=True)
                alias_claim = issue_access_token_principal_claim(token, group_id="g1", home=home)
                aliased = alias_claim.consume_current_for_home(alias, lambda current, canonical_home: current)
                self.assertEqual(aliased.user_id, "member")

    @unittest.skipIf(os.name == "nt", "symlink retargeting is POSIX-specific")
    def test_home_bound_consume_rejects_retargeted_symlink(self) -> None:
        from no1.kernel.access_tokens import (
            AccessTokenClaimHomeMismatchError,
            AccessTokenClaimStaleError,
            create_access_token,
            issue_access_token_principal_claim,
        )

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            first_home = root / "first"
            second_home = root / "second"
            first_home.mkdir()
            second_home.mkdir()
            alias = root / "active-home"
            alias.symlink_to(first_home, target_is_directory=True)
            token = create_access_token("member", allowed_groups=["g1"], home=first_home)["token"]
            claim = issue_access_token_principal_claim(token, group_id="g1", home=alias)
            alias.unlink()
            alias.symlink_to(second_home, target_is_directory=True)
            called = []

            with self.assertRaisesRegex(
                AccessTokenClaimHomeMismatchError,
                "^Access token principal claim does not authorize this home$",
            ):
                claim.consume_current_for_home(alias, lambda principal, canonical_home: called.append((principal, canonical_home)))
            self.assertEqual(called, [])
            with self.assertRaises(AccessTokenClaimStaleError):
                claim.consume_current(lambda principal: principal)

    def test_home_bound_callback_failure_preserves_bytes_and_nested_guard_applies(self) -> None:
        from no1.kernel.access_tokens import (
            AccessTokenClaimStaleError,
            AccessTokenLockOrderError,
            create_access_token,
            issue_access_token_principal_claim,
        )

        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            token = create_access_token("member", allowed_groups=["g1"], home=home)["token"]
            path = home / "access_tokens.yaml"
            before = path.read_bytes()
            failing = issue_access_token_principal_claim(token, group_id="g1", home=home)
            with self.assertRaisesRegex(RuntimeError, "callback failed"):
                failing.consume_current_for_home(
                    home,
                    lambda _principal, _canonical_home: (_ for _ in ()).throw(RuntimeError("callback failed")),
                )
            self.assertEqual(path.read_bytes(), before)
            with self.assertRaises(AccessTokenClaimStaleError):
                failing.consume_current_for_home(home, lambda principal, canonical_home: principal)

            outer = issue_access_token_principal_claim(token, group_id="g1", home=home)
            inner = issue_access_token_principal_claim(token, group_id="g1", home=home)
            with self.assertRaisesRegex(
                AccessTokenLockOrderError,
                "^Access token APIs are unavailable during claim consumption$",
            ):
                outer.consume_current_for_home(
                    home,
                    lambda _principal, _canonical_home: inner.consume_current_for_home(
                        home,
                        lambda principal, canonical_home: principal,
                    ),
                )
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(
                inner.consume_current_for_home(home, lambda principal, canonical_home: principal).user_id,
                "member",
            )

    @unittest.skipIf(os.name == "nt", "symlink retargeting is POSIX-specific")
    def test_home_bound_consume_passes_claim_home_across_symlink_retarget(self) -> None:
        from no1.kernel.access_tokens import create_access_token, issue_access_token_principal_claim

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            first_home = root / "first"
            second_home = root / "second"
            first_home.mkdir()
            second_home.mkdir()
            alias = root / "active-home"
            alias.symlink_to(first_home, target_is_directory=True)
            token = create_access_token("member", allowed_groups=["g1"], home=first_home)["token"]
            claim = issue_access_token_principal_claim(token, group_id="g1", home=alias)
            first_bytes = (first_home / "access_tokens.yaml").read_bytes()
            ready = threading.Event()
            changed = threading.Event()

            def retarget() -> None:
                self.assertTrue(ready.wait(timeout=5))
                alias.unlink()
                alias.symlink_to(second_home, target_is_directory=True)
                changed.set()

            worker = threading.Thread(target=retarget)
            worker.start()

            def write_marker(_principal, canonical_home: Path) -> Path:
                ready.set()
                self.assertTrue(changed.wait(timeout=5))
                marker = canonical_home / "claim-home-marker"
                marker.write_text("authorized", encoding="utf-8")
                return canonical_home

            consumed_home = claim.consume_current_for_home(alias, write_marker)
            worker.join(timeout=10)
            self.assertFalse(worker.is_alive())
            self.assertEqual(consumed_home, first_home.resolve())
            self.assertEqual((first_home / "claim-home-marker").read_text(encoding="utf-8"), "authorized")
            self.assertFalse((second_home / "claim-home-marker").exists())
            self.assertEqual((first_home / "access_tokens.yaml").read_bytes(), first_bytes)
            self.assertFalse((second_home / "access_tokens.yaml").exists())

    def test_callback_guard_rejects_every_public_token_entrypoint(self) -> None:
        from no1.kernel.access_tokens import (
            AccessTokenLockOrderError,
            create_access_token,
            delete_access_token,
            issue_access_token_principal_claim,
            list_access_tokens,
            load_access_tokens,
            lookup_access_token,
            save_access_tokens,
            update_access_token,
        )

        home, cleanup = self._with_home()
        try:
            token = create_access_token("member", allowed_groups=["g1"], home=home)["token"]
            snapshot = load_access_tokens(home)
            operations = {
                "load": lambda claim: load_access_tokens(home),
                "lookup": lambda claim: lookup_access_token(token, home),
                "list": lambda claim: list_access_tokens(home),
                "save": lambda claim: save_access_tokens(snapshot, home),
                "create": lambda claim: create_access_token("other", allowed_groups=["g1"], home=home),
                "update": lambda claim: update_access_token(token, allowed_groups=["g1"], home=home),
                "delete": lambda claim: delete_access_token(token, home),
                "issue": lambda claim: issue_access_token_principal_claim(token, group_id="g1", home=home),
                "principal": lambda claim: claim.principal,
                "consume": lambda claim: claim.consume_current(lambda principal: principal),
                "consume_home": lambda claim: claim.consume_current_for_home(
                    home,
                    lambda principal, canonical_home: principal,
                ),
            }
            path = home / "access_tokens.yaml"
            before = path.read_bytes()
            for name, operation in operations.items():
                with self.subTest(name=name):
                    claim = issue_access_token_principal_claim(token, group_id="g1", home=home)
                    with self.assertRaisesRegex(
                        AccessTokenLockOrderError,
                        "^Access token APIs are unavailable during claim consumption$",
                    ):
                        claim.consume_current(lambda _principal: operation(claim))
                    self.assertEqual(path.read_bytes(), before)
        finally:
            cleanup()

    def test_consume_holds_lock_ahead_of_concurrent_delete(self) -> None:
        from no1.kernel.access_tokens import create_access_token, issue_access_token_principal_claim, lookup_access_token

        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            token = create_access_token("member", allowed_groups=["g1"], home=home)["token"]
            claim = issue_access_token_principal_claim(token, group_id="g1", home=home)
            context = multiprocessing.get_context("spawn")
            gate, attempted, done = context.Event(), context.Event(), context.Event()
            results = context.Queue()
            process = context.Process(
                target=_gated_delete_worker,
                args=(str(home), token, gate, attempted, done, results),
            )
            process.start()

            def consume(principal):
                gate.set()
                self.assertTrue(attempted.wait(5))
                self.assertFalse(done.wait(0.2))
                self.assertEqual(principal.user_id, "member")
                return "consumed"

            self.assertEqual(claim.consume_current(consume), "consumed")
            process.join(timeout=10)
            self.assertEqual(process.exitcode, 0)
            self.assertEqual(results.get(timeout=5), ("ok", True))
            self.assertIsNone(lookup_access_token(token, home))

    def test_completed_delete_makes_unconsumed_claim_stale(self) -> None:
        from no1.kernel.access_tokens import (
            AccessTokenClaimStaleError,
            create_access_token,
            issue_access_token_principal_claim,
        )

        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            token = create_access_token("member", allowed_groups=["g1"], home=home)["token"]
            claim = issue_access_token_principal_claim(token, group_id="g1", home=home)
            results = _run_processes(_delete_worker, [(str(home), token)])
            self.assertEqual(results, [("ok", True)])
            called = []
            with self.assertRaises(AccessTokenClaimStaleError):
                claim.consume_current(lambda principal: called.append(principal))
            self.assertEqual(called, [])

    def test_consume_holds_lock_ahead_of_concurrent_scope_update(self) -> None:
        from no1.kernel.access_tokens import create_access_token, issue_access_token_principal_claim, lookup_access_token

        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            token = create_access_token("member", allowed_groups=["group-a"], home=home)["token"]
            claim = issue_access_token_principal_claim(token, group_id="group-a", home=home)
            context = multiprocessing.get_context("spawn")
            gate, attempted, done = context.Event(), context.Event(), context.Event()
            results = context.Queue()
            process = context.Process(
                target=_gated_update_worker,
                args=(str(home), token, gate, attempted, done, results),
            )
            process.start()

            def consume(principal):
                gate.set()
                self.assertTrue(attempted.wait(5))
                self.assertFalse(done.wait(0.2))
                self.assertEqual(principal.allowed_groups, ("group-a",))
                return "consumed"

            self.assertEqual(claim.consume_current(consume), "consumed")
            process.join(timeout=10)
            self.assertEqual(process.exitcode, 0)
            self.assertEqual(results.get(timeout=5), ("ok", ["group-b"]))
            self.assertEqual(lookup_access_token(token, home)["allowed_groups"], ["group-b"])


if __name__ == "__main__":
    unittest.main()
