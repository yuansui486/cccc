from __future__ import annotations

"""Nuitka executable entry point for OneColleague."""

import importlib
import sys
from typing import Callable, Optional

try:
    from .util.process import INTERNAL_MODULE_ARG, INTERNAL_MODULE_SEPARATOR
except ImportError:
    from no1.util.process import INTERNAL_MODULE_ARG, INTERNAL_MODULE_SEPARATOR

_INTERNAL_MAIN_MODULES = {
    "no1.daemon_main",
    "no1.ports.web.main",
    "no1.ports.im",
    "no1.ports.im.bridge",
    "no1.ports.mcp.main",
}
_MODULE_IMPORT_ALIASES = {
    "no1.ports.im": "no1.ports.im.__main__",
}
_ARGV_MAIN_MODULES = {
    "no1.daemon_main",
    "no1.ports.web.main",
}


def _call_module_main(module_name: str, argv: list[str]) -> int:
    if module_name not in _INTERNAL_MAIN_MODULES:
        print(f"error: unsupported internal module: {module_name}", file=sys.stderr)
        return 2

    module = importlib.import_module(_MODULE_IMPORT_ALIASES.get(module_name, module_name))
    main = getattr(module, "main", None)
    if callable(main):
        if module_name in _ARGV_MAIN_MODULES:
            return int(main(argv))  # type: ignore[misc]
        previous = sys.argv[:]
        sys.argv = [module_name, *argv]
        try:
            return int(main())
        finally:
            sys.argv = previous

    if module_name == "no1.ports.im.bridge":
        start_bridge: Optional[Callable[[str, str], object]] = getattr(module, "start_bridge", None)
        if start_bridge is None:
            print("error: no1.ports.im.bridge has no start_bridge", file=sys.stderr)
            return 2
        if not argv:
            print("Usage: onecolleague --internal-module no1.ports.im.bridge -- <group_id> [platform]")
            return 1
        group_id = argv[0]
        platform = argv[1] if len(argv) > 1 else "telegram"
        start_bridge(group_id, platform)
        return 0

    print(f"error: internal module has no callable main: {module_name}", file=sys.stderr)
    return 2


def _dispatch_internal(argv: list[str]) -> int:
    if len(argv) < 2:
        print(f"Usage: onecolleague {INTERNAL_MODULE_ARG} <module> [{INTERNAL_MODULE_SEPARATOR} args...]", file=sys.stderr)
        return 2
    module_name = str(argv[1] or "").strip()
    remainder = argv[2:]
    if remainder and remainder[0] == INTERNAL_MODULE_SEPARATOR:
        remainder = remainder[1:]
    return _call_module_main(module_name, remainder)


def main(argv: Optional[list[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == INTERNAL_MODULE_ARG:
        return _dispatch_internal(args)

    return _run_cli(args)


def _run_cli(args: list[str]) -> int:
    try:
        from .cli import main as cli_main
    except ImportError:
        from no1.cli import main as cli_main

    return int(cli_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
