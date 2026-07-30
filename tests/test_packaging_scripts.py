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


def test_project_scripts_keep_wheel_and_source_entrypoints_unchanged() -> None:
    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")

    assert 'onecolleague = "no1.cli:main"' in pyproject
    assert 'onecolleagued = "no1.daemon_main:main"' in pyproject
    assert "no1.frozen_entry" not in pyproject


def test_build_nuitka_ps1_uses_frozen_entry_and_packaged_smoke_home() -> None:
    script = Path("scripts/build_nuitka_standalone.ps1").read_text(encoding="utf-8-sig")

    assert "--main=src\\no1\\frozen_entry.py" in script
    assert "--output-folder-name=no1.frozen_entry.dist" in script
    assert '"uv"' in script
    assert "Resolve-ProjectPython" in script
    assert ".venv\\Scripts\\python.exe" in script
    assert "Invoke-CheckedNative -FilePath $pythonPath -ArgumentList $nuitkaArgs" in script
    assert "--output-filename=onecolleague.exe" in script
    assert "--include-package=no1" in script
    assert "Prepare-WindowsUIAutomationBindings" in script
    assert "stage_windows_uia_bindings.py" in script
    assert "--include-package=comtypes.gen" in script
    assert "--include-module=comtypes.gen.UIAutomationClient" in script
    assert "--include-module=comtypes.gen._944DE083_8FB8_45CF_BCB7_C477ACB2F897_0_1_0" in script
    assert "--include-module=comtypes.gen.stdole" in script
    assert "--include-module=comtypes.gen._00020430_0000_0000_C000_000000000046_0_2_0" in script
    assert "--include-package-data=no1" in script
    assert "--include-distribution-metadata=no1" in script
    assert "src\\no1\\ports\\web\\dist=no1\\ports\\web\\dist" in script
    assert "src\\no1\\resources=no1\\resources" in script
    assert "--nofollow-import-to=lark_oapi.*" in script
    assert "Get-ProjectVersion" in script
    assert "Test-OneColleagueVersion" in script
    assert "Nuitka smoke version mismatch" in script
    assert '$actualVersion -eq "0.0.0"' in script
    assert "onecolleague-nuitka-smoke-" in script
    assert "$env:ONECOLLEAGUE_HOME = $script:smokeHome" in script
    assert "$env:CCCC_HOME = $script:smokeHome" in script
    assert "daemon\", \"start" in script
    assert '"doctor", "--require-native-picker"' in script
    assert "daemon\", \"status" in script
    assert "& $ExePath daemon stop" in script
    assert "SkipSmokeTests" in script


def test_vendored_windows_uia_bindings_are_complete() -> None:
    bindings_dir = Path("scripts/windows_comtypes_gen")
    expected = {
        "UIAutomationClient.py",
        "_944DE083_8FB8_45CF_BCB7_C477ACB2F897_0_1_0.py",
        "stdole.py",
        "_00020430_0000_0000_C000_000000000046_0_2_0.py",
    }

    assert expected.issubset({path.name for path in bindings_dir.glob("*.py")})
    assert "IUIAutomation" in (bindings_dir / "UIAutomationClient.py").read_text(encoding="utf-8")
    stage_script = Path("scripts/stage_windows_uia_bindings.py").read_text(encoding="utf-8")
    assert 'EXPECTED_COMTYPES_VERSION = "1.4.16"' in stage_script
    assert "UIA_wine_private" not in stage_script


def test_build_nuitka_macos_script_uses_native_standalone_and_smoke_checks() -> None:
    script = Path("scripts/build_nuitka_standalone.sh").read_text(encoding="utf-8")

    assert '[[ "$(uname -s)" == "Darwin" ]]' in script
    assert "--standalone" in script
    assert "--output-filename=onecolleague" in script
    assert "--main=\"$ROOT_DIR/src/no1/frozen_entry.py\"" in script
    assert '--include-data-dir="$WEB_DIST_DIR=no1/ports/web/dist"' in script
    assert '--include-data-dir="$RESOURCES_DIR=no1/resources"' in script
    assert "onecolleague-nuitka-smoke" in script
    assert 'export ONECOLLEAGUE_HOME="$SMOKE_HOME"' in script
    assert 'export CCCC_HOME="$SMOKE_HOME"' in script
    assert '"$BIN_PATH" daemon start' in script
    assert '"$BIN_PATH" daemon status' in script
    assert 'if [[ "$SKIP_SMOKE_TESTS" == 0 ]]; then' in script
    assert 'ditto -c -k' in script
    assert "macos-arm64.zip" in script


def test_nuitka_workflow_targets_native_macos_arm64_and_uploads_zip() -> None:
    workflow = Path(".github/workflows/nuitka-standalone.yml").read_text(encoding="utf-8")

    assert "runs-on: macos-15" in workflow
    assert 'test "$(uname -m)" = arm64' in workflow
    assert "bash scripts/build_nuitka_standalone.sh" in workflow
    assert "onecolleague-nuitka-macos-arm64" in workflow
    assert "macos-arm64.zip" in workflow


def test_install_smoke_ps1_verifies_wheel_and_editable_with_temp_home() -> None:
    script = Path("scripts/test_install_smoke.ps1").read_text(encoding="utf-8-sig")

    assert '[ValidateSet("Wheel", "Editable", "Both")]' in script
    assert '"uv"' in script
    assert '"venv", $venvRoot, "--python", $PythonSelector' in script
    assert '"pip", "install", "--python", $venvPython, $resolvedWheel' in script
    assert '"pip", "install", "--python", $venvPython, "-e", $rootDir' in script
    assert "onecolleague-home-smoke-" in script
    assert "$env:ONECOLLEAGUE_HOME = $homeRoot" in script
    assert "$env:CCCC_HOME = $homeRoot" in script
    assert "version" in script
    assert "doctor" in script
    assert "daemon\", \"start" in script
    assert "daemon\", \"status" in script
    assert "& $exePath daemon stop" in script


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
