from __future__ import annotations

import importlib
import shutil
import sys
from importlib.metadata import version
from pathlib import Path


EXPECTED_COMTYPES_VERSION = "1.4.16"
BINDING_MODULES = (
    "UIAutomationClient",
    "_944DE083_8FB8_45CF_BCB7_C477ACB2F897_0_1_0",
    "stdole",
    "_00020430_0000_0000_C000_000000000046_0_2_0",
)


def _import_staged_bindings() -> object:
    marker_missing = not hasattr(sys, "frozen")
    if marker_missing:
        sys.frozen = True  # type: ignore[attr-defined]
    try:
        importlib.invalidate_caches()
        for module_name in BINDING_MODULES:
            sys.modules.pop(f"comtypes.gen.{module_name}", None)
        return importlib.import_module("comtypes.gen.UIAutomationClient")
    finally:
        if marker_missing:
            delattr(sys, "frozen")


def main() -> int:
    if sys.platform != "win32":
        raise SystemExit("Windows UIAutomation bindings must be staged with a Windows Python runtime")

    installed_version = version("comtypes")
    if installed_version != EXPECTED_COMTYPES_VERSION:
        raise SystemExit(
            f"vendored UIAutomation bindings require comtypes {EXPECTED_COMTYPES_VERSION}; "
            f"installed version is {installed_version}"
        )

    import comtypes.gen  # type: ignore

    source_dir = Path(__file__).resolve().with_name("windows_comtypes_gen")
    source_files = [source_dir / f"{module_name}.py" for module_name in BINDING_MODULES]
    missing = [str(path) for path in source_files if not path.is_file()]
    if missing:
        raise SystemExit("missing vendored UIAutomation bindings: " + ", ".join(missing))

    package_paths = [Path(path) for path in comtypes.gen.__path__]  # type: ignore[attr-defined]
    if not package_paths:
        raise SystemExit("comtypes.gen has no writable package path")
    target_dir = package_paths[0]
    target_dir.mkdir(parents=True, exist_ok=True)

    for source in source_files:
        shutil.copy2(source, target_dir / source.name)

    generated = _import_staged_bindings()
    if not getattr(generated, "IUIAutomation", None):
        raise SystemExit("vendored UIAutomation bindings are missing IUIAutomation")
    if not (getattr(generated, "CUIAutomation8", None) or getattr(generated, "CUIAutomation", None)):
        raise SystemExit("vendored UIAutomation bindings are missing CUIAutomation")

    print(f"UIAutomation bindings: {getattr(generated, '__file__', '')}")
    print(f"comtypes: {installed_version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
