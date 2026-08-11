import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from no1.daemon.opencode_provider import get_opencode_model_catalog, merge_opencode_provider_config


class TestOpenCodeProvider(unittest.TestCase):
    def test_merge_provider_preserves_existing_config_and_uses_env_key(self) -> None:
        env = {"ONECOLLEAGUE_HOME": tempfile.mkdtemp()}
        with patch(
            "no1.daemon.opencode_provider.load_opencode_model_catalog",
            return_value=[{"model": "gpt-5.4", "locked": True}, {"model": "qwen3.6-plus", "locked": False}],
        ):
            result = merge_opencode_provider_config(
                {
                    "theme": "dark",
                    "provider": {"custom": {"name": "Keep me"}},
                    "mcp": {"other": {"type": "local"}},
                },
                env,
            )

        provider = result["provider"]["onecolleague"]
        self.assertEqual(result["theme"], "dark")
        self.assertEqual(result["provider"]["custom"]["name"], "Keep me")
        self.assertEqual(result["mcp"]["other"]["type"], "local")
        self.assertEqual(provider["npm"], "@ai-sdk/openai-compatible")
        self.assertEqual(provider["options"]["baseURL"], "https://peer.shierkeji.com/v1")
        self.assertEqual(provider["options"]["apiKey"], "{env:ONECOLLEAGUE_API_KEY}")
        self.assertEqual(sorted(provider["models"]), ["gpt-5.4", "qwen3.6-plus"])

    def test_merge_provider_does_not_refresh_catalog_over_network(self) -> None:
        with tempfile.TemporaryDirectory() as td, patch("no1.daemon.opencode_provider.httpx.get") as get:
            result = merge_opencode_provider_config({}, {"ONECOLLEAGUE_HOME": td})

        self.assertTrue(result["provider"]["onecolleague"]["models"])
        get.assert_not_called()

    def test_merge_provider_adds_deepseek_v4_reasoning_variants(self) -> None:
        with patch(
            "no1.daemon.opencode_provider.load_opencode_model_catalog",
            return_value=[
                {"model": "deepseek-v4-pro", "locked": False},
                {"model": "deepseek-v4-flash", "locked": False},
                {"model": "qwen3.6-plus", "locked": False},
            ],
        ):
            result = merge_opencode_provider_config({})

        models = result["provider"]["onecolleague"]["models"]
        expected_variants = {
            "none": {"thinking": {"type": "disabled"}},
            "low": {"thinking": {"type": "enabled"}, "reasoningEffort": "low"},
            "high": {"thinking": {"type": "enabled"}, "reasoningEffort": "high"},
            "max": {"thinking": {"type": "enabled"}, "reasoningEffort": "max"},
        }
        for model in ("deepseek-v4-pro", "deepseek-v4-flash"):
            self.assertTrue(models[model]["reasoning"])
            self.assertEqual(models[model]["variants"], expected_variants)
            self.assertNotIn("reasoningEffort", models[model]["variants"]["none"])
        self.assertEqual(models["qwen3.6-plus"], {"name": "qwen3.6-plus"})

    def test_catalog_keeps_locked_models_and_caches_server_response(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            response = Mock()
            response.json.return_value = {
                "success": True,
                "data": {
                    "gpt-5.4": {"price": {"locked": True}},
                    "deepseek-v4-pro": {"price": {"locked": False}},
                },
            }
            with patch("no1.daemon.opencode_provider.httpx.get", return_value=response) as get:
                first = get_opencode_model_catalog({"ONECOLLEAGUE_HOME": td})
                second = get_opencode_model_catalog({"ONECOLLEAGUE_HOME": td})

            self.assertEqual([item["model"] for item in first], ["gpt-5.4", "deepseek-v4-pro"])
            self.assertTrue(first[0]["locked"])
            self.assertEqual(second, first)
            get.assert_called_once()
            cache = Path(td) / "state" / "cache" / "opencode_models.json"
            self.assertTrue(cache.exists())
            self.assertEqual(json.loads(cache.read_text(encoding="utf-8"))["models"], first)

    def test_catalog_falls_back_when_server_is_unreachable(self) -> None:
        with tempfile.TemporaryDirectory() as td, patch(
            "no1.daemon.opencode_provider.httpx.get",
            side_effect=OSError("offline"),
        ):
            models = get_opencode_model_catalog({"ONECOLLEAGUE_HOME": td})

        names = [item["model"] for item in models]
        self.assertIn("gpt-5.4", names)
        self.assertIn("deepseek-v4-pro", names)
