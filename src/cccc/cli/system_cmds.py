from __future__ import annotations

"""System/daemon/web/mcp CLI command handlers."""

from .common import *  # noqa: F401,F403

__all__ = [
    "cmd_version",
    "cmd_status",
    "cmd_doctor",
    "cmd_web",
    "cmd_mcp",
    "cmd_setup",
    "cmd_daemon",
]

def cmd_version(_: argparse.Namespace) -> int:
    print(__version__)
    return 0

def cmd_status(_: argparse.Namespace) -> int:
    """Show overall CCCC status: daemon, groups, actors."""
    from ..kernel.runtime import detect_all_runtimes
    
    home = ensure_home()
    
    # Check daemon
    daemon_resp = call_daemon({"op": "ping"})
    daemon_ok = daemon_resp.get("ok", False)
    
    # Get groups
    groups_resp = call_daemon({"op": "groups"}) if daemon_ok else {"ok": False}
    groups = groups_resp.get("result", {}).get("groups", []) if groups_resp.get("ok") else []
    
    # Get active group
    active = load_active()
    active_group_id = str(active.get("active_group_id") or "").strip()
    
    # Get runtimes
    runtimes = detect_all_runtimes(primary_only=False)
    available_runtimes = [r.name for r in runtimes if r.available]
    
    print(f"CCCC Status")
    print(f"===========")
    print(f"Version:     {__version__}")
    print(f"Home:        {home}")
    print(f"Daemon:      {'running' if daemon_ok else 'stopped'}")
    print(f"Runtimes:    {', '.join(available_runtimes) if available_runtimes else '(none detected)'}")
    print()
    
    if not groups:
        print("Groups:      (none)")
    else:
        print(f"Groups:      {len(groups)}")
        for g in groups:
            gid = str(g.get("group_id") or "")
            title = str(g.get("title") or gid)
            running = g.get("running", False)
            active_mark = " *" if gid == active_group_id else ""
            status = "running" if running else "stopped"
            print(f"  - {title} ({gid}){active_mark} [{status}]")
            
            # Get actors for this group
            if daemon_ok:
                actors_resp = call_daemon({"op": "actor_list", "args": {"group_id": gid}})
                actors = actors_resp.get("result", {}).get("actors", []) if actors_resp.get("ok") else []
                for a in actors:
                    aid = str(a.get("id") or "")
                    role = str(a.get("role") or "peer")
                    enabled = a.get("enabled", False)
                    runtime = str(a.get("runtime") or "codex")
                    runner = str(a.get("runner") or "pty")
                    status = "on" if enabled else "off"
                    print(f"      {aid} ({role}, {runtime}, {runner}) [{status}]")
    
    return 0

def cmd_doctor(args: argparse.Namespace) -> int:
    """Check environment and show available agent runtimes."""
    from ..kernel.runtime import detect_all_runtimes
    from ..runners.platform_support import pty_support_details
    
    print("[DOCTOR] CCCC Environment Check")
    print()
    
    # Python version
    print(f"Python: {sys.version.split()[0]} ({sys.executable})")
    
    # CCCC version
    print(f"CCCC: {__version__}")
    
    # CCCC_HOME
    home = ensure_home()
    print(f"CCCC_HOME: {home}")
    
    # Daemon status
    resp = call_daemon({"op": "ping"})
    if resp.get("ok"):
        r = resp.get("result") if isinstance(resp.get("result"), dict) else {}
        print(f"Daemon: running (pid={r.get('pid')}, version={r.get('version')})")
    else:
        print("Daemon: not running")

    pty_diag = pty_support_details()
    if sys.platform.startswith("win"):
        print()
        if bool(pty_diag.get("supported")):
            print("Windows PTY: OK (ConPTY backend available)")
        else:
            print(f"Windows PTY: NOT READY ({pty_diag.get('code')})")
            print(f"  {pty_diag.get('message')}")
            for hint in pty_diag.get("hints") if isinstance(pty_diag.get("hints"), list) else []:
                print(f"  - {hint}")

    print()
    print("Agent Runtimes:")
    
    # Check all runtimes
    all_runtimes = args.all if hasattr(args, 'all') else False
    runtimes = detect_all_runtimes(primary_only=not all_runtimes)

    encoding = str(getattr(sys.stdout, "encoding", "") or "").lower()
    ascii_only = sys.platform.startswith("win") and encoding not in ("utf-8", "utf8")

    available_count = 0
    for rt in runtimes:
        status = "OK" if rt.available else "NOT FOUND"
        mark = "[OK]" if rt.available else "[NO]" if ascii_only else ("✓" if rt.available else "✗")
        path_info = f" ({rt.path})" if rt.available else ""
        print(f"  {mark} {rt.name}: {status}{path_info}")
        if rt.available:
            available_count += 1
    
    print()
    if available_count == 0:
        print("No agent runtimes detected.")
        print("First-class supported runtimes: claude, codex, droid, amp, auggie, neovate, gemini, kimi")
        print("Manual fallback: custom (bring your own command and MCP wiring)")
    else:
        print(f"{available_count} runtime(s) available.")
        print()
        print("Quick start:")
        print(f"  cccc setup --runtime {runtimes[0].name if runtimes[0].available else 'claude'}")
        print("  cccc attach .")
        print("  cccc actor add my-agent --runtime <name>")
        print("  cccc")
    
    return 0

def cmd_web(args: argparse.Namespace) -> int:
    from ..ports.web.main import main as web_main

    argv: list[str] = []
    if str(args.host or "").strip():
        argv.extend(["--host", str(args.host)])
    if args.port is not None:
        argv.extend(["--port", str(int(args.port))])
    if bool(getattr(args, "exhibit", False)):
        argv.append("--exhibit")
    elif str(getattr(args, "mode", "") or "").strip():
        argv.extend(["--mode", str(getattr(args, "mode"))])
    if bool(args.reload):
        argv.append("--reload")
    if str(args.log_level or "").strip():
        argv.extend(["--log-level", str(args.log_level)])
    return int(web_main(argv))

def cmd_mcp(args: argparse.Namespace) -> int:
    from ..ports.mcp.main import main as mcp_main

    return int(mcp_main())

def cmd_setup(args: argparse.Namespace) -> int:
    """Setup CCCC MCP for agent runtimes (configure MCP, print guidance)."""
    from ..daemon.mcp_install import build_mcp_add_command, ensure_mcp_installed, is_mcp_installed
    from ..kernel.runtime import detect_runtime
    from ..kernel.runtime import get_cccc_mcp_stdio_command

    runtime = str(args.runtime or "").strip()
    project_path = Path(args.path or ".").resolve()

    # Supported runtimes
    # - claude/codex/droid/amp/auggie/neovate/gemini/kimi: MCP setup can be automated via their CLIs
    # - custom: user-provided runtime; MCP setup is manual (generic guidance only)
    SUPPORTED_RUNTIMES = [
        "claude",
        "codex",
        "droid",
        "amp",
        "auggie",
        "neovate",
        "gemini",
        "kimi",
        "custom",
    ]

    if runtime and runtime not in SUPPORTED_RUNTIMES:
        _print_json({
            "ok": False,
            "error": {
                "code": "unsupported_runtime",
                "message": f"Unsupported runtime: {runtime}. Supported: {', '.join(SUPPORTED_RUNTIMES)}",
            },
        })
        return 2

    results: dict[str, Any] = {"mcp": {}, "notes": []}

    cccc_cmd = get_cccc_mcp_stdio_command()
    auto_mcp_runtimes = tuple(name for name in SUPPORTED_RUNTIMES if name != "custom")

    def _cmd_line(parts: list[str]) -> str:
        return " ".join(shlex.quote(p) for p in parts)

    def _manual_setup(rt: str, *, runtime_available: bool) -> None:
        cmd = build_mcp_add_command(rt) or cccc_cmd
        display_cmd = cmd
        if runtime_available:
            try:
                display_cmd = resolve_subprocess_argv(cmd)
            except FileNotFoundError:
                display_cmd = cmd
        results["mcp"][rt] = {"mode": "manual", "command": _cmd_line(display_cmd)}
        if runtime_available:
            results["notes"].append(f"{rt}: MCP CLI failed; run the command shown in result.mcp.{rt}.command")
        else:
            results["notes"].append(f"{rt}: CLI not found; run the command shown in result.mcp.{rt}.command")

    def _auto_setup(rt: str) -> None:
        runtime_info = detect_runtime(rt)
        was_ready = is_mcp_installed(rt) if runtime_info.available else False
        if ensure_mcp_installed(rt, project_path, auto_mcp_runtimes=auto_mcp_runtimes):
            results["mcp"][rt] = {"mode": "auto", "status": "present" if was_ready else "added"}
            return
        _manual_setup(rt, runtime_available=runtime_info.available)

    # Runtime-specific setup
    runtimes_to_setup = [runtime] if runtime else SUPPORTED_RUNTIMES

    for rt in runtimes_to_setup:
        if rt in auto_mcp_runtimes:
            _auto_setup(rt)

        elif rt == "custom":
            results["mcp"]["custom"] = {
                "mode": "manual",
                "hint": f"Add an MCP stdio server named 'cccc' that runs: {_cmd_line(cccc_cmd)}",
            }
            results["notes"].append(
                "custom: MCP setup depends on your runtime. Add an MCP stdio server named 'cccc' that runs the command in result.mcp.custom.hint."
            )

    # Clean up empty notes
    if not results["notes"]:
        del results["notes"]

    _print_json({"ok": True, "result": results})
    return 0

def cmd_daemon(args: argparse.Namespace) -> int:
    if args.action == "status":
        resp = call_daemon({"op": "ping"})
        if resp.get("ok"):
            r = resp.get("result") if isinstance(resp.get("result"), dict) else {}
            print(f"ccccd: running pid={r.get('pid')} version={r.get('version')}")
            return 0
        print("ccccd: not running")
        return 1

    if args.action == "start":
        if _ensure_daemon_running():
            print("ccccd: running")
            return 0
        print("ccccd: failed to start")
        return 1

    if args.action == "stop":
        resp = call_daemon({"op": "shutdown"})
        if resp.get("ok"):
            print("ccccd: shutdown requested")
            return 0
        print("ccccd: not running")
        return 0

    return 2
