from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from no1.ports.web.routes.account import _api_v1_base_url


def test_api_v1_base_url_accepts_service_root() -> None:
    assert (
        _api_v1_base_url(
            "http://dongdongkc.shierkeji.com:6201/ocs",
            service_path="ocs",
            code="invalid",
            label="OCS",
        )
        == "http://dongdongkc.shierkeji.com:6201/ocs/api/v1"
    )


def test_api_v1_base_url_accepts_api_root() -> None:
    assert (
        _api_v1_base_url(
            "http://dongdongkc.shierkeji.com:6201/ocs/api/v1",
            service_path="ocs",
            code="invalid",
            label="OCS",
        )
        == "http://dongdongkc.shierkeji.com:6201/ocs/api/v1"
    )


def test_api_v1_base_url_accepts_smart_ops_root() -> None:
    assert (
        _api_v1_base_url(
            "https://dongdongkc.shierkeji.com:6201",
            service_path="ua2",
            code="invalid",
            label="UA2",
        )
        == "https://dongdongkc.shierkeji.com:6201/ua2/api/v1"
    )


def test_api_v1_base_url_accepts_smart_ops_root_for_ocs() -> None:
    assert (
        _api_v1_base_url(
            "https://dongdongkc.shierkeji.com:6201",
            service_path="ocs",
            code="invalid",
            label="OCS",
        )
        == "https://dongdongkc.shierkeji.com:6201/ocs/api/v1"
    )


class TestAccountCodexConfig(unittest.TestCase):
    def test_fetch_account_session_repairs_codex_custom_provider_config(self) -> None:
        from no1.ports.web.routes import account

        async def fake_json_request(_client, _method, _url, *, stage, **_kwargs):
            if stage == "ua2_self":
                return {"username": "agent1", "display_name": "Agent 1", "status": "enabled"}
            if stage == "ocs_donehub_ensure":
                return {
                    "ok": True,
                    "result": {
                        "codex_api_key": "sk-codex-secret",
                        "codex_model": "gpt-5.4",
                        "donehub_group": "default",
                        "quota": 250000,
                        "used_quota": 1200,
                        "allow_multi_client_login": True,
                    },
                }
            raise AssertionError(f"unexpected stage: {stage}")

        with tempfile.TemporaryDirectory() as tmpdir:
            home_path = Path(tmpdir)
            codex_dir = home_path / ".codex"
            codex_dir.mkdir(parents=True, exist_ok=True)
            config_path = codex_dir / "config.toml"
            config_path.write_text(
                'model = "old-model"\n'
                'openai_base_url = "https://old.example/v1"\n'
                'model_provider = "legacy"\n'
                "\n"
                "[model_providers.custom]\n"
                'name = "custom"\n'
                "requires_openai_auth = true\n"
                'base_url = "https://old.example/v1"\n'
                "\n"
                "[projects.'F:\\\\existing']\n"
                'trust_level = "trusted"\n',
                encoding="utf-8",
            )

            with (
                patch("no1.ports.web.routes.account._json_request", new=AsyncMock(side_effect=fake_json_request)),
                patch("no1.ports.web.codex_client_config.Path.home", return_value=home_path),
            ):
                resp = asyncio.run(account._fetch_account_session("ua-token", start_onecolleague_session=True))

            self.assertTrue(bool(resp.get("ok")), resp)
            session = ((resp.get("result") or {}).get("session") or {})
            self.assertEqual(str(session.get("codex_api_key") or ""), "sk-codex-secret")
            content = config_path.read_text(encoding="utf-8")
            self.assertIn('model_provider = "custom"\n', content)
            self.assertIn('model_reasoning_effort = "high"\n', content)
            self.assertIn("disable_response_storage = true\n", content)
            self.assertIn("[model_providers.custom]\n", content)
            self.assertIn('wire_api = "responses"\n', content)
            self.assertIn('base_url = "https://peer.shierkeji.com/v1"\n', content)
            self.assertIn('env_key = "ONECOLLEAGUE_API_KEY"\n', content)
            self.assertIn("[projects.'F:\\\\existing']\n", content)
            self.assertNotIn("openai_base_url", content)
            self.assertNotIn("requires_openai_auth", content)

    def test_fetch_account_session_returns_generic_error_when_codex_config_fails(self) -> None:
        from no1.ports.web.routes import account

        async def fake_json_request(_client, _method, _url, *, stage, **_kwargs):
            if stage == "ua2_self":
                return {"username": "agent1", "display_name": "Agent 1", "status": "enabled"}
            if stage == "ocs_donehub_ensure":
                return {"ok": True, "result": {"codex_api_key": "sk-codex-secret"}}
            raise AssertionError(f"unexpected stage: {stage}")

        with (
            patch("no1.ports.web.routes.account._json_request", new=AsyncMock(side_effect=fake_json_request)),
            patch("no1.ports.web.routes.account.sync_codex_custom_provider_config", side_effect=OSError("C:/Users/name/.codex/config.toml")),
        ):
            resp = asyncio.run(account._fetch_account_session("ua-token", start_onecolleague_session=True))

        self.assertFalse(bool(resp.get("ok")), resp)
        error = resp.get("error") if isinstance(resp.get("error"), dict) else {}
        self.assertEqual(str(error.get("code") or ""), "account_client_config_failed")
        self.assertNotIn(".codex", str(error.get("message") or ""))
