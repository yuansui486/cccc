"""Environment-driven settings for Router and Worker."""
from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit


DEFAULT_PUBLIC_BASE_URL = "https://dongdongkc.shierkeji.com:6205"


def _env(key: str, default: str | None = None) -> str | None:
    v = os.getenv(key)
    return v if v is not None and v != "" else default


def _env_int(key: str, default: int) -> int:
    v = os.getenv(key)
    if v is None or v == "":
        return default
    return int(v)


def _env_float(key: str, default: float) -> float:
    v = os.getenv(key)
    if v is None or v == "":
        return default
    return float(v)


def _normalize_router_ws_url(url: str) -> str:
    raw = url.strip()
    parts = urlsplit(raw)
    if parts.scheme == "https":
        scheme = "wss"
    elif parts.scheme == "http":
        scheme = "ws"
    elif parts.scheme in ("ws", "wss"):
        scheme = parts.scheme
    else:
        return raw

    path = parts.path
    if path.rstrip("/") in ("", "/ws/workers"):
        path = "/ws/workers"

    return urlunsplit((scheme, parts.netloc, path, parts.query, parts.fragment))


@dataclass(frozen=True)
class RouterSettings:
    host: str
    port: int
    public_base_url: str
    worker_token: str
    max_upload_mb: int
    upload_stream_chunk_bytes: int
    worker_assign_timeout_seconds: float
    worker_rpc_timeout_seconds: float
    prompt_route_ttl_hours: float

    @classmethod
    def from_env(cls) -> "RouterSettings":
        return cls(
            host=_env("ROUTER_HOST", "127.0.0.1"),
            port=_env_int("ROUTER_PORT", 38349),
            public_base_url=_env("PUBLIC_BASE_URL", DEFAULT_PUBLIC_BASE_URL),
            worker_token=_env("WORKER_TOKEN", "change-me"),
            max_upload_mb=_env_int("MAX_UPLOAD_MB", 200),
            upload_stream_chunk_bytes=_env_int("UPLOAD_STREAM_CHUNK_BYTES", 262144),
            worker_assign_timeout_seconds=_env_float("WORKER_ASSIGN_TIMEOUT_SECONDS", 10.0),
            worker_rpc_timeout_seconds=_env_float("WORKER_RPC_TIMEOUT_SECONDS", 30.0),
            prompt_route_ttl_hours=_env_float("PROMPT_ROUTE_TTL_HOURS", 24.0),
        )


@dataclass(frozen=True)
class WorkerSettings:
    router_ws_url: str
    worker_id: str
    worker_token: str
    comfyui_base_url: str
    workflow_file: str
    image_node_id: str
    audio_node_id: str
    video_node_id: str
    width_node_id: str
    height_node_id: str
    poll_interval_seconds: float
    poll_timeout_seconds: float
    worker_tmp_dir: str
    reconnect_seconds: float
    heartbeat_seconds: float

    @classmethod
    def from_env(cls) -> "WorkerSettings":
        return cls(
            router_ws_url=_normalize_router_ws_url(_env("ROUTER_WS_URL", DEFAULT_PUBLIC_BASE_URL) or DEFAULT_PUBLIC_BASE_URL),
            worker_id=_env("WORKER_ID", "gpu-worker-01"),
            worker_token=_env("WORKER_TOKEN", "change-me"),
            comfyui_base_url=_env("COMFYUI_BASE_URL", "http://127.0.0.1:8080"),
            workflow_file=_env("WORKFLOW_FILE", "./workflows/kijai-wanvideo_I2V_InfiniteTalk_example_01.json"),
            image_node_id=_env("IMAGE_NODE_ID", "284"),
            audio_node_id=_env("AUDIO_NODE_ID", "125"),
            video_node_id=_env("VIDEO_NODE_ID", "131"),
            width_node_id=_env("WIDTH_NODE_ID", "245"),
            height_node_id=_env("HEIGHT_NODE_ID", "246"),
            poll_interval_seconds=_env_float("POLL_INTERVAL_SECONDS", 2.0),
            poll_timeout_seconds=_env_float("POLL_TIMEOUT_SECONDS", 600.0),
            worker_tmp_dir=_env("WORKER_TMP_DIR", "./worker_data/inflight"),
            reconnect_seconds=_env_float("WORKER_RECONNECT_SECONDS", 5.0),
            heartbeat_seconds=_env_float("WORKER_HEARTBEAT_SECONDS", 25.0),
        )
