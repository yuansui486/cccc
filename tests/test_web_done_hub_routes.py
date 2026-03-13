from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient


class _FakeResponse:
    def __init__(self, status_code: int, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class _FakeAsyncClient:
    def __init__(self, responses: dict[tuple[str, str], _FakeResponse], calls: list[tuple[str, str, dict]]):
        self._responses = responses
        self._calls = calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url: str, **kwargs):
        self._calls.append(("POST", url, kwargs))
        resp = self._responses[("POST", url)]
        if isinstance(resp, list):
            if not resp:
                raise AssertionError(f"no queued response left for POST {url}")
            return resp.pop(0)
        return resp

    async def get(self, url: str, **kwargs):
        self._calls.append(("GET", url, kwargs))
        resp = self._responses[("GET", url)]
        if isinstance(resp, list):
            if not resp:
                raise AssertionError(f"no queued response left for GET {url}")
            return resp.pop(0)
        return resp


class TestWebDoneHubRoutes(unittest.TestCase):
    def _create_client(self) -> TestClient:
        from cccc.ports.web.app import create_app

        return TestClient(create_app())

    def test_done_hub_login_rejects_invalid_base_url(self) -> None:
        client = self._create_client()
        resp = client.post(
            "/api/v1/done_hub/login",
            json={"base_url": "peer.shierkeji.com", "username": "agent1", "password": "admin123"},
        )
        self.assertEqual(resp.status_code, 400)
        body = resp.json()
        self.assertEqual(str((body.get("error") or {}).get("code") or ""), "invalid_base_url")

    def test_done_hub_login_chains_login_and_self(self) -> None:
        base = "https://peer.shierkeji.com"
        calls: list[tuple[str, str, dict]] = []
        responses = {
            ("POST", f"{base}/api/user/login"): _FakeResponse(200, {"success": True, "message": "", "data": {"id": 2}}),
            ("GET", f"{base}/api/user/self"): _FakeResponse(
                200,
                {
                    "success": True,
                    "message": "",
                    "data": {
                        "username": "agent1",
                        "display_name": "Agent 1",
                        "group": "default",
                        "quota": 250000,
                        "used_quota": 1200,
                        "role": 1,
                        "status": 1,
                        "access_token": "token-32",
                    },
                },
            ),
        }

        def _factory(*args, **kwargs):
            return _FakeAsyncClient(responses, calls)

        with (
            patch("cccc.ports.web.routes.done_hub.httpx.AsyncClient", side_effect=_factory),
            patch("cccc.ports.web.routes.done_hub._configure_local_clients", new=AsyncMock(return_value=None)),
        ):
            client = self._create_client()
            resp = client.post(
                "/api/v1/done_hub/login",
                json={"base_url": f"{base}/", "username": "agent1", "password": "admin123"},
            )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(bool(body.get("ok")))
        session = ((body.get("result") or {}).get("session") or {})
        self.assertEqual(str(session.get("base_url") or ""), base)
        self.assertEqual(str(session.get("username") or ""), "agent1")
        self.assertEqual(str(session.get("display_name") or ""), "Agent 1")
        self.assertEqual(int(session.get("quota") or 0), 250000)
        self.assertEqual(int(session.get("used_quota") or 0), 1200)
        self.assertEqual(str(session.get("access_token") or ""), "token-32")
        self.assertEqual(calls[0][0:2], ("POST", f"{base}/api/user/login"))
        self.assertEqual(calls[1][0:2], ("GET", f"{base}/api/user/self"))

    def test_done_hub_login_surfaces_business_failure(self) -> None:
        base = "https://peer.shierkeji.com"

        def _factory(*args, **kwargs):
            return _FakeAsyncClient(
                {
                    ("POST", f"{base}/api/user/login"): _FakeResponse(
                        200,
                        {"success": False, "message": "用户名或密码错误"},
                    )
                },
                [],
            )

        with patch("cccc.ports.web.routes.done_hub.httpx.AsyncClient", side_effect=_factory):
            client = self._create_client()
            resp = client.post(
                "/api/v1/done_hub/login",
                json={"base_url": base, "username": "agent1", "password": "wrong"},
            )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertFalse(bool(body.get("ok")))
        self.assertEqual(str((body.get("error") or {}).get("code") or ""), "done_hub_login_failed")
        self.assertEqual(str((body.get("error") or {}).get("message") or ""), "用户名或密码错误")

    def test_done_hub_self_uses_bearer_token(self) -> None:
        base = "https://peer.shierkeji.com"
        calls: list[tuple[str, str, dict]] = []

        def _factory(*args, **kwargs):
            return _FakeAsyncClient(
                {
                    ("GET", f"{base}/api/user/self"): _FakeResponse(
                        200,
                        {
                            "success": True,
                            "message": "",
                            "data": {
                                "username": "agent1",
                                "display_name": "Agent 1",
                                "group": "default",
                                "quota": 250000,
                                "used_quota": 1200,
                                "role": 1,
                                "status": 1,
                                "access_token": "token-32",
                            },
                        },
                    )
                },
                calls,
            )

        with patch("cccc.ports.web.routes.done_hub.httpx.AsyncClient", side_effect=_factory):
            client = self._create_client()
            resp = client.post(
                "/api/v1/done_hub/self",
                json={"base_url": base, "access_token": "token-32"},
            )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(bool(body.get("ok")))
        headers = calls[0][2].get("headers") or {}
        self.assertEqual(headers.get("Authorization"), "Bearer token-32")

    def test_done_hub_login_provisions_client_files_for_normal_user(self) -> None:
        base = "https://peer.shierkeji.com"
        calls: list[tuple[str, str, dict]] = []
        with tempfile.TemporaryDirectory() as tmpdir:
            home_path = Path(tmpdir)
            codex_dir = home_path / ".codex"
            codex_dir.mkdir(parents=True, exist_ok=True)
            existing_tail = (
                "[projects.'F:\\\\新建文件夹']\n"
                'trust_level = "trusted"\n'
                "\n"
                "[windows]\n"
                'sandbox = "elevated"\n'
            )
            (codex_dir / "config.toml").write_text(
                'model_provider = "legacy"\n'
                'model = "old-model"\n'
                "\n"
                "[model_providers.legacy]\n"
                'name = "legacy"\n'
                "\n"
                f"{existing_tail}",
                encoding="utf-8",
            )
            responses = {
                ("POST", f"{base}/api/user/login"): _FakeResponse(200, {"success": True, "message": "", "data": {"id": 2}}),
                ("GET", f"{base}/api/user/self"): _FakeResponse(
                    200,
                    {
                        "success": True,
                        "message": "",
                        "data": {
                            "username": "agent1",
                            "display_name": "Agent 1",
                            "group": "default",
                            "quota": 250000,
                            "used_quota": 1200,
                            "role": 1,
                            "status": 1,
                            "access_token": "token-32",
                        },
                    },
                ),
                ("GET", f"{base}/api/token/"): [
                    _FakeResponse(
                        200,
                        {
                            "success": True,
                            "message": "",
                            "data": {
                                "data": [
                                    {"id": 20, "name": "codex", "key": "codex-secret"},
                                ],
                                "page": 1,
                                "size": 100,
                                "total_count": 1,
                            },
                        },
                    ),
                    _FakeResponse(
                        200,
                        {
                            "success": True,
                            "message": "",
                            "data": {
                                "data": [
                                    {"id": 20, "name": "codex", "key": "codex-secret"},
                                ],
                                "page": 1,
                                "size": 100,
                                "total_count": 1,
                            },
                        },
                    ),
                    _FakeResponse(
                        200,
                        {
                            "success": True,
                            "message": "",
                            "data": {
                                "data": [
                                    {"id": 21, "name": "gemini", "key": "gemini-secret"},
                                    {"id": 20, "name": "codex", "key": "codex-secret"},
                                ],
                                "page": 1,
                                "size": 100,
                                "total_count": 2,
                            },
                        },
                    ),
                ],
                ("POST", f"{base}/api/token/"): _FakeResponse(200, {"success": True, "message": ""}),
                ("GET", f"{base}/api/available_model"): _FakeResponse(
                    200,
                    {
                        "success": True,
                        "message": "",
                        "data": {
                            "gemini-3.1-pro-preview": {},
                            "gpt-5.4": {},
                        },
                    },
                ),
            }

            def _factory(*args, **kwargs):
                return _FakeAsyncClient(responses, calls)

            with (
                patch("cccc.ports.web.routes.done_hub.httpx.AsyncClient", side_effect=_factory),
                patch("cccc.ports.web.routes.done_hub.Path.home", return_value=home_path),
            ):
                client = self._create_client()
                resp = client.post(
                    "/api/v1/done_hub/login",
                    json={"base_url": base, "username": "agent1", "password": "admin123"},
                )

            self.assertEqual(resp.status_code, 200)
            body = resp.json()
            self.assertTrue(bool(body.get("ok")))
            post_token_calls = [call for call in calls if call[0:2] == ("POST", f"{base}/api/token/")]
            self.assertEqual(len(post_token_calls), 1)
            self.assertEqual(post_token_calls[0][2].get("json"), {
                "name": "gemini",
                "expired_time": -1,
                "remain_quota": 250000,
                "unlimited_quota": True,
            })
            self.assertEqual(
                (codex_dir / "auth.json").read_text(encoding="utf-8"),
                '{\n  "OPENAI_API_KEY": "sk-codex-secret"\n}\n',
            )
            self.assertEqual(
                (codex_dir / "config.toml").read_text(encoding="utf-8"),
                'model_provider = "custom"\n'
                'model = "gpt-5.4"\n'
                'model_reasoning_effort = "high"\n'
                "disable_response_storage = true\n"
                "\n"
                "[model_providers.custom]\n"
                'name = "custom"\n'
                'wire_api = "responses"\n'
                "requires_openai_auth = true\n"
                'base_url = "https://peer.shierkeji.com/v1"\n'
                "\n"
                f"{existing_tail}",
            )
            gemini_dir = home_path / ".gemini"
            self.assertEqual(
                (gemini_dir / ".env").read_text(encoding="utf-8"),
                "GOOGLE_GEMINI_BASE_URL=https://peer.shierkeji.com/gemini\n"
                "GEMINI_API_KEY=gemini-secret\n"
                "GEMINI_MODEL=gemini-3.1-pro-preview\n",
            )
            self.assertEqual(
                (gemini_dir / "settings.json").read_text(encoding="utf-8"),
                '{\n'
                '  "ide": {\n'
                '    "enabled": true\n'
                "  },\n"
                '  "security": {\n'
                '    "auth": {\n'
                '      "selectedType": "gemini-api-key"\n'
                "    }\n"
                "  }\n"
                "}\n",
            )

    def test_done_hub_login_skips_client_files_for_pro_user(self) -> None:
        base = "https://peer.shierkeji.com"
        calls: list[tuple[str, str, dict]] = []
        with tempfile.TemporaryDirectory() as tmpdir:
            home_path = Path(tmpdir)
            responses = {
                ("POST", f"{base}/api/user/login"): _FakeResponse(200, {"success": True, "message": "", "data": {"id": 3}}),
                ("GET", f"{base}/api/user/self"): _FakeResponse(
                    200,
                    {
                        "success": True,
                        "message": "",
                        "data": {
                            "username": "agent2",
                            "display_name": "Agent 2",
                            "group": "pro",
                            "quota": 500000,
                            "used_quota": 800,
                            "role": 1,
                            "status": 1,
                            "access_token": "token-pro",
                        },
                    },
                ),
            }

            def _factory(*args, **kwargs):
                return _FakeAsyncClient(responses, calls)

            with (
                patch("cccc.ports.web.routes.done_hub.httpx.AsyncClient", side_effect=_factory),
                patch("cccc.ports.web.routes.done_hub.Path.home", return_value=home_path),
            ):
                client = self._create_client()
                resp = client.post(
                    "/api/v1/done_hub/login",
                    json={"base_url": base, "username": "agent2", "password": "admin123"},
                )

            self.assertEqual(resp.status_code, 200)
            body = resp.json()
            self.assertTrue(bool(body.get("ok")))
            self.assertFalse((home_path / ".codex").exists())
            self.assertFalse((home_path / ".gemini").exists())
            self.assertEqual(
                [call[0:2] for call in calls],
                [
                    ("POST", f"{base}/api/user/login"),
                    ("GET", f"{base}/api/user/self"),
                ],
            )

    def test_done_hub_login_paginates_token_lookup_before_creating(self) -> None:
        base = "https://peer.shierkeji.com"
        calls: list[tuple[str, str, dict]] = []
        with tempfile.TemporaryDirectory() as tmpdir:
            home_path = Path(tmpdir)
            page_one_payload = {
                "success": True,
                "message": "",
                "data": {
                    "data": [
                        {"id": 9, "name": "alpha", "key": "alpha-key"},
                        {"id": 8, "name": "beta", "key": "beta-key"},
                    ],
                    "page": 1,
                    "size": 2,
                    "total_count": 3,
                },
            }
            page_two_payload = {
                "success": True,
                "message": "",
                "data": {
                    "data": [
                        {"id": 7, "name": "gemini", "key": "gemini-key"},
                        {"id": 6, "name": "codex", "key": "codex-key"},
                    ],
                    "page": 2,
                    "size": 2,
                    "total_count": 3,
                },
            }
            responses = {
                ("POST", f"{base}/api/user/login"): _FakeResponse(200, {"success": True, "message": "", "data": {"id": 2}}),
                ("GET", f"{base}/api/user/self"): _FakeResponse(
                    200,
                    {
                        "success": True,
                        "message": "",
                        "data": {
                            "username": "agent1",
                            "display_name": "Agent 1",
                            "group": "default",
                            "quota": 250000,
                            "used_quota": 1200,
                            "role": 1,
                            "status": 1,
                            "access_token": "token-32",
                        },
                    },
                ),
                ("GET", f"{base}/api/token/"): [
                    _FakeResponse(200, page_one_payload),
                    _FakeResponse(200, page_two_payload),
                    _FakeResponse(200, page_one_payload),
                    _FakeResponse(200, page_two_payload),
                ],
                ("GET", f"{base}/api/available_model"): _FakeResponse(
                    200,
                    {
                        "success": True,
                        "message": "",
                        "data": {
                            "gpt-5.4": {},
                            "gemini-3.1-pro-preview": {},
                        },
                    },
                ),
            }

            def _factory(*args, **kwargs):
                return _FakeAsyncClient(responses, calls)

            with (
                patch("cccc.ports.web.routes.done_hub.httpx.AsyncClient", side_effect=_factory),
                patch("cccc.ports.web.routes.done_hub.Path.home", return_value=home_path),
                patch("cccc.ports.web.routes.done_hub._TOKEN_PAGE_SIZE", 2),
            ):
                client = self._create_client()
                resp = client.post(
                    "/api/v1/done_hub/login",
                    json={"base_url": base, "username": "agent1", "password": "admin123"},
                )

            self.assertEqual(resp.status_code, 200)
            body = resp.json()
            self.assertTrue(bool(body.get("ok")))
            post_token_calls = [call for call in calls if call[0:2] == ("POST", f"{base}/api/token/")]
            self.assertEqual(post_token_calls, [])
            token_get_params = [call[2].get("params") for call in calls if call[0:2] == ("GET", f"{base}/api/token/")]
            self.assertEqual(token_get_params, [
                {"page": 1, "size": 2, "keyword": "", "order": "-id"},
                {"page": 2, "size": 2, "keyword": "", "order": "-id"},
                {"page": 1, "size": 2, "keyword": "", "order": "-id"},
                {"page": 2, "size": 2, "keyword": "", "order": "-id"},
            ])
            self.assertEqual(
                (home_path / ".codex" / "auth.json").read_text(encoding="utf-8"),
                '{\n  "OPENAI_API_KEY": "sk-codex-key"\n}\n',
            )

    def test_done_hub_login_returns_generic_error_when_config_write_fails(self) -> None:
        base = "https://peer.shierkeji.com"
        calls: list[tuple[str, str, dict]] = []
        responses = {
            ("POST", f"{base}/api/user/login"): _FakeResponse(200, {"success": True, "message": "", "data": {"id": 2}}),
            ("GET", f"{base}/api/user/self"): _FakeResponse(
                200,
                {
                    "success": True,
                    "message": "",
                    "data": {
                        "username": "agent1",
                        "display_name": "Agent 1",
                        "group": "default",
                        "quota": 250000,
                        "used_quota": 1200,
                        "role": 1,
                        "status": 1,
                        "access_token": "token-32",
                    },
                },
            ),
            ("GET", f"{base}/api/token/"): [
                _FakeResponse(
                    200,
                    {
                        "success": True,
                        "message": "",
                        "data": {
                            "data": [
                                {"id": 20, "name": "codex", "key": "codex-secret"},
                                {"id": 21, "name": "gemini", "key": "gemini-secret"},
                            ],
                            "page": 1,
                            "size": 100,
                            "total_count": 2,
                        },
                    },
                ),
                _FakeResponse(
                    200,
                    {
                        "success": True,
                        "message": "",
                        "data": {
                            "data": [
                                {"id": 20, "name": "codex", "key": "codex-secret"},
                                {"id": 21, "name": "gemini", "key": "gemini-secret"},
                            ],
                            "page": 1,
                            "size": 100,
                            "total_count": 2,
                        },
                    },
                ),
            ],
            ("GET", f"{base}/api/available_model"): _FakeResponse(
                200,
                {
                    "success": True,
                    "message": "",
                    "data": {
                        "gpt-5.4": {},
                        "gemini-3.1-pro-preview": {},
                    },
                },
            ),
        }

        def _factory(*args, **kwargs):
            return _FakeAsyncClient(responses, calls)

        with (
            patch("cccc.ports.web.routes.done_hub.httpx.AsyncClient", side_effect=_factory),
            patch("cccc.ports.web.routes.done_hub._sync_local_client_files", side_effect=OSError("failed to write /tmp/.codex/config.toml")),
        ):
            client = self._create_client()
            resp = client.post(
                "/api/v1/done_hub/login",
                json={"base_url": base, "username": "agent1", "password": "admin123"},
            )

        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertFalse(bool(body.get("ok")))
        self.assertEqual(str((body.get("error") or {}).get("code") or ""), "done_hub_client_config_failed")
        self.assertEqual(
            str((body.get("error") or {}).get("message") or ""),
            "登录成功，但本机客户端配置写入失败，请稍后重试或联系管理员。",
        )
        self.assertNotIn("/tmp/.codex/config.toml", str((body.get("error") or {}).get("message") or ""))


if __name__ == "__main__":
    unittest.main()
