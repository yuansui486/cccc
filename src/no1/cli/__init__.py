from __future__ import annotations

"""OneColleague CLI package entrypoint."""

import sys
import types

# Re-export everything from submodules (mirroring main.py's imports).
# common.__all__ includes underscore names like _ensure_daemon_running.
from .common import *  # noqa: F401,F403
from .group_cmds import *  # noqa: F401,F403
from .actor_cmds import *  # noqa: F401,F403
from .messaging_cmds import *  # noqa: F401,F403
from .space_cmds import *  # noqa: F401,F403
from .im_cmds import *  # noqa: F401,F403
from .system_cmds import *  # noqa: F401,F403
from .main import build_parser, main  # noqa: F401

_PATCH_FORWARD_MODULES = (
    "no1.cli.common",
    "no1.cli.group_cmds",
    "no1.cli.actor_cmds",
    "no1.cli.messaging_cmds",
    "no1.cli.space_cmds",
    "no1.cli.im_cmds",
    "no1.cli.system_cmds",
    "no1.cli.common",
    "no1.cli.group_cmds",
    "no1.cli.actor_cmds",
    "no1.cli.messaging_cmds",
    "no1.cli.space_cmds",
    "no1.cli.im_cmds",
    "no1.cli.system_cmds",
)


class _CliModule(types.ModuleType):
    """Forward patched attrs on CLI package modules to split command modules."""

    def __setattr__(self, name: str, value: object) -> None:
        super().__setattr__(name, value)
        for mod_name in _PATCH_FORWARD_MODULES:
            mod = sys.modules.get(mod_name)
            if mod is None:
                continue
            if name in mod.__dict__:
                mod.__dict__[name] = value


sys.modules[__name__].__class__ = _CliModule
