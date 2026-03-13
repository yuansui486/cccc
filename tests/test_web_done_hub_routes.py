from __future__ import annotations

import unittest
from unittest.mock import patch

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
        return self._responses[("POST", url)]

    async def get(self, url: str, **kwargs):
        self._calls.append(("GET", url, kwargs))
        return self._responses[("GET", url)]


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

        with patch("cccc.ports.web.routes.done_hub.httpx.AsyncClient", side_effect=_factory):
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


if __name__ == "__main__":
    unittest.main()
