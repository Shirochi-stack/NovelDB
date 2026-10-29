"""Desktop build configuration: variants, asset names, the CI plan and the workflow wiring."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

import app_version
from desktop_build.variants import VARIANTS, asset_name, expected_assets, package_ext

ROOT = Path(__file__).resolve().parents[1]


def test_app_names_per_variant():
    assert app_version.app_name("full") == f"NovelDB{app_version.VERSION}"
    assert app_version.app_name("lite") == app_version.APP_NAME_LITE
    assert app_version.app_name("turbolite") == app_version.APP_NAME_TURBOLITE
    assert app_version.bundle_id("lite") == app_version.BUNDLE_ID_LITE
    assert app_version.bundle_id("turbolite") == app_version.BUNDLE_ID_TURBOLITE
    assert app_version.display_name("turbolite") == "NovelDB TurboLite"


def test_variant_contents():
    full, lite, turbo = VARIANTS["full"], VARIANTS["lite"], VARIANTS["turbolite"]
    assert "playwright" in full.all_hidden and "playwright" not in full.all_excludes
    assert "playwright" in lite.all_excludes and "PySide6.QtWebEngineWidgets" not in lite.all_excludes
    assert {"playwright", "PySide6.QtWebEngineCore", "PySide6.QtQuick"} <= set(turbo.all_excludes)
    for variant in VARIANTS.values():
        assert "tkinter" in variant.all_excludes
        assert not set(variant.all_hidden) & set(variant.all_excludes)


def test_asset_names():
    v = app_version.VERSION
    assert asset_name("full", "windows", "x86_64", ".exe") == f"NovelDB{v}-windows-x86_64.exe"
    assert asset_name("lite", "macos", "arm64", ".dmg", "hotfix2") == f"NovelDB{v}_Lite-macos-arm64_hotfix2.dmg"
    assert asset_name("turbolite", "linux", "x86_64", ".AppImage", "none") == f"NovelDB{v}_TurboLite-linux-x86_64.AppImage"
    assert package_ext("windows") == ".exe" and package_ext("windows", "onedir") == ".zip"


def test_expected_assets_include_the_windows_full_zip():
    names = expected_assets("windows", ["x86_64"], list(VARIANTS))
    assert len(names) == 4
    assert any(name.endswith("-windows-x86_64.zip") for name in names)
    assert len(expected_assets("macos", ["arm64", "x86_64"], list(VARIANTS), "hotfix1")) == 6


def test_ci_plan_output():
    done = subprocess.run([sys.executable, str(ROOT / "scripts" / "build_desktop.py"), "--plan", "macos"],
                          capture_output=True, text=True, cwd=ROOT, env={**__import__("os").environ, "LABEL": "hotfix4"},
                          check=True)
    lines = dict(line.split("=", 1) for line in done.stdout.strip().splitlines())
    matrix = json.loads(lines["matrix"])["include"]
    assert {(m["runner"], m["arch"]) for m in matrix} == {("macos-15", "arm64"), ("macos-15-intel", "x86_64")}
    assert len(matrix) == 6
    assert all(name.endswith("_hotfix4.dmg") for name in lines["expected"].split())


def test_workflows_call_the_reusable_build():
    for os_id in ("windows", "macos", "linux"):
        text = (ROOT / ".github" / "workflows" / f"build-{os_id}.yml").read_text(encoding="utf-8")
        assert "uses: ./.github/workflows/desktop-build.yml" in text
        assert f"os: {os_id}" in text
        assert "types: [published]" in text
    reusable = (ROOT / ".github" / "workflows" / "desktop-build.yml").read_text(encoding="utf-8")
    assert "gh release upload" in reusable and "--clobber" in reusable
    assert "tests/test_desktop_build.py" in reusable


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_the_appdir_is_opened_to_every_user(tmp_path):
    import importlib.util

    spec = importlib.util.spec_from_file_location("build_desktop", ROOT / "scripts" / "build_desktop.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    locales = tmp_path / "AppDir" / "usr" / "lib" / "locales"
    locales.mkdir(parents=True)
    (locales / "en-US.pak").write_bytes(b"x")
    (tmp_path / "AppDir" / "run").write_bytes(b"#!/bin/sh")
    locales.chmod(0o700)
    (locales / "en-US.pak").chmod(0o600)
    (tmp_path / "AppDir" / "run").chmod(0o700)
    build.open_to_everyone(tmp_path / "AppDir")
    assert locales.stat().st_mode & 0o777 == 0o755
    assert (locales / "en-US.pak").stat().st_mode & 0o777 == 0o644
    assert (tmp_path / "AppDir" / "run").stat().st_mode & 0o777 == 0o755
