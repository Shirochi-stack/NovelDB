"""Updates of the desktop app itself, from this repository's GitHub releases.

Every release carries `update-<os>.json` (written by .github/workflows/desktop-build.yml): each desktop file
with its variant, architecture, kind, build number, size and SHA-256. A release build knows its own build number
from `_build_info` (written by scripts/build_desktop.py). The app compares the two, downloads the newer file,
checks its size and SHA-256 against the manifest, and only then replaces itself: a small helper script waits
for the app to quit, swaps the files in, and starts the new copy. Nothing is downloaded or installed until the
user asks for it, and an unverified file is never installed.

The Android and iOS apps follow the same contract (`update-android.json`, `update-ios.json`).
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from noveldb.core import paths
from noveldb.core.net_timeouts import request_timeout

RELEASES_URL = "https://github.com/Shirochi-stack/NovelDB/releases/"
CHECK_EVERY = 24 * 3600  # seconds between automatic checks
SCHEMA = 1
KINDS = ("exe", "zip", "dmg", "appimage")

_lock = threading.Lock()


class UpdateError(Exception):
    """A download or install that could not finish; the message is shown to the user."""


class UpdateCancelled(UpdateError):
    pass


def releases_url() -> str:
    """The releases address; `NOVELDB_UPDATE_BASE` points tests and manual checks at a local server."""
    base = os.environ.get("NOVELDB_UPDATE_BASE", "").strip()
    return base.rstrip("/") + "/" if base else RELEASES_URL


def version_text(tag: str, label: str) -> str:
    """"v98" or "v98 hotfix3"."""
    return f"{tag} {label}" if label and label != "none" else tag


def os_id() -> str:
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def arch_id() -> str:
    value = platform.machine().lower()
    if value in ("amd64", "x86_64", "x64"):
        return "x86_64"
    if value in ("arm64", "aarch64"):
        return "arm64"
    return value


# -- this copy of the app ----------------------------------------------------------------------------

@dataclass(frozen=True)
class BuildInfo:
    build: int = 0  # Unix time the release workflow planned the build; 0 = development build
    tag: str = ""
    label: str = ""
    commit: str = ""
    variant: str = ""
    os: str = ""
    arch: str = ""
    kind: str = ""  # exe, zip, dmg, appimage; "" when this copy cannot replace itself

    @property
    def can_update(self) -> bool:
        return self.build > 0 and self.kind in KINDS

    def describe(self) -> str:
        if not self.build:
            return "Development build"
        return f"{version_text(self.tag, self.label)}, build {self.build}"


def build_info() -> BuildInfo:
    """What the release workflow wrote into this build; an empty BuildInfo when run from source."""
    if not paths.is_frozen():
        return BuildInfo()
    try:
        import _build_info as info  # written by scripts/build_desktop.py
    except ImportError:
        return BuildInfo()
    try:
        build = int(getattr(info, "BUILD", 0) or 0)
    except (TypeError, ValueError):
        build = 0
    system = getattr(info, "OS", "") or os_id()
    mode = getattr(info, "BUILD_MODE", "")
    if system == "windows":
        kind = "exe" if mode == "onefile" else "zip"
    elif system == "macos":
        kind = "dmg" if _app_bundle() is not None else ""
    else:
        kind = "appimage" if os.environ.get("APPIMAGE") else ""
    return BuildInfo(build=build, tag=str(getattr(info, "TAG", "") or ""), label=str(getattr(info, "LABEL", "") or ""),
                     commit=str(getattr(info, "COMMIT", "") or ""), variant=str(getattr(info, "VARIANT", "") or ""),
                     os=system, arch=str(getattr(info, "ARCH", "") or arch_id()), kind=kind)


def _app_bundle() -> Optional[Path]:
    for parent in Path(sys.executable).resolve().parents:
        if parent.suffix == ".app":
            return parent
    return None


# -- the release manifest ----------------------------------------------------------------------------

@dataclass(frozen=True)
class Asset:
    name: str
    variant: str
    arch: str
    kind: str
    build: int
    label: str
    commit: str
    size: int
    sha256: str


@dataclass(frozen=True)
class Update:
    tag: str
    release_url: str
    asset: Asset

    @property
    def title(self) -> str:
        return version_text(self.tag, self.asset.label)

    @property
    def url(self) -> str:
        return f"{releases_url()}download/{self.tag}/{self.asset.name}"


_PLAIN_NAME = re.compile(r"^[A-Za-z0-9._-]+$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def parse_manifest(data: bytes, platform_name: str) -> Optional[tuple[str, str, list[Asset]]]:
    """(tag, release_url, assets) from an update manifest, or None when it is not one this app reads."""
    try:
        manifest = json.loads(data)
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(manifest, dict) or manifest.get("schema") != SCHEMA:
        return None
    if manifest.get("platform") != platform_name:
        return None
    tag = manifest.get("tag")
    if not isinstance(tag, str) or not _PLAIN_NAME.match(tag):
        return None
    release_url = manifest.get("release_url")
    if not isinstance(release_url, str) or not release_url.startswith("https://"):
        release_url = f"{RELEASES_URL}tag/{tag}"
    assets = []
    for item in manifest.get("assets") or []:
        if not isinstance(item, dict):
            continue
        name, sha = item.get("name"), str(item.get("sha256", "")).lower()
        build, size = item.get("build"), item.get("size")
        if not (isinstance(name, str) and _PLAIN_NAME.match(name) and ".." not in name and _SHA256.match(sha)):
            continue
        if not (isinstance(build, int) and build > 0 and isinstance(size, int) and size > 0):
            continue
        assets.append(Asset(name=name, variant=str(item.get("variant", "")), arch=str(item.get("arch", "")),
                            kind=str(item.get("kind", "")), build=build, label=str(item.get("label", "")),
                            commit=str(item.get("commit", "")), size=size, sha256=sha))
    return tag, release_url, assets


def find_update(info: BuildInfo, data: bytes) -> Optional[Update]:
    """The newer build of this copy's variant, architecture and kind, if the manifest has one."""
    parsed = parse_manifest(data, info.os)
    if parsed is None:
        return None
    tag, release_url, assets = parsed
    for asset in assets:
        if (asset.variant, asset.arch, asset.kind) == (info.variant, info.arch, info.kind):
            return Update(tag, release_url, asset) if asset.build > info.build else None
    return None


# -- checking ----------------------------------------------------------------------------------------

@dataclass(frozen=True)
class CheckResult:
    status: str  # "update", "current", "none" (no update information), "off" (cannot update), "failed"
    message: str
    update: Optional[Update] = None


def _http_get(url: str, timeout: float) -> bytes:
    import requests

    response = requests.get(url, timeout=request_timeout(timeout), headers={"User-Agent": "NovelDB"})
    if response.status_code == 404:
        raise FileNotFoundError(url)
    response.raise_for_status()
    return response.content


def _state_path() -> Path:
    return paths.data_dir() / "app-update.json"


def last_checked() -> float:
    try:
        return float(json.loads(_state_path().read_text(encoding="utf-8")).get("checked", 0))
    except (OSError, ValueError, TypeError, AttributeError):
        return 0.0


def _mark_checked() -> None:
    path = _state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"checked": time.time()}), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass


def check(info: Optional[BuildInfo] = None, fetch: Callable[[str, float], bytes] = _http_get,
          timeout: float = 20.0) -> CheckResult:
    """Look for a newer build of this copy in the latest release."""
    info = info or build_info()
    if not info.build:
        return CheckResult("off", "Development build: app updates are off.")
    if not info.can_update:
        return CheckResult("off", "This copy of NovelDB can't update itself. Run the .exe, the .app or the "
                                  "AppImage from the release to get updates.")
    with _lock:
        _mark_checked()
    url = f"{releases_url()}latest/download/update-{info.os}.json"
    try:
        data = fetch(url, timeout)
    except FileNotFoundError:
        return CheckResult("none", "The latest release has no update information yet.")
    except Exception as exc:  # network and HTTP errors
        return CheckResult("failed", f"Could not check for app updates: {exc}")
    update = find_update(info, data)
    if update is not None:
        return CheckResult("update", f"NovelDB {update.title} is available.", update)
    if parse_manifest(data, info.os) is None:
        return CheckResult("none", "The latest release's update information could not be read.")
    return CheckResult("current", f"NovelDB is up to date ({info.describe()}).")


def check_if_due(info: Optional[BuildInfo] = None, every: float = CHECK_EVERY,
                 fetch: Callable[[str, float], bytes] = _http_get) -> Optional[CheckResult]:
    """The automatic check: at most once per `every` seconds, and only for copies that can update."""
    info = info or build_info()
    if not info.can_update or time.time() - last_checked() < every:
        return None
    return check(info, fetch)


# -- downloading -------------------------------------------------------------------------------------

def updates_dir() -> Path:
    return paths.data_dir() / "updates"


def _http_stream(url: str, timeout: float):
    import requests

    response = requests.get(url, timeout=request_timeout(timeout), stream=True, headers={"User-Agent": "NovelDB"})
    response.raise_for_status()
    return response.iter_content(chunk_size=1 << 20)


def download(update: Update, folder: Optional[Path] = None,
             progress: Callable[[int, int], None] = lambda _done, _total: None,
             cancelled: Callable[[], bool] = lambda: False,
             stream: Callable[[str, float], object] = _http_stream) -> Path:
    """Download the update and check its size and SHA-256; the verified file's path."""
    folder = Path(folder) if folder is not None else updates_dir()
    folder.mkdir(parents=True, exist_ok=True)
    asset = update.asset
    target = folder / asset.name
    partial = folder / (asset.name + ".part")
    digest = hashlib.sha256()
    done = 0
    try:
        with open(partial, "wb") as handle:
            for chunk in stream(update.url, 60.0):
                if cancelled():
                    raise UpdateCancelled("The download was cancelled.")
                if not chunk:
                    continue
                handle.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                if done > asset.size:
                    break
                progress(done, asset.size)
    except UpdateError:
        partial.unlink(missing_ok=True)
        raise
    except Exception as exc:
        partial.unlink(missing_ok=True)
        raise UpdateError(f"Could not download the update: {exc}") from exc
    if done != asset.size or digest.hexdigest() != asset.sha256:
        partial.unlink(missing_ok=True)
        raise UpdateError("The download did not match its checksum; nothing was installed.")
    os.replace(partial, target)
    return target


# -- installing --------------------------------------------------------------------------------------

@dataclass(frozen=True)
class InstallPlan:
    """The helper to start once the app has quit, and what it will do."""

    command: list[str]
    summary: str


def prepare_install(update: Update, file: Path, info: Optional[BuildInfo] = None,
                    pid: Optional[int] = None) -> InstallPlan:
    """Stage the verified file and write the helper that swaps it in after this process exits."""
    info = info or build_info()
    pid = os.getpid() if pid is None else pid
    folder = updates_dir()
    folder.mkdir(parents=True, exist_ok=True)
    log = paths.logs_dir() / "update.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    if info.kind == "exe":
        return _windows_exe(file, Path(sys.executable).resolve(), pid, folder, log)
    if info.kind == "zip":
        return _windows_folder(file, Path(sys.executable).resolve(), pid, folder, log)
    if info.kind == "dmg":
        bundle = _app_bundle()
        if bundle is None:
            raise UpdateError("Could not find the NovelDB app bundle to replace.")
        return _macos_app(file, bundle, pid, folder, log)
    if info.kind == "appimage":
        return _linux_appimage(file, Path(os.environ["APPIMAGE"]).resolve(), pid, folder, log)
    raise UpdateError("This copy of NovelDB can't update itself.")


def start_helper(plan: InstallPlan) -> None:
    """Start the helper detached, so it outlives this process."""
    if sys.platform == "win32":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        subprocess.Popen(plan.command, creationflags=flags, close_fds=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        subprocess.Popen(plan.command, start_new_session=True, close_fds=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _ps(value: os.PathLike | str) -> str:
    """A PowerShell single-quoted literal."""
    return "'" + str(value).replace("'", "''") + "'"


_PS_HEAD = """$ErrorActionPreference = 'Stop'
$log = {log}
function Log($text) {{ Add-Content -LiteralPath $log -Value ((Get-Date -Format s) + ' ' + $text) }}
function Retry([scriptblock]$step) {{
  for ($i = 1; $i -le 60; $i++) {{
    try {{ & $step; return }} catch {{ if ($i -eq 60) {{ throw }}; Start-Sleep -Milliseconds 500 }}
  }}
}}
try {{ Wait-Process -Id {pid} -Timeout 300 -ErrorAction SilentlyContinue }} catch {{}}
"""

_PS_TAIL = """Start-Process -FilePath {exe}
Remove-Item -LiteralPath $MyInvocation.MyCommand.Path -Force -ErrorAction SilentlyContinue
"""


def _powershell(script: Path) -> list[str]:
    return ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-WindowStyle", "Hidden", "-File", str(script)]


def _windows_exe(file: Path, exe: Path, pid: int, folder: Path, log: Path) -> InstallPlan:
    # The single-file exe stays locked a moment after the app quits (its launcher exits last): retry.
    body = (_PS_HEAD.format(log=_ps(log), pid=pid)
            + f"try {{\n  Retry {{ Copy-Item -LiteralPath {_ps(file)} -Destination {_ps(exe)} -Force }}\n"
              f"  Remove-Item -LiteralPath {_ps(file)} -Force -ErrorAction SilentlyContinue\n"
              f"  Log 'Updated {exe.name}'\n}} catch {{ Log ('Update failed: ' + $_) }}\n"
            + _PS_TAIL.format(exe=_ps(exe)))
    script = folder / "apply-update.ps1"
    script.write_text(body, encoding="utf-8-sig")
    return InstallPlan(_powershell(script), f"Replace {exe.name} and start it again.")


def _windows_folder(file: Path, exe: Path, pid: int, folder: Path, log: Path) -> InstallPlan:
    staging = folder / "staging"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    with zipfile.ZipFile(file) as archive:
        archive.extractall(staging)
    tops = [p for p in staging.iterdir() if p.is_dir()]
    if len(tops) != 1 or not (tops[0] / "_internal").is_dir():
        raise UpdateError("The update archive is not a NovelDB folder build.")
    top = tops[0]
    new_exe = next((p for p in top.glob("*.exe")), None)
    if new_exe is None:
        raise UpdateError("The update archive has no program file.")
    app_dir = exe.parent
    internal, backup = app_dir / "_internal", app_dir / "_internal.old"
    body = (_PS_HEAD.format(log=_ps(log), pid=pid) + f"""$dir = {_ps(app_dir)}
$top = {_ps(top)}
try {{
  if (Test-Path -LiteralPath {_ps(backup)}) {{ Remove-Item -LiteralPath {_ps(backup)} -Recurse -Force }}
  Retry {{ Rename-Item -LiteralPath {_ps(internal)} -NewName '_internal.old' }}
  try {{
    Get-ChildItem -LiteralPath $top | Where-Object {{ $_.Name -ne {_ps(new_exe.name)} }} |
      ForEach-Object {{ Copy-Item -LiteralPath $_.FullName -Destination $dir -Recurse -Force }}
    Retry {{ Copy-Item -LiteralPath {_ps(new_exe)} -Destination {_ps(exe)} -Force }}
  }} catch {{
    if (Test-Path -LiteralPath {_ps(internal)}) {{ Remove-Item -LiteralPath {_ps(internal)} -Recurse -Force }}
    Rename-Item -LiteralPath {_ps(backup)} -NewName '_internal'
    throw
  }}
  Remove-Item -LiteralPath {_ps(backup)} -Recurse -Force -ErrorAction SilentlyContinue
  Log 'Updated the NovelDB folder'
}} catch {{ Log ('Update failed: ' + $_) }}
Remove-Item -LiteralPath {_ps(staging)} -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath {_ps(file)} -Force -ErrorAction SilentlyContinue
""" + _PS_TAIL.format(exe=_ps(exe)))
    script = folder / "apply-update.ps1"
    script.write_text(body, encoding="utf-8-sig")
    return InstallPlan(_powershell(script), f"Replace the NovelDB folder at {app_dir} and start it again.")


_SH_HEAD = """#!/bin/sh
log={log}
while kill -0 {pid} 2>/dev/null; do sleep 0.5; done
"""


def _macos_app(file: Path, bundle: Path, pid: int, folder: Path, log: Path) -> InstallPlan:
    if "/AppTranslocation/" in str(bundle) or not os.access(bundle.parent, os.W_OK):
        raise UpdateError("NovelDB is running from a read-only place (the disk image or a translocated copy). "
                          "Move NovelDB to Applications, open it from there, and update again.")
    mount = Path(tempfile.mkdtemp(prefix="noveldb-update-"))
    staged = bundle.parent / f".{bundle.name}.update"
    try:
        subprocess.run(["hdiutil", "attach", "-nobrowse", "-readonly", "-mountpoint", str(mount), str(file)],
                       check=True, capture_output=True, timeout=120)
        try:
            apps = sorted(mount.glob("*.app"))
            if not apps:
                raise UpdateError("The update disk image has no app in it.")
            shutil.rmtree(staged, ignore_errors=True)
            subprocess.run(["ditto", str(apps[0]), str(staged)], check=True, capture_output=True, timeout=600)
        finally:
            subprocess.run(["hdiutil", "detach", str(mount), "-force"], capture_output=True, timeout=120)
    except subprocess.CalledProcessError as exc:
        raise UpdateError(f"Could not unpack the update: {exc.stderr.decode(errors='replace').strip()}") from exc
    old = bundle.parent / f".{bundle.name}.old"
    q = shlex.quote
    body = _SH_HEAD.format(log=q(str(log)), pid=pid) + f"""{{
  rm -rf {q(str(old))}
  if mv {q(str(bundle))} {q(str(old))} && mv {q(str(staged))} {q(str(bundle))}; then
    rm -rf {q(str(old))}; echo "$(date) Updated {bundle.name}"
  else
    [ -d {q(str(old))} ] && [ ! -d {q(str(bundle))} ] && mv {q(str(old))} {q(str(bundle))}
    echo "$(date) Update failed"
  fi
  rm -f {q(str(file))}
}} >> "$log" 2>&1
open {q(str(bundle))}
rm -f "$0"
"""
    script = folder / "apply-update.sh"
    script.write_text(body, encoding="utf-8")
    script.chmod(0o755)
    return InstallPlan(["/bin/sh", str(script)], f"Replace {bundle.name} and open it again.")


def _linux_appimage(file: Path, appimage: Path, pid: int, folder: Path, log: Path) -> InstallPlan:
    q = shlex.quote
    body = _SH_HEAD.format(log=q(str(log)), pid=pid) + f"""{{
  if mv -f {q(str(file))} {q(str(appimage))} && chmod +x {q(str(appimage))}; then
    echo "$(date) Updated {appimage.name}"
  else
    echo "$(date) Update failed"
  fi
}} >> "$log" 2>&1
nohup {q(str(appimage))} >/dev/null 2>&1 &
rm -f "$0"
"""
    script = folder / "apply-update.sh"
    script.write_text(body, encoding="utf-8")
    script.chmod(0o755)
    return InstallPlan(["/bin/sh", str(script)], f"Replace {appimage.name} and start it again.")


def clean_up() -> None:
    """Remove leftovers of earlier updates (downloads that were never installed)."""
    folder = updates_dir()
    if not folder.is_dir():
        return
    cutoff = time.time() - 24 * 3600
    for path in folder.iterdir():
        try:
            if path.stat().st_mtime < cutoff:
                shutil.rmtree(path) if path.is_dir() else path.unlink()
        except OSError:
            pass
