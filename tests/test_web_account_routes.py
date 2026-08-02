from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

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
    def _create_client(self) -> TestClient:
        from no1.ports.web.app import create_app

        client = TestClient(create_app())
        self.addCleanup(self._close_client, client)
        return client

    def _close_client(self, client: TestClient) -> None:
        client.close()
        from no1.ports.web.app import _close_web_logging

        _close_web_logging()

    def test_login_session_preserves_existing_codex_config(self) -> None:
        async def fake_json_request(_client, _method, _url, *, stage, **_kwargs):
            if stage == "ua2_login":
                return {"access_token": "ua-token"}
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
            sentinel = b'preserve = "exactly"\r\n# login must not rewrite this file\r\n'
            config_path.write_bytes(sentinel)

            with (
                patch("no1.ports.web.routes.account._json_request", new=AsyncMock(side_effect=fake_json_request)),
                patch("no1.ports.web.codex_client_config.Path.home", return_value=home_path),
                patch("no1.ports.web.codex_client_config.sync_codex_custom_provider_config") as sync_config,
            ):
                client = self._create_client()
                resp = client.post(
                    "/api/v1/account/login",
                    json={"tenant_code": "tenant", "username": "agent1", "password": "secret"},
                )

            self.assertEqual(resp.status_code, 200)
            body = resp.json()
            self.assertTrue(bool(body.get("ok")), body)
            session = ((body.get("result") or {}).get("session") or {})
            self.assertEqual(str(session.get("codex_api_key") or ""), "sk-codex-secret")
            self.assertEqual(config_path.read_bytes(), sentinel)
            sync_config.assert_not_called()

    def test_self_session_does_not_create_codex_config(self) -> None:
        async def fake_json_request(_client, _method, _url, *, stage, **_kwargs):
            if stage == "ua2_self":
                return {"username": "agent1", "display_name": "Agent 1", "status": "enabled"}
            if stage == "ocs_donehub_me":
                return {"ok": True, "result": {"codex_api_key": "sk-codex-secret"}}
            raise AssertionError(f"unexpected stage: {stage}")

        with tempfile.TemporaryDirectory() as tmpdir:
            home_path = Path(tmpdir)
            config_path = home_path / ".codex" / "config.toml"
            with (
                patch("no1.ports.web.routes.account._json_request", new=AsyncMock(side_effect=fake_json_request)),
                patch("no1.ports.web.codex_client_config.Path.home", return_value=home_path),
                patch(
                    "no1.ports.web.codex_client_config.sync_codex_custom_provider_config",
                    side_effect=OSError(str(config_path)),
                ) as sync_config,
            ):
                client = self._create_client()
                resp = client.post(
                    "/api/v1/account/self",
                    json={"access_token": "ua-token", "onecolleague_session_version": 7},
                )

            self.assertEqual(resp.status_code, 200)
            body = resp.json()
            self.assertTrue(bool(body.get("ok")), body)
            session = ((body.get("result") or {}).get("session") or {})
            self.assertEqual(str(session.get("codex_api_key") or ""), "sk-codex-secret")
            self.assertFalse(config_path.exists())
            sync_config.assert_not_called()
