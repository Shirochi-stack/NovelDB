"""Build, check and package one desktop variant for the current OS.

    python scripts/build_desktop.py --variant full --package --verify --smoke
    python scripts/build_desktop.py --variant lite --mode onedir         # local debugging

Windows builds a single exe (and, for Full with --zip, a compressed folder build as a .zip);
macOS builds an .app inside a DMG; Linux builds an AppImage. Files land in dist-desktop/ under
release asset names (see desktop_build/variants.py).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import app_version  # noqa: E402
from desktop_build.variants import VARIANTS, arch_name, asset_name, os_name  # noqa: E402

GITHUB_ASSET_LIMIT = 2 * 1024 ** 3
WARN_AT = int(GITHUB_ASSET_LIMIT * 0.85)


def log(text: str) -> None:
    print(f"[build] {text}", flush=True)


def run(cmd, env=None, cwd=ROOT) -> None:
    log(" ".join(str(c) for c in cmd))
    subprocess.run([str(c) for c in cmd], check=True, env=env, cwd=cwd)


# -- Playwright browsers -------------------------------------------------------------------------

def _revision(name: str) -> int:
    match = re.search(r"-(\d+)$", name)
    return int(match.group(1)) if match else -1


def prepare_browsers(folder: Path) -> Path:
    """Install Chromium into an isolated folder and keep only the newest revision of each."""
    env = dict(os.environ, PLAYWRIGHT_BROWSERS_PATH=str(folder))
    run([sys.executable, "-m", "playwright", "install", "chromium"], env=env)
    for prefix in ("chromium-", "chromium_headless_shell-"):
        found = sorted((p for p in folder.iterdir() if p.is_dir() and p.name.startswith(prefix)),
                       key=lambda p: _revision(p.name))
        if not found:
            raise SystemExit(f"Playwright did not install {prefix}*")
        for old in found[:-1]:
            log(f"removing old browser revision {old.name}")
            shutil.rmtree(old, ignore_errors=True)
    for extra in folder.iterdir():  # ffmpeg, firefox, webkit: not used
        if extra.is_dir() and not extra.name.startswith(("chromium-", "chromium_headless_shell-")):
            shutil.rmtree(extra, ignore_errors=True)
    if sys.platform == "darwin":
        # PyInstaller re-signs what it bundles; Google's nested signatures break that.
        for path in folder.rglob("_CodeSignature"):
            shutil.rmtree(path, ignore_errors=True)
    return folder


# -- PyInstaller ---------------------------------------------------------------------------------

def write_build_info(variant: str, mode: str, arch: str = "", label: str = "") -> None:
    """The variant, plus what the app updater needs (noveldb/core/app_update.py).

    The release workflow sets NOVELDB_BUILD (the Unix time it planned the build) and NOVELDB_TAG;
    builds without them are development builds and never offer updates.
    """
    build = os.environ.get("NOVELDB_BUILD", "").strip()
    (ROOT / "_build_info.py").write_text(
        "# Written by scripts/build_desktop.py; not committed.\n"
        f"VARIANT = {variant!r}\nBUILD_MODE = {mode!r}\nVERSION = {app_version.VERSION!r}\n"
        f"BUILD = {int(build) if build.isdigit() else 0}\n"
        f"TAG = {os.environ.get('NOVELDB_TAG', '').strip()!r}\n"
        f"LABEL = {(label or os.environ.get('LABEL', '') or 'none').strip()!r}\n"
        f"COMMIT = {os.environ.get('GITHUB_SHA', '').strip()!r}\n"
        f"OS = {os_name()!r}\nARCH = {arch or arch_name('')!r}\n",
        encoding="utf-8",
    )


def macos_icon() -> None:
    """icon.icns from icon.ico (sips + iconutil ship with macOS)."""
    if sys.platform != "darwin" or (ROOT / "icon.icns").exists():
        return
    work = Path(tempfile.mkdtemp())
    iconset = work / "icon.iconset"
    iconset.mkdir()
    base = work / "base.png"
    shutil.copy2(ROOT / "assets" / "app-icon.png", base)
    if not base.exists():
        return
    for size in (16, 32, 64, 128, 256, 512):
        subprocess.run(["sips", "-z", str(size), str(size), str(base), "--out", str(iconset / f"icon_{size}x{size}.png")],
                       check=False, capture_output=True)
        subprocess.run(["sips", "-z", str(size * 2), str(size * 2), str(base), "--out",
                        str(iconset / f"icon_{size}x{size}@2x.png")], check=False, capture_output=True)
    subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(ROOT / "icon.icns")], check=False)


def pyinstaller(variant: str, mode: str, browsers: Path | None, arch: str, label: str = "") -> Path:
    work = ROOT / "build" / "desktop" / f"{variant}-{mode}"
    dist = work / "dist"
    shutil.rmtree(dist, ignore_errors=True)
    env = dict(os.environ, NOVELDB_VARIANT=variant, NOVELDB_MODE=mode)
    if browsers is not None:
        env["NOVELDB_PW_BROWSERS"] = str(browsers)
    if sys.platform == "darwin":
        env["NOVELDB_TARGET_ARCH"] = arch
    write_build_info(variant, mode, arch, label)
    run([sys.executable, "-m", "PyInstaller", str(ROOT / "desktop_build" / "noveldb.spec"), "--noconfirm",
         "--clean", "--distpath", str(dist), "--workpath", str(work / "work")], env=env)
    return dist


def built_executable(dist: Path, variant: str, mode: str) -> Path:
    name = app_version.app_name(variant)
    if sys.platform == "win32":
        return dist / f"{name}.exe" if mode == "onefile" else dist / name / f"{name}.exe"
    if sys.platform == "darwin" and mode == "onedir":
        return dist / f"{name}.app" / "Contents" / "MacOS" / name
    return dist / name if mode == "onefile" else dist / name / name


# -- checks ----------------------------------------------------------------------------------------

def smoke(executable: Path, variant: str, report_path: Path) -> dict:
    """Start the built app with --smoke and check the report against the variant."""
    expected = VARIANTS[variant]
    checks = ["pdf"]
    if expected.webengine:
        checks += ["webengine", "catalog"]
    if expected.playwright:
        checks.append("browser")
    home = Path(tempfile.mkdtemp(prefix="noveldb-smoke-"))
    env = dict(os.environ, NOVELDB_HOME=str(home), NOVELDB_SMOKE_DELAY_MS="500")
    env.pop("PLAYWRIGHT_BROWSERS_PATH", None)  # the bundled browsers must be found on their own
    cmd = [str(executable), "--smoke", "--smoke-report", str(report_path), "--smoke-checks", ",".join(checks)]
    if sys.platform.startswith("linux") and not env.get("DISPLAY"):
        # A virtual X screen is closer to a desktop than offscreen: the Catalog crash only showed there.
        if shutil.which("xvfb-run"):
            cmd = ["xvfb-run", "-a", "-s", "-screen 0 1600x1000x24"] + cmd
        else:
            env.setdefault("QT_QPA_PLATFORM", "offscreen")
    log(" ".join(cmd))
    done = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=600)
    if not report_path.exists():
        raise SystemExit(f"Smoke test wrote no report (exit {done.returncode}):\n{done.stdout}\n{done.stderr}")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    problems = []
    if report.get("variant") != variant:
        problems.append(f"variant is {report.get('variant')!r}")
    for feature, wanted in expected.expected_features().items():
        if report.get("features", {}).get(feature) != wanted:
            problems.append(f"{feature} should be {'present' if wanted else 'absent'}")
    if report.get("forbidden_loaded"):
        problems.append(f"loaded {report['forbidden_loaded']}")
    for name, result in (report.get("checks") or {}).items():
        if not result.get("ok"):
            problems.append(f"{name} check failed: {result}")
    if not report.get("window"):
        problems.append("the window did not open")
    log("smoke report: " + json.dumps(report))
    if problems:
        raise SystemExit("Smoke test failed: " + "; ".join(problems))
    return report


def verify_tree(folder: Path, variant: str) -> None:
    """Folder builds: required and forbidden files for the variant."""
    expected = VARIANTS[variant]
    names = {p.name.lower() for p in folder.rglob("*")}
    joined = "\n".join(str(p.relative_to(folder)).replace("\\", "/").lower() for p in folder.rglob("*"))
    problems = []
    has_webengine = any("qtwebengineprocess" in n for n in names)
    has_browsers = "ms-playwright/chromium" in joined
    if has_webengine != expected.webengine:
        problems.append(f"QtWebEngine {'missing' if expected.webengine else 'present'}")
    if has_browsers != expected.playwright:
        problems.append(f"bundled Chromium {'missing' if expected.playwright else 'present'}")
    if "tkinter" in joined or "_tkinter" in joined:
        problems.append("tkinter is bundled")
    if "qtwebengine_devtools_resources" in joined:
        problems.append("QtWebEngine devtools resources are bundled")
    if problems:
        raise SystemExit("Bundle check failed: " + "; ".join(problems))
    log(f"bundle check passed for {variant}")


def check_size(path: Path) -> None:
    size = path.stat().st_size
    log(f"{path.name}: {size / 1024 ** 2:,.1f} MB")
    if size >= GITHUB_ASSET_LIMIT:
        raise SystemExit(f"{path.name} is over GitHub's 2 GB release asset limit")
    if size >= WARN_AT:
        print(f"::warning::{path.name} is {size / 1024 ** 3:.2f} GB, close to GitHub's 2 GB asset limit")


# -- packaging ---------------------------------------------------------------------------------------

def zip_folder(folder: Path, target: Path) -> Path:
    target.unlink(missing_ok=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(folder.rglob("*")):
            info_name = str(Path(folder.name) / path.relative_to(folder)).replace("\\", "/")
            if path.is_dir():
                continue
            archive.write(path, info_name)
    return target


def make_dmg(app: Path, target: Path, volume: str) -> Path:
    staging = Path(tempfile.mkdtemp(prefix="noveldb-dmg-"))
    shutil.copytree(app, staging / app.name, symlinks=True)
    (staging / "Applications").symlink_to("/Applications")
    target.unlink(missing_ok=True)
    # hdiutil fails now and then with "Resource busy" on CI runners (a scanner holds the image).
    for attempt in range(1, 6):
        try:
            run(["hdiutil", "create", "-volname", volume, "-srcfolder", staging, "-ov", "-format", "UDZO", target])
            return target
        except subprocess.CalledProcessError:
            if attempt == 5:
                raise
            log(f"hdiutil failed (attempt {attempt}); retrying in {attempt * 10} s")
            target.unlink(missing_ok=True)
            time.sleep(attempt * 10)
    return target


def open_to_everyone(root: Path) -> None:
    """`chmod -R a+rX`: the AppImage runs as whoever starts it, and appimagetool refuses an AppDir with
    entries other users cannot read (the trimmed Qt locales folder comes out 0700)."""
    for path in [root, *root.rglob("*")]:
        if path.is_symlink():
            continue
        mode = path.stat().st_mode
        wanted = mode | 0o444 | (0o111 if path.is_dir() or mode & 0o111 else 0)
        if wanted != mode:
            path.chmod(wanted)


def make_appimage(folder: Path, variant: str, target: Path, arch: str) -> Path:
    name = app_version.app_name(variant)
    display = app_version.display_name(variant)
    appdir = Path(tempfile.mkdtemp(prefix="noveldb-appdir-")) / f"{name}.AppDir"
    shutil.copytree(folder, appdir / "usr" / "lib" / name, symlinks=True)
    (appdir / "AppRun").write_text(
        "#!/bin/sh\n"
        'HERE="$(dirname "$(readlink -f "$0")")"\n'
        "export QTWEBENGINE_DISABLE_SANDBOX=${QTWEBENGINE_DISABLE_SANDBOX:-1}\n"
        f'exec "$HERE/usr/lib/{name}/{name}" "$@"\n',
        encoding="utf-8",
    )
    os.chmod(appdir / "AppRun", 0o755)
    icon_id = name.lower()
    (appdir / f"{icon_id}.desktop").write_text(
        "[Desktop Entry]\nType=Application\n"
        f"Name={display}\nExec={name}\nIcon={icon_id}\nCategories=Utility;Network;\nTerminal=false\n",
        encoding="utf-8",
    )
    from PIL import Image

    with Image.open(ROOT / "assets" / "app-icon.png") as image:
        image.convert("RGBA").resize((256, 256)).save(appdir / f"{icon_id}.png")
    tool = os.environ.get("APPIMAGETOOL") or shutil.which("appimagetool")
    if not tool:
        raise SystemExit("appimagetool not found (set APPIMAGETOOL)")
    target.unlink(missing_ok=True)
    open_to_everyone(appdir)
    env = dict(os.environ, ARCH={"x86_64": "x86_64", "arm64": "aarch64"}.get(arch, arch))
    run([tool, "--appimage-extract-and-run", "--no-appstream", appdir, target], env=env)
    return target


RUNNERS = {
    "windows": (("windows-2022", "x86_64"),),
    "macos": (("macos-15", "arm64"), ("macos-15-intel", "x86_64")),
    "linux": (("ubuntu-22.04", "x86_64"),),
}


def plan(os_id: str, variants_text: str, label: str) -> list[str]:
    """GitHub output lines for the reusable workflow: the build matrix and the expected assets."""
    from desktop_build.variants import expected_assets

    wanted = (variants_text or "all").strip()
    variants = list(VARIANTS) if wanted == "all" else [v.strip() for v in wanted.split(",") if v.strip()]
    unknown = [v for v in variants if v not in VARIANTS]
    if unknown or os_id not in RUNNERS:
        raise SystemExit(f"Unknown OS or variants: {os_id} {unknown}")
    include = [{"variant": v, "runner": runner, "arch": arch} for runner, arch in RUNNERS[os_id] for v in variants]
    arches = [arch for _runner, arch in RUNNERS[os_id]]
    return [
        "matrix=" + json.dumps({"include": include}),
        "expected=" + " ".join(expected_assets(os_id, arches, variants, label)),
    ]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--plan", metavar="OS", help="print the CI build matrix for windows, macos or linux")
    parser.add_argument("--plan-variants", default="all")
    parser.add_argument("--variant", choices=list(VARIANTS))
    parser.add_argument("--mode", choices=("auto", "onefile", "onedir"), default="auto")
    parser.add_argument("--arch", default="", help="defaults to this machine's architecture")
    parser.add_argument("--label", default="", help="asset label such as hotfix2 ('' or none: plain names)")
    parser.add_argument("--out", default=str(ROOT / "dist-desktop"))
    parser.add_argument("--zip", action="store_true", help="Windows: also build the compressed folder .zip")
    parser.add_argument("--package", action="store_true")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args(argv)
    if args.plan:
        print("\n".join(plan(args.plan, args.plan_variants, os.environ.get("LABEL", args.label))))
        return 0
    if not args.variant:
        parser.error("--variant is required")

    os_id, arch = os_name(), arch_name(args.arch)
    variant = args.variant
    mode = args.mode if args.mode != "auto" else ("onefile" if os_id == "windows" else "onedir")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    browsers = prepare_browsers(ROOT / "build" / "ms-playwright") if VARIANTS[variant].playwright else None
    macos_icon()

    modes = [mode]
    if os_id == "windows" and args.zip and mode == "onefile":
        modes.append("onedir")
    produced = []
    try:
        for build_mode in modes:
            dist = pyinstaller(variant, build_mode, browsers, arch, args.label)
            executable = built_executable(dist, variant, build_mode)
            if not executable.exists():
                raise SystemExit(f"PyInstaller did not produce {executable}")
            if args.verify and build_mode == "onedir":
                tree = executable.parent if os_id != "macos" else dist / f"{app_version.app_name(variant)}.app"
                verify_tree(tree, variant)
            if args.smoke:
                smoke(executable, variant, out / f"smoke-{variant}-{os_id}-{arch}-{build_mode}.json")
            if not args.package:
                continue
            if os_id == "windows":
                if build_mode == "onefile":
                    target = out / asset_name(variant, os_id, arch, ".exe", args.label)
                    shutil.copy2(executable, target)
                else:
                    target = zip_folder(executable.parent, out / asset_name(variant, os_id, arch, ".zip", args.label))
            elif os_id == "macos":
                target = make_dmg(dist / f"{app_version.app_name(variant)}.app",
                                  out / asset_name(variant, os_id, arch, ".dmg", args.label),
                                  app_version.display_name(variant))
            else:
                target = make_appimage(executable.parent, variant,
                                       out / asset_name(variant, os_id, arch, ".AppImage", args.label), arch)
            check_size(target)
            produced.append(target)
    finally:
        (ROOT / "_build_info.py").unlink(missing_ok=True)
    for path in produced:
        log(f"ready: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
