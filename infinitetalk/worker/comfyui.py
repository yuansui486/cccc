"""Thin async HTTP client for ComfyUI."""
from __future__ import annotations

from typing import Any, AsyncIterator
from urllib.parse import urlencode

import httpx


class ComfyUIError(Exception):
    pass


class ComfyUIClient:
    def __init__(self, base_url: str, timeout: float = 30.0):
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    async def upload_image(self, file_bytes: bytes, filename: str, content_type: str | None = None) -> str:
        files = {"image": (filename, file_bytes, content_type or "application/octet-stream")}
        async with httpx.AsyncClient(timeout=self._timeout) as cli:
            resp = await cli.post(f"{self._base_url}/upload/image", files=files)
        if resp.status_code != 200:
            raise ComfyUIError(f"upload rejected ({resp.status_code}): {resp.text}")
        data = resp.json()
        name = data.get("name")
        if not name:
            raise ComfyUIError("upload response missing 'name'")
        return name

    async def queue_prompt(self, prompt_json: dict) -> str:
        async with httpx.AsyncClient(timeout=self._timeout) as cli:
            resp = await cli.post(f"{self._base_url}/prompt", json={"prompt": prompt_json})
        if resp.status_code != 200:
            raise ComfyUIError(f"queue_prompt rejected ({resp.status_code}): {resp.text}")
        data = resp.json()
        prompt_id = data.get("prompt_id")
        if not prompt_id:
            raise ComfyUIError("queue_prompt response missing 'prompt_id'")
        return prompt_id

    async def history(self, prompt_id: str) -> dict:
        async with httpx.AsyncClient(timeout=self._timeout) as cli:
            resp = await cli.get(f"{self._base_url}/history/{prompt_id}")
        if resp.status_code != 200:
            raise ComfyUIError(f"history query failed ({resp.status_code}): {resp.text}")
        return resp.json()

    def build_view_url(self, video_info: dict) -> str:
        query = {
            "filename": video_info.get("filename", ""),
            "subfolder": video_info.get("subfolder", ""),
            "type": video_info.get("type", "output"),
        }
        for k, v in video_info.items():
            if v is not None and k not in query:
                query[k] = v
        return f"{self._base_url}/api/view?{urlencode(query)}"

    async def stream_view(self, url: str, chunk_size: int = 262144) -> AsyncIterator[tuple[dict, AsyncIterator[bytes]]]:
        """Yield exactly once: (headers, async iterator of bytes)."""
        raise NotImplementedError  # see open_view below

    async def open_view(self, url: str):
        """Return an async context-manager-like helper exposing headers and an aiter_bytes()."""
        return _ViewStream(url, self._timeout)


class _ViewStream:
    def __init__(self, url: str, timeout: float):
        self._url = url
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None
        self._response: httpx.Response | None = None

    async def __aenter__(self) -> "_ViewStream":
        self._client = httpx.AsyncClient(timeout=self._timeout)
        ctx = self._client.stream("GET", self._url)
        self._stream_ctx = ctx
        self._response = await ctx.__aenter__()
        if self._response.status_code != 200:
            text = await self._response.aread()
            raise ComfyUIError(f"view fetch failed ({self._response.status_code}): {text!r}")
        return self

    @property
    def content_type(self) -> str:
        return (self._response.headers.get("content-type") if self._response else None) or "application/octet-stream"

    @property
    def content_length(self) -> int | None:
        if not self._response:
            return None
        cl = self._response.headers.get("content-length")
        try:
            return int(cl) if cl is not None else None
        except ValueError:
            return None

    async def aiter_bytes(self, chunk_size: int = 262144):
        assert self._response is not None
        async for chunk in self._response.aiter_bytes(chunk_size=chunk_size):
            yield chunk

    async def __aexit__(self, exc_type, exc, tb):
        try:
            await self._stream_ctx.__aexit__(exc_type, exc, tb)
        finally:
            if self._client is not None:
                await self._client.aclose()
