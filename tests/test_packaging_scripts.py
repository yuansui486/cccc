from pathlib import Path


def test_build_package_ps1_compiles_no1_package() -> None:
    script = Path("scripts/build_package.ps1").read_text(encoding="utf-8-sig")

    assert 'src\\no1' in script
    assert 'src\\cccc' not in script
    assert 'src/cccc' not in script
