from __future__ import annotations

import sys
from typing import Any, Dict


def _platform_name() -> str:
    return str(sys.platform or "unknown")


def computer_control_availability() -> Dict[str, Any]:
    platform = _platform_name()
    supported = platform == "win32"
    return {
        "supported": supported,
        "platform": platform,
        "reason": "" if supported else "windows_only",
    }


def computer_control_supported() -> bool:
    return bool(computer_control_availability()["supported"])
