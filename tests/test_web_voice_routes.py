from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient


class TestWebVoiceRoutes(unittest.TestCase):
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

        return td, cleanup

    def _create_group(self) -> str:
        from cccc.kernel.group import create_group
        from cccc.kernel.registry import load_registry

        reg = load_registry()
        return create_group(reg, title="voice-route", topic="").group_id

    def _client(self) -> TestClient:
        from cccc.ports.web.app import create_app

        return TestClient(create_app())

    def test_transcribe_audio_route_returns_text_and_saved_metadata(self) -> None:
        from cccc.ports.web.voice_asr import SavedVoiceAudio

        _, cleanup = self._with_home()
        try:
            group_id = self._create_group()
            saved = SavedVoiceAudio(path=Path("/tmp/voice/sample.webm"), content_type="audio/webm", bytes_count=3)
            with (
                patch("cccc.ports.web.routes.messaging.persist_voice_upload", new=AsyncMock(return_value=saved)),
                patch("cccc.ports.web.routes.messaging.transcribe_saved_audio", new=AsyncMock(return_value="hello from voice")),
            ):
                client = self._client()
                resp = client.post(
                    f"/api/v1/groups/{group_id}/transcribe_audio",
                    files={"file": ("voice.webm", BytesIO(b"abc"), "audio/webm")},
                )

            self.assertEqual(resp.status_code, 200)
            body = resp.json()
            self.assertTrue(bool(body.get("ok")))
            result = body.get("result") or {}
            self.assertEqual(str(result.get("text") or ""), "hello from voice")
            self.assertEqual(str(result.get("file_name") or ""), "sample.webm")
            self.assertEqual(str(result.get("saved_path") or ""), "voice/sample.webm")
            self.assertEqual(int(result.get("bytes") or 0), 3)
        finally:
            cleanup()

    def test_persist_voice_upload_writes_under_project_voice_dir(self) -> None:
        from cccc.ports.web import voice_asr

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with patch("cccc.ports.web.voice_asr._repo_root", return_value=root):
                saved = asyncio.run(
                    voice_asr.persist_voice_upload(
                        raw=b"voice-bytes",
                        filename="note.webm",
                        content_type="audio/webm",
                        max_bytes=1024,
                    )
                )

                self.assertTrue(saved.path.exists())
                self.assertEqual(saved.path.parent, root / "voice")
                self.assertEqual(saved.path.read_bytes(), b"voice-bytes")
                self.assertEqual(saved.content_type, "audio/webm")

    def test_prune_old_voice_files_removes_only_stale_entries(self) -> None:
        from cccc.ports.web import voice_asr

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            now = 1_750_000_000.0
            with patch("cccc.ports.web.voice_asr._repo_root", return_value=root):
                voice_root = root / "voice"
                voice_root.mkdir(parents=True, exist_ok=True)
                stale = voice_root / "stale.webm"
                fresh = voice_root / "fresh.webm"
                stale.write_bytes(b"old")
                fresh.write_bytes(b"new")
                os.utime(stale, (now - voice_asr.VOICE_RETENTION_SECONDS - 5, now - voice_asr.VOICE_RETENTION_SECONDS - 5))
                os.utime(fresh, (now, now))

                voice_asr.prune_old_voice_files(now=now)

                self.assertFalse(stale.exists())
                self.assertTrue(fresh.exists())


if __name__ == "__main__":
    unittest.main()
