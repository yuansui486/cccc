from __future__ import annotations

import argparse
import os
import sys
from typing import Optional

import uvicorn

from ...daemon.server import call_daemon
from ...kernel.settings import resolve_remote_access_web_binding
from .bind_preflight import ensure_tcp_port_bindable


def _check_daemon_running() -> bool:
    """Check if daemon is running (don't start it)."""
    resp = call_daemon({"op": "ping"})
    return resp.get("ok", False)


def main(argv: Optional[list[str]] = None) -> int:
    binding = resolve_remote_access_web_binding()
    default_host = str(binding.get("web_host") or "").strip() or "127.0.0.1"
    default_port = int(binding.get("web_port") or 8848)
    parser = argparse.ArgumentParser(prog="cccc web", description="cccc web port (FastAPI)")
    parser.add_argument("--host", default=default_host, help=f"Bind host (default: {default_host})")
    parser.add_argument("--port", type=int, default=default_port, help=f"Bind port (default: {default_port})")
    parser.add_argument(
        "--mode",
        default=str(os.environ.get("CCCC_WEB_MODE") or "normal"),
        choices=["normal", "exhibit"],
        help="Web mode: normal (read/write) or exhibit (read-only) (default: normal)",
    )
    parser.add_argument("--exhibit", action="store_true", help="Shortcut for: --mode exhibit")
    parser.add_argument("--reload", action="store_true", help="Enable autoreload (dev)")
    parser.add_argument("--log-level", default="info", help="Uvicorn log level (default: info)")
    args = parser.parse_args(argv)
    if bool(getattr(args, "exhibit", False)):
        args.mode = "exhibit"

    os.environ["CCCC_WEB_MODE"] = str(args.mode or "normal")

    # Check daemon is running (don't auto-start)
    if not _check_daemon_running():
        print("error: daemon is not running. Start it with: cccc daemon start", file=sys.stderr)
        print("  or use: cccc (to start both daemon and web together)", file=sys.stderr)
        return 1

    try:
        ensure_tcp_port_bindable(host=str(args.host), port=int(args.port))
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    config = uvicorn.Config(
        "cccc.ports.web.app:create_app",
        factory=True,
        host=str(args.host),
        port=int(args.port),
        log_level=str(args.log_level),
        reload=bool(args.reload),
        timeout_graceful_shutdown=0.2,
    )
    server = uvicorn.Server(config)

    try:
        server.run()
    except (KeyboardInterrupt, SystemExit):
        pass
    
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
