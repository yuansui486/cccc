from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest.mock import patch


class TestNotebookLMProviderScaffold(unittest.TestCase):
    def test_delete_source_treats_vendor_none_as_success(self) -> None:
        import asyncio

        from no1.providers.notebooklm.adapter import _delete_source_async

        class _FakeSources:
            async def delete(self, notebook_id: str, source_id: str):
                self.called_with = (notebook_id, source_id)
                return None

        class _FakeClient:
            def __init__(self) -> None:
                self.sources = _FakeSources()

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

        fake_client = _FakeClient()

        async def _fake_build_client(*, auth_payload, timeout_seconds):
            _ = auth_payload, timeout_seconds
            return fake_client

        with patch("no1.providers.notebooklm.adapter._build_client", side_effect=_fake_build_client):
            out = asyncio.run(
                _delete_source_async(
                    notebook_id="nb_1",
                    source_id="src_1",
                    auth_payload={},
                    timeout_seconds=10.0,
                )
            )

        self.assertEqual(fake_client.sources.called_with, ("nb_1", "src_1"))
        self.assertTrue(out.get("deleted"))

    def test_health_check_accepts_explicit_auth_even_without_real_env_flag(self) -> None:
        from no1.providers.notebooklm.compat import NotebookLMCompatStatus
        from no1.providers.notebooklm.health import notebooklm_health_check

        raw_auth = '{"cookies":[{"name":"SID","value":"abc123","domain":".google.com"}]}'
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CCCC_NOTEBOOKLM_REAL", None)
            with patch(
                "no1.providers.notebooklm.health.probe_notebooklm_vendor",
                return_value=NotebookLMCompatStatus(compatible=True, reason="ok"),
            ):
                out = notebooklm_health_check(auth_json_raw=raw_auth)

        self.assertEqual(str(out.get("provider") or ""), "notebooklm")
        self.assertTrue(bool(out.get("enabled")))
        self.assertTrue(bool(out.get("compatible")))

    def test_adapter_run_with_vendor_auth_injects_env_temporarily(self) -> None:
        from no1.providers.notebooklm import adapter as notebooklm_adapter

        seen: dict[str, str] = {}

        async def _probe():
            seen["value"] = str(os.environ.get("NOTEBOOKLM_AUTH_JSON") or "")
            return {"ok": True}

        auth_payload = {"cookies": [{"name": "SID", "value": "token-x", "domain": ".google.com"}]}
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("NOTEBOOKLM_AUTH_JSON", None)
            out = notebooklm_adapter._run_with_vendor_auth(auth_payload, _probe())
            self.assertEqual(out, {"ok": True})
            self.assertTrue(seen.get("value"))
            parsed = json.loads(seen["value"])
            cookies = parsed.get("cookies") if isinstance(parsed, dict) else []
            self.assertEqual(str((cookies[0] if cookies else {}).get("value") or ""), "token-x")
            self.assertNotIn("NOTEBOOKLM_AUTH_JSON", os.environ)

    def test_adapter_run_with_vendor_auth_restores_previous_env(self) -> None:
        from no1.providers.notebooklm import adapter as notebooklm_adapter

        async def _probe():
            return str(os.environ.get("NOTEBOOKLM_AUTH_JSON") or "")

        previous = '{"cookies":[{"name":"SID","value":"old","domain":".google.com"}]}'
        auth_payload = {"cookies": [{"name": "SID", "value": "new", "domain": ".google.com"}]}
        with patch.dict(os.environ, {"NOTEBOOKLM_AUTH_JSON": previous}, clear=False):
            seen = notebooklm_adapter._run_with_vendor_auth(auth_payload, _probe())
            self.assertIn('"value": "new"', seen)
            self.assertEqual(str(os.environ.get("NOTEBOOKLM_AUTH_JSON") or ""), previous)

    def test_adapter_download_artifact_injects_vendor_auth_env(self) -> None:
        from no1.providers.notebooklm.adapter import NotebookLMAdapter

        captured: dict[str, str] = {}

        async def _fake_download(
            *,
            notebook_id: str,
            kind: str,
            output_path: str,
            artifact_id: str,
            output_format: str,
            auth_payload: dict,
            timeout_seconds: float,
        ):
            _ = notebook_id, kind, output_path, artifact_id, output_format, auth_payload, timeout_seconds
            captured["env"] = str(os.environ.get("NOTEBOOKLM_AUTH_JSON") or "")
            return {"output_path": output_path, "downloaded": True}

        adapter = NotebookLMAdapter()
        raw_auth = '{"cookies":[{"name":"SID","value":"abc123","domain":".google.com"}]}'
        with patch.object(adapter, "health_check", return_value={"provider": "notebooklm"}), patch(
            "no1.providers.notebooklm.adapter._download_artifact_async",
            side_effect=_fake_download,
        ):
            out = adapter.download_artifact(
                remote_space_id="nb_test",
                kind="infographic",
                output_path="/tmp/out.png",
                artifact_id="art_1",
                output_format="",
                auth_json_raw=raw_auth,
            )

        self.assertEqual(bool(out.get("downloaded")), True)
        self.assertTrue(captured.get("env"))
        payload = json.loads(captured["env"])
        cookies = payload.get("cookies") if isinstance(payload, dict) else []
        self.assertEqual(str((cookies[0] if cookies else {}).get("value") or ""), "abc123")

    def test_real_mode_takes_precedence_over_stub_mode(self) -> None:
        from no1.daemon.space.group_space_provider import SpaceProviderError, provider_query

        with patch.dict(os.environ, {}, clear=False):
            with tempfile.TemporaryDirectory() as td:
                os.environ["CCCC_HOME"] = td
                os.environ["CCCC_NOTEBOOKLM_REAL"] = "1"
                os.environ["CCCC_NOTEBOOKLM_STUB"] = "1"
                os.environ.pop("CCCC_NOTEBOOKLM_AUTH_JSON", None)
                with patch("no1.daemon.space.group_space_provider.notebooklm_real_enabled", return_value=True):
                    with self.assertRaises(SpaceProviderError) as ctx:
                        provider_query(
                            "notebooklm",
                            remote_space_id="nb_1",
                            query="hello",
                            options={},
                        )
                self.assertEqual(ctx.exception.code, "space_provider_not_configured")
                self.assertTrue(ctx.exception.degrade_provider)

    def test_invalid_auth_json_is_mapped_to_space_provider_error(self) -> None:
        from no1.daemon.space.group_space_provider import SpaceProviderError, provider_ingest

        with patch.dict(os.environ, {}, clear=False):
            os.environ["CCCC_NOTEBOOKLM_REAL"] = "1"
            os.environ["CCCC_NOTEBOOKLM_AUTH_JSON"] = "{bad-json"
            with patch("no1.daemon.space.group_space_provider.notebooklm_real_enabled", return_value=True):
                with self.assertRaises(SpaceProviderError) as ctx:
                    provider_ingest(
                        "notebooklm",
                        remote_space_id="nb_2",
                        kind="context_sync",
                        payload={"k": "v"},
                    )
            self.assertEqual(ctx.exception.code, "space_provider_auth_invalid")
            self.assertTrue(ctx.exception.degrade_provider)
            self.assertFalse(ctx.exception.transient)

    def test_compat_mismatch_is_mapped_to_space_provider_error(self) -> None:
        from no1.daemon.space.group_space_provider import SpaceProviderError, provider_query
        from no1.providers.notebooklm.compat import NotebookLMCompatStatus

        with patch.dict(os.environ, {}, clear=False):
            os.environ["CCCC_NOTEBOOKLM_REAL"] = "1"
            os.environ["CCCC_NOTEBOOKLM_AUTH_JSON"] = (
                '{"cookies":[{"name":"__Secure-1PSID","value":"x","domain":".google.com"}]}'
            )
            with patch("no1.daemon.space.group_space_provider.notebooklm_real_enabled", return_value=True):
                with patch(
                    "no1.providers.notebooklm.health.probe_notebooklm_vendor",
                    return_value=NotebookLMCompatStatus(compatible=False, reason="forced mismatch"),
                ):
                    with self.assertRaises(SpaceProviderError) as ctx:
                        provider_query(
                            "notebooklm",
                            remote_space_id="nb_3",
                            query="status",
                            options={},
                        )
            self.assertEqual(ctx.exception.code, "space_provider_compat_mismatch")
            self.assertTrue(ctx.exception.degrade_provider)

    def test_real_adapter_maps_unexpected_runtime_error(self) -> None:
        from no1.daemon.space.group_space_provider import SpaceProviderError, provider_query
        from no1.providers.notebooklm.compat import NotebookLMCompatStatus

        def _boom(coro):
            coro.close()
            raise RuntimeError("boom")

        with patch.dict(os.environ, {}, clear=False):
            os.environ["CCCC_NOTEBOOKLM_REAL"] = "1"
            os.environ["CCCC_NOTEBOOKLM_AUTH_JSON"] = (
                '{"cookies":[{"name":"__Secure-1PSID","value":"x","domain":".google.com"}]}'
            )
            with patch("no1.daemon.space.group_space_provider.notebooklm_real_enabled", return_value=True):
                with patch(
                    "no1.providers.notebooklm.health.probe_notebooklm_vendor",
                    return_value=NotebookLMCompatStatus(compatible=True, reason="ok"),
                ):
                    with patch(
                        "no1.providers.notebooklm.adapter._run_coroutine_sync",
                        side_effect=_boom,
                    ):
                        with self.assertRaises(SpaceProviderError) as ctx:
                            provider_query(
                                "notebooklm",
                                remote_space_id="nb_4",
                                query="status",
                                options={},
                            )
            self.assertEqual(ctx.exception.code, "space_provider_upstream_error")
            self.assertTrue(ctx.exception.transient)
            self.assertFalse(ctx.exception.degrade_provider)

    def test_real_adapter_query_success_path_via_runner_mock(self) -> None:
        from no1.daemon.space.group_space_provider import provider_query
        from no1.providers.notebooklm.compat import NotebookLMCompatStatus

        def _ok(coro):
            coro.close()
            return {"answer": "ok", "references": [{"source_id": "s1"}]}

        with patch.dict(os.environ, {}, clear=False):
            os.environ["CCCC_NOTEBOOKLM_REAL"] = "1"
            os.environ["CCCC_NOTEBOOKLM_AUTH_JSON"] = (
                '{"cookies":[{"name":"__Secure-1PSID","value":"x","domain":".google.com"}]}'
            )
            with patch("no1.daemon.space.group_space_provider.notebooklm_real_enabled", return_value=True):
                with patch(
                    "no1.providers.notebooklm.health.probe_notebooklm_vendor",
                    return_value=NotebookLMCompatStatus(compatible=True, reason="ok"),
                ):
                    with patch(
                        "no1.providers.notebooklm.adapter._run_coroutine_sync",
                        side_effect=_ok,
                    ):
                        out = provider_query(
                            "notebooklm",
                            remote_space_id="nb_4",
                            query="status",
                            options={},
                        )
        self.assertEqual(str(out.get("answer") or ""), "ok")
        refs = out.get("references") if isinstance(out.get("references"), list) else []
        self.assertEqual(len(refs), 1)

    def test_query_reference_metadata_keeps_answer_range_and_score(self) -> None:
        from no1.providers.notebooklm.adapter import _reference_to_dict

        class _Ref:
            source_id = "src_1"
            citation_number = 3
            cited_text = "quoted text"
            answer_start_char = 12
            answer_end_char = 28
            score = 0.81

        out = _reference_to_dict(_Ref())

        self.assertEqual(str(out.get("source_id") or ""), "src_1")
        self.assertEqual(out.get("citation_number"), 3)
        self.assertEqual(out.get("answer_range"), {"start_char": 12, "end_char": 28})
        self.assertEqual(out.get("score"), 0.81)

    def test_query_reference_metadata_omits_invalid_ranges_and_scores(self) -> None:
        from no1.providers.notebooklm.adapter import _reference_to_dict

        invalid_values = (
            (20, 10, float("nan")),
            (True, 10, float("inf")),
            ("not-an-int", 10, True),
            (4, None, float("-inf")),
        )
        for answer_start, answer_end, score in invalid_values:
            with self.subTest(answer_start=answer_start, answer_end=answer_end, score=score):
                ref = type(
                    "_Ref",
                    (),
                    {
                        "source_id": "src_invalid",
                        "answer_start_char": answer_start,
                        "answer_end_char": answer_end,
                        "score": score,
                    },
                )()

                out = _reference_to_dict(ref)

                self.assertNotIn("answer_range", out)
                self.assertNotIn("score", out)

    def test_generate_infographic_passes_style_option_to_vendor(self) -> None:
        import asyncio

        from no1.providers.notebooklm.adapter import _generate_artifact_async

        captured: dict[str, object] = {}

        class _FakeStatus:
            task_id = "task_1"
            status = "queued"
            url = ""
            error = ""
            error_code = ""
            metadata = {}

        class _FakeArtifacts:
            async def generate_infographic(self, *args, **kwargs):
                captured["args"] = args
                captured["kwargs"] = kwargs
                return _FakeStatus()

        class _FakeClient:
            artifacts = _FakeArtifacts()

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

        async def _fake_build_client(*, auth_payload, timeout_seconds):
            _ = auth_payload, timeout_seconds
            return _FakeClient()

        for options in (
            {"infographic_style": "scientific"},
            {"style": "scientific"},
            {"infographic_style": None, "style": "scientific"},
            {"infographic_style": "scientific", "style": "not-a-style"},
        ):
            captured.clear()
            with self.subTest(options=options), patch(
                "no1.providers.notebooklm.adapter._build_client",
                side_effect=_fake_build_client,
            ):
                out = asyncio.run(
                    _generate_artifact_async(
                        notebook_id="nb_1",
                        kind="infographic",
                        options=options,
                        auth_payload={},
                        timeout_seconds=10.0,
                    )
                )

            self.assertEqual(str(out.get("task_id") or ""), "task_1")
            kwargs = captured.get("kwargs") if isinstance(captured.get("kwargs"), dict) else {}
            self.assertEqual(str(getattr(kwargs.get("style"), "name", "") or ""), "SCIENTIFIC")

    def test_generate_infographic_rejects_invalid_style(self) -> None:
        import asyncio

        from no1.providers.notebooklm.adapter import _generate_artifact_async
        from no1.providers.notebooklm.errors import NotebookLMProviderError

        class _FakeArtifacts:
            async def generate_infographic(self, *args, **kwargs):
                raise AssertionError("invalid style must fail before the vendor call")

        class _FakeClient:
            artifacts = _FakeArtifacts()

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

        async def _fake_build_client(*, auth_payload, timeout_seconds):
            _ = auth_payload, timeout_seconds
            return _FakeClient()

        invalid_options = (
            {"infographic_style": False},
            {"style": 0},
            {"infographic_style": [], "style": "scientific"},
            {"style": {}},
            {"infographic_style": "not-a-style", "style": "scientific"},
        )
        for options in invalid_options:
            with self.subTest(options=options), patch(
                "no1.providers.notebooklm.adapter._build_client",
                side_effect=_fake_build_client,
            ):
                with self.assertRaises(NotebookLMProviderError) as ctx:
                    asyncio.run(
                        _generate_artifact_async(
                            notebook_id="nb_1",
                            kind="infographic",
                            options=options,
                            auth_payload={},
                            timeout_seconds=10.0,
                        )
                    )

            self.assertEqual(ctx.exception.code, "space_job_invalid")
            self.assertIn("invalid infographic_style", str(ctx.exception))

    def test_create_space_works_from_saved_state_without_real_env_flag(self) -> None:
        from no1.daemon.space.group_space_provider import provider_create_space
        from no1.daemon.space.group_space_store import set_space_provider_state, update_space_provider_secrets
        from no1.providers.notebooklm.compat import NotebookLMCompatStatus

        def _ok(coro):
            coro.close()
            return {"remote_space_id": "nb_auth_state", "title": "CCCC Space"}

        raw_auth = '{"cookies":[{"name":"SID","value":"abc123","domain":".google.com"}]}'
        with patch.dict(os.environ, {}, clear=False):
            with tempfile.TemporaryDirectory() as td:
                old_home = os.environ.get("CCCC_HOME")
                os.environ["CCCC_HOME"] = td
                os.environ.pop("CCCC_NOTEBOOKLM_REAL", None)
                try:
                    set_space_provider_state(
                        "notebooklm",
                        enabled=True,
                        real_enabled=True,
                        mode="active",
                        last_error="",
                        touch_health=True,
                    )
                    update_space_provider_secrets(
                        "notebooklm",
                        set_vars={"NOTEBOOKLM_AUTH_JSON": raw_auth},
                        unset_keys=[],
                        clear=False,
                    )
                    with patch(
                        "no1.providers.notebooklm.health.probe_notebooklm_vendor",
                        return_value=NotebookLMCompatStatus(compatible=True, reason="ok"),
                    ), patch(
                        "no1.providers.notebooklm.adapter._run_coroutine_sync",
                        side_effect=_ok,
                    ):
                        out = provider_create_space("notebooklm", title="CCCC Space")
                finally:
                    if old_home is None:
                        os.environ.pop("CCCC_HOME", None)
                    else:
                        os.environ["CCCC_HOME"] = old_home

        self.assertEqual(str(out.get("remote_space_id") or ""), "nb_auth_state")

    def test_notebooklm_error_flags_are_preserved_by_provider_mapping(self) -> None:
        from no1.daemon.space.group_space_provider import SpaceProviderError, provider_ingest
        from no1.providers.notebooklm.errors import NotebookLMProviderError

        class _DummyAdapter:
            def ingest(self, *, remote_space_id: str, kind: str, payload: dict):
                _ = remote_space_id, kind, payload
                raise NotebookLMProviderError(
                    code="space_upstream_busy",
                    message="upstream busy",
                    transient=True,
                    degrade_provider=False,
                )

        with patch("no1.daemon.space.group_space_provider.notebooklm_real_enabled", return_value=True):
            with patch("no1.daemon.space.group_space_provider.get_notebooklm_adapter", return_value=_DummyAdapter()):
                with self.assertRaises(SpaceProviderError) as ctx:
                    provider_ingest(
                        "notebooklm",
                        remote_space_id="nb_5",
                        kind="context_sync",
                        payload={"k": "v"},
                    )
        self.assertEqual(ctx.exception.code, "space_upstream_busy")
        self.assertTrue(ctx.exception.transient)
        self.assertFalse(ctx.exception.degrade_provider)


if __name__ == "__main__":
    unittest.main()
