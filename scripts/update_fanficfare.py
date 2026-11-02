"""Build the FanFicFare engine bundle (fanficfare/fanficfare.zip) from its PyPI release.

FanFicFare (https://github.com/JimmXinu/FanFicFare, Apache-2.0) is pure Python. This packs its
`fanficfare` package from the release's wheel, unchanged, so the app can use a newer release than the
one it was built with: the app unpacks the zip and imports FanFicFare from it, falling back to the
copy it was built with when the zip is missing or fails to load. The build imports the packed
package in a separate Python process and lists every adapter's sites in the manifest.

    python scripts/update_fanficfare.py                    # the newest release
    python scripts/update_fanficfare.py --ref 4.52.0       # a given release
    python scripts/update_fanficfare.py --release-dir out  # also write the files the update release hosts
    python scripts/update_fanficfare.py --print-key        # just print "<version>-<shim>" and exit
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "fanficfare"
PROJECT = "FanFicFare"
API = 1  # bump when the way the app loads the bundle changes

# Imports the packed package (cloudscraper, which FanFicFare imports for sites behind bot checks, is
# replaced by an inert module as in the app) and prints its adapters' sites.
CHECK = r"""
import json, sys, types
sys.path.insert(0, sys.argv[1])
stub = types.ModuleType("cloudscraper")
exceptions = types.ModuleType("cloudscraper.exceptions")
exceptions.CloudflareException = type("CloudflareException", (Exception,), {})
def refuse(*_args, **_kwargs):
    raise RuntimeError("not available")
stub.exceptions = exceptions
stub.CloudScraper = stub.CipherSuiteAdapter = stub.create_scraper = refuse
sys.modules["cloudscraper"] = stub
sys.modules["cloudscraper.exceptions"] = exceptions
import fanficfare
from fanficfare import adapters
sites = set()
classes = getattr(adapters, "__class_list")
for cls in classes:
    try:
        domains = cls.getAcceptDomains() or [cls.getSiteDomain()]
    except Exception:
        continue
    sites.update(str(d).lower().removeprefix("www.") for d in domains if d)
print(json.dumps({"version": getattr(fanficfare, "__version__", ""), "adapters": len(classes), "sites": sorted(sites)}))
"""


def _get(url: str) -> bytes:
    try:
        import requests
    except ImportError:  # the workflow's first step runs before anything is installed
        import urllib.request

        request = urllib.request.Request(url, headers={"User-Agent": "NovelDB-engine-update"})
        with urllib.request.urlopen(request, timeout=300) as response:
            return response.read()
    for attempt in range(4):
        try:
            response = requests.get(url, headers={"User-Agent": "NovelDB-fanficfare-update"}, timeout=180)
            response.raise_for_status()
            return response.content
        except requests.ConnectionError:
            if attempt == 3:
                raise
    raise AssertionError("unreachable")


def shim_key() -> str:
    """Changes when this build (or the app's way of loading it) changes."""
    digest = hashlib.sha256(Path(__file__).read_bytes().replace(b"\r\n", b"\n"))
    digest.update(str(API).encode())
    return digest.hexdigest()[:12]


def resolve(ref: str) -> tuple[str, str, str]:
    """(version, upload date, wheel URL) of a release ("latest" for the newest)."""
    path = f"{PROJECT}/json" if ref in ("", "latest") else f"{PROJECT}/{ref}/json"
    data = json.loads(_get(f"https://pypi.org/pypi/{path}"))
    version = data["info"]["version"]
    wheels = [f for f in data["urls"] if f["packagetype"] == "bdist_wheel" and f["filename"].endswith("-none-any.whl")]
    if not wheels:
        raise SystemExit(f"FanFicFare {version} has no pure-Python wheel")
    wheel = wheels[0]
    when = datetime.fromisoformat(wheel["upload_time_iso_8601"].replace("Z", "+00:00"))
    return version, when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), wheel["url"]


def pack(wheel: bytes, out: Path) -> str:
    """The wheel's `fanficfare` package into a reproducible zip; returns the license text."""
    license_text = ""
    with zipfile.ZipFile(io.BytesIO(wheel)) as source, \
            zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name in sorted(source.namelist()):
            # Only the package, on relative paths: a downloaded archive is untrusted input.
            if name.startswith("/") or ".." in Path(name).parts or name.endswith("/"):
                continue
            if name.split("/")[0].endswith(".dist-info") and Path(name).name.upper().startswith("LICENSE"):
                license_text = source.read(name).decode("utf-8", "replace")
            if not name.startswith("fanficfare/") or "__pycache__" in name:
                continue
            info = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, source.read(name))
    return license_text


def check(bundle: Path) -> dict:
    with tempfile.TemporaryDirectory(prefix="fff-check-") as temp, tempfile.TemporaryDirectory(prefix="fff-cwd-") as cwd:
        with zipfile.ZipFile(bundle) as archive:
            archive.extractall(temp)
        # -P and an empty working folder: nothing but the bundle and the installed packages is importable.
        result = subprocess.run([sys.executable, "-P", "-c", CHECK, temp], capture_output=True, text=True,
                                encoding="utf-8", timeout=600, cwd=cwd)
    if result.returncode != 0:
        raise SystemExit(f"The packed FanFicFare does not load:\n{result.stderr[-3000:]}")
    found = json.loads(result.stdout.strip().splitlines()[-1])
    if found["adapters"] < 50:
        raise SystemExit(f"Only {found['adapters']} adapters loaded")
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ref", default="latest", help="a FanFicFare release, or latest")
    parser.add_argument("--out", type=Path, default=OUT_DIR, help="where fanficfare.zip and manifest.json go")
    parser.add_argument("--release-dir", type=Path, help="also write the update release files here")
    parser.add_argument("--print-key", action="store_true", help='print "<version>-<shim>" and exit')
    parser.add_argument("--validate", action="store_true", help="accepted for the workflow; the build always checks")
    args = parser.parse_args(argv)

    version, date, url = resolve(args.ref)
    if args.print_key:
        print(f"{version}-{shim_key()}")
        return 0
    args.out.mkdir(parents=True, exist_ok=True)
    bundle = args.out / "fanficfare.zip"
    license_text = pack(_get(url), bundle)
    found = check(bundle)
    data = bundle.read_bytes()
    meta = {"engine": "fanficfare", "commit": version, "version": version, "date": date, "ref": args.ref,
            "shim": shim_key(), "api": API, "adapters": found["adapters"], "sites": found["sites"],
            "file": "fanficfare.zip", "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
            "built": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    (args.out / "manifest.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8", newline="\n")
    if license_text:
        (args.out / "LICENSE").write_text(license_text, encoding="utf-8", newline="\n")
    if args.release_dir:
        args.release_dir.mkdir(parents=True, exist_ok=True)
        name = f"fanficfare-{version}-{meta['shim']}.zip"
        shutil.copyfile(bundle, args.release_dir / name)
        (args.release_dir / "fanficfare-manifest.json").write_text(
            json.dumps(dict(meta, file=name), indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"FanFicFare {version} ({date}): {found['adapters']} adapters, {len(found['sites'])} sites, "
          f"{len(data) / 1e6:.1f} MB -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
