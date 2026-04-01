from __future__ import annotations

import json
import mimetypes
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from ...paths import ensure_home

VOICE_RETENTION_SECONDS = 7 * 24 * 60 * 60
VOICE_UPLOAD_MODEL = "fun-asr"
VOICE_UPLOAD_POLICY_URL = "https://dashscope.aliyuncs.com/api/v1/uploads"
VOICE_TRANSCRIPTION_URL = "https://dashscope.aliyuncs.com/api/v1/services/audio/asr/transcription"
VOICE_TASK_URL_TEMPLATE = "https://dashscope.aliyuncs.com/api/v1/tasks/{task_id}"
VOICE_DEFAULT_TIMEOUT = 60.0
VOICE_TASK_POLL_SECONDS = 0.8
VOICE_TASK_DEADLINE_SECONDS = 90.0
VOICE_KEY_FILE = "dashscope_api_key"


class VoiceAsrError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = str(code or "voice_asr_error").strip() or "voice_asr_error"
        self.message = str(message or "voice asr error").strip() or "voice asr error"
        self.status_code = int(status_code or 400)


@dataclass(frozen=True)
class SavedVoiceAudio:
    path: Path
    content_type: str
    bytes_count: int


def _repo_root() -> Path:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "pyproject.toml").exists():
            return parent
    return Path.cwd().resolve()


def voice_dir() -> Path:
    root = _repo_root() / "voice"
    root.mkdir(parents=True, exist_ok=True)
    return root


def prune_old_voice_files(*, now: float | None = None) -> None:
    cutoff = float(now if now is not None else time.time()) - float(VOICE_RETENTION_SECONDS)
    root = voice_dir()
    for path in root.iterdir():
        try:
            if not path.is_file():
                continue
            if path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
        except Exception:
            continue


def _load_dashscope_api_key() -> str:
    env_value = str(os.environ.get("DASHSCOPE_API_KEY") or "").strip()
    if env_value:
        return env_value
    key_path = ensure_home() / "config" / VOICE_KEY_FILE
    try:
        file_value = key_path.read_text(encoding="utf-8").strip()
    except Exception:
        file_value = ""
    if file_value:
        return file_value
    raise VoiceAsrError(
        "voice_asr_api_key_missing",
        "missing DashScope API key",
        status_code=503,
    )


def _guess_suffix(filename: str, content_type: str) -> str:
    suffix = Path(str(filename or "")).suffix.lower()
    if suffix:
        return suffix
    guessed = mimetypes.guess_extension(str(content_type or "").split(";", 1)[0].strip())
    if guessed:
        return guessed
    return ".webm"


def _parse_provider_error(payload: Any, *, fallback: str) -> str:
    if isinstance(payload, dict):
        for key in ("message", "error_message"):
            message = str(payload.get(key) or "").strip()
            if message:
                return message
        output = payload.get("output")
        if isinstance(output, dict):
            message = str(output.get("message") or "").strip()
            if message:
                return message
        error = payload.get("error")
        if isinstance(error, dict):
            message = str(error.get("message") or "").strip()
            if message:
                return message
    return fallback


def _parse_json_response(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except Exception:
        return None


async def persist_voice_upload(
    *,
    raw: bytes,
    filename: str,
    content_type: str,
    max_bytes: int,
) -> SavedVoiceAudio:
    if not raw:
        raise VoiceAsrError("voice_audio_empty", "empty audio payload", status_code=400)
    if len(raw) > int(max_bytes):
        raise VoiceAsrError("voice_audio_too_large", "audio file too large", status_code=413)

    suffix = _guess_suffix(filename, content_type)
    saved_name = f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex}{suffix}"
    path = voice_dir() / saved_name
    path.write_bytes(raw)
    normalized_content_type = str(content_type or "").split(";", 1)[0].strip() or "application/octet-stream"
    return SavedVoiceAudio(path=path, content_type=normalized_content_type, bytes_count=len(raw))


async def _get_upload_policy(*, client: httpx.AsyncClient, api_key: str, model_name: str) -> dict[str, Any]:
    resp = await client.get(
        VOICE_UPLOAD_POLICY_URL,
        params={"action": "getPolicy", "model": model_name},
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    payload = _parse_json_response(resp)
    if resp.status_code >= 400:
        raise VoiceAsrError(
            "voice_asr_upload_policy_failed",
            _parse_provider_error(payload, fallback=f"upload policy failed with HTTP {resp.status_code}"),
            status_code=502,
        )
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        raise VoiceAsrError("voice_asr_upload_policy_invalid", "upload policy response missing data", status_code=502)
    return data


async def _upload_file_to_oss(
    *,
    client: httpx.AsyncClient,
    policy: dict[str, Any],
    audio: SavedVoiceAudio,
) -> str:
    upload_host = str(policy.get("upload_host") or "").strip()
    upload_dir = str(policy.get("upload_dir") or "").strip().rstrip("/")
    if not upload_host or not upload_dir:
        raise VoiceAsrError("voice_asr_upload_policy_invalid", "upload policy is incomplete", status_code=502)

    key = f"{upload_dir}/{audio.path.name}"
    form_data = {
        "OSSAccessKeyId": str(policy.get("oss_access_key_id") or "").strip(),
        "policy": str(policy.get("policy") or "").strip(),
        "Signature": str(policy.get("signature") or "").strip(),
        "x-oss-object-acl": str(policy.get("x_oss_object_acl") or "private").strip() or "private",
        "x-oss-forbid-overwrite": str(policy.get("x_oss_forbid_overwrite") or "true").strip() or "true",
        "key": key,
        "success_action_status": "200",
    }
    if not form_data["OSSAccessKeyId"] or not form_data["policy"] or not form_data["Signature"]:
        raise VoiceAsrError("voice_asr_upload_policy_invalid", "upload policy credentials are incomplete", status_code=502)

    with audio.path.open("rb") as file_handle:
        resp = await client.post(
            upload_host,
            data=form_data,
            files={"file": (audio.path.name, file_handle, audio.content_type)},
        )
    if resp.status_code != 200:
        raise VoiceAsrError(
            "voice_asr_upload_failed",
            f"upload file failed with HTTP {resp.status_code}",
            status_code=502,
        )
    return f"oss://{key}"


async def _submit_transcription_task(
    *,
    client: httpx.AsyncClient,
    api_key: str,
    file_url: str,
) -> str:
    resp = await client.post(
        VOICE_TRANSCRIPTION_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "X-DashScope-Async": "enable",
            "X-DashScope-OssResourceResolve": "enable",
        },
        json={
            "model": VOICE_UPLOAD_MODEL,
            "input": {"file_urls": [file_url]},
        },
    )
    payload = _parse_json_response(resp)
    if resp.status_code >= 400:
        raise VoiceAsrError(
            "voice_asr_submit_failed",
            _parse_provider_error(payload, fallback=f"transcription submit failed with HTTP {resp.status_code}"),
            status_code=502,
        )
    output = payload.get("output") if isinstance(payload, dict) else None
    task_id = str(output.get("task_id") or "").strip() if isinstance(output, dict) else ""
    if not task_id:
        raise VoiceAsrError("voice_asr_submit_invalid", "transcription response missing task_id", status_code=502)
    return task_id


def _extract_transcript_text(payload: Any) -> str:
    transcripts = payload.get("transcripts") if isinstance(payload, dict) else None
    if not isinstance(transcripts, list):
        return ""
    parts: list[str] = []
    for item in transcripts:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if text:
            parts.append(text)
    return "\n".join(parts).strip()


async def _wait_for_transcription(
    *,
    client: httpx.AsyncClient,
    api_key: str,
    task_id: str,
) -> str:
    deadline = time.monotonic() + VOICE_TASK_DEADLINE_SECONDS
    last_message = "transcription task is still running"
    while time.monotonic() < deadline:
        resp = await client.get(
            VOICE_TASK_URL_TEMPLATE.format(task_id=task_id),
            headers={"Authorization": f"Bearer {api_key}"},
        )
        payload = _parse_json_response(resp)
        if resp.status_code >= 400:
            raise VoiceAsrError(
                "voice_asr_query_failed",
                _parse_provider_error(payload, fallback=f"transcription query failed with HTTP {resp.status_code}"),
                status_code=502,
            )

        output = payload.get("output") if isinstance(payload, dict) else None
        results = output.get("results") if isinstance(output, dict) else None
        if isinstance(results, list) and results:
            result = results[0] if isinstance(results[0], dict) else {}
            status = str(result.get("subtask_status") or "").strip().upper()
            if status == "SUCCEEDED":
                transcription_url = str(result.get("transcription_url") or "").strip()
                if not transcription_url:
                    raise VoiceAsrError("voice_asr_result_invalid", "transcription result missing url", status_code=502)
                result_resp = await client.get(transcription_url)
                result_payload = _parse_json_response(result_resp)
                if result_resp.status_code >= 400:
                    raise VoiceAsrError(
                        "voice_asr_result_fetch_failed",
                        f"failed to fetch transcription result with HTTP {result_resp.status_code}",
                        status_code=502,
                    )
                text = _extract_transcript_text(result_payload)
                if text:
                    return text
                raise VoiceAsrError("voice_asr_empty_result", "transcription result is empty", status_code=422)
            if status == "FAILED":
                last_message = str(result.get("message") or "").strip() or "transcription failed"
                raise VoiceAsrError("voice_asr_failed", last_message, status_code=502)
            last_message = str(result.get("message") or "").strip() or last_message

        task_status = str(output.get("task_status") or "").strip().upper() if isinstance(output, dict) else ""
        if task_status == "FAILED":
            raise VoiceAsrError(
                "voice_asr_failed",
                _parse_provider_error(payload, fallback=last_message),
                status_code=502,
            )
        await asyncio_sleep(VOICE_TASK_POLL_SECONDS)

    raise VoiceAsrError("voice_asr_timeout", "transcription timed out", status_code=504)


async def asyncio_sleep(seconds: float) -> None:
    import asyncio

    await asyncio.sleep(seconds)


async def transcribe_saved_audio(audio: SavedVoiceAudio) -> str:
    api_key = _load_dashscope_api_key()
    timeout = httpx.Timeout(VOICE_DEFAULT_TIMEOUT, connect=20.0)
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            policy = await _get_upload_policy(client=client, api_key=api_key, model_name=VOICE_UPLOAD_MODEL)
            file_url = await _upload_file_to_oss(client=client, policy=policy, audio=audio)
            task_id = await _submit_transcription_task(client=client, api_key=api_key, file_url=file_url)
            return await _wait_for_transcription(client=client, api_key=api_key, task_id=task_id)
    except httpx.HTTPError as exc:
        raise VoiceAsrError("voice_asr_network_error", str(exc), status_code=502) from exc


def write_dashscope_api_key_for_local_runtime(api_key: str) -> Path:
    normalized = str(api_key or "").strip()
    if not normalized:
        raise ValueError("missing api key")
    config_dir = ensure_home() / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    path = config_dir / VOICE_KEY_FILE
    path.write_text(normalized + "\n", encoding="utf-8")
    return path
