from pathlib import Path
import tarfile
import zipfile

import pytest


def _project_version() -> str:
    for line in Path("pyproject.toml").read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("version"):
            return line.split("=", 1)[1].strip().strip('"')
    raise AssertionError("project version not found")


def _metadata_requires_pywinpty(metadata: str) -> bool:
    return any(
        line.startswith("Requires-Dist: pywinpty>=2.0;") and 'platform_system == "Windows"' in line
        for line in metadata.splitlines()
    )


def test_build_package_ps1_compiles_no1_package() -> None:
    script = Path("scripts/build_package.ps1").read_text(encoding="utf-8-sig")

    assert 'src\\no1' in script
    assert 'src\\cccc' not in script
    assert 'src/cccc' not in script


def test_build_package_ps1_verifies_windows_pty_wheel_dependency() -> None:
    script = Path("scripts/build_package.ps1").read_text(encoding="utf-8-sig")

    assert "Test-WindowsPtyWheel" in script
    assert "pip install $WheelPath" in script
    assert "import json, winpty" in script
    assert "pty_support_details" in script


def test_built_wheel_metadata_keeps_windows_pywinpty_dependency() -> None:
    version = _project_version()
    wheel = Path("dist") / f"no1-{version}-py3-none-any.whl"
    if not wheel.exists():
        pytest.skip(f"built wheel not found: {wheel}")

    with zipfile.ZipFile(wheel) as archive:
        metadata = archive.read(f"no1-{version}.dist-info/METADATA").decode("utf-8")

    assert _metadata_requires_pywinpty(metadata)


def test_built_sdist_metadata_keeps_windows_pywinpty_dependency() -> None:
    version = _project_version()
    sdist = Path("dist") / f"no1-{version}.tar.gz"
    if not sdist.exists():
        pytest.skip(f"built sdist not found: {sdist}")

    with tarfile.open(sdist) as archive:
        member = archive.getmember(f"no1-{version}/PKG-INFO")
        extracted = archive.extractfile(member)
        assert extracted is not None
        metadata = extracted.read().decode("utf-8")

    assert _metadata_requires_pywinpty(metadata)
