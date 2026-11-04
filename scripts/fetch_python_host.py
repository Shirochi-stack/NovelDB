"""Builds the mobile apps' Python engines folder (scripts/python_host) at app build time.

lightnovel-crawler, FanFicFare and gallery-dl run on the phones in Pyodide (Python for web pages),
with the desktop's own engine modules. The folder this writes is what the apps serve to their hidden
Python page:

    python-host.html, python-host.js      the page (the engines run in python-worker.js, python-core.js)
    pyodide/                              Pyodide (pinned below, checked against its hashes) and the
                                          packages the engines need (checked against Pyodide's lock file)
    py/                                   noveldb_mobile.py and the desktop's engine modules
    bundles/<engine>/                     the bundles the app ships (manifest.json and the zip)

Pyodide is fetched here instead of kept in the repository (about 20 MB). The CI workflows run this
before building; a local build runs it once:

    python scripts/fetch_python_host.py            # both apps
    python scripts/fetch_python_host.py --check    # fail when the page or the modules are out of date
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOST = ROOT / "scripts" / "python_host"
# Next to the engines' other bundles: the apps' engine stores read the shipped Python bundles from here.
TARGETS = [
    ROOT / "android" / "sources" / "src" / "main" / "resources" / "com" / "shirochi" / "npia" / "sources" / "python",
    ROOT / "ios" / "NpiaKit" / "Sources" / "NpiaSources" / "Reader" / "Resources" / "python",
]
VERSION = "0.29.5"
CDN = f"https://cdn.jsdelivr.net/pyodide/v{VERSION}/full/"
CORE = {
    "pyodide.js": "7f832a350240263d9946a9c3c877f7bcdab6c37d6dc65f72cd3be5905dca62dd",
    "pyodide.asm.js": "356c42f69e1695397e9d8670bd3c2e678248cde76e18bdae1731e848537b47d7",
    "pyodide.asm.wasm": "54309a5a2cfd757b1f0fdbc9093c92503f7b341500110329d932487183912718",
    "python_stdlib.zip": "831fd1e535084b972f87c6a35275de3d236efd5166f5e77ce89497710635e73c",
    "pyodide-lock.json": "14d2c2dba101277999e17135e653d8f15389ad1437f53eae213bf0c3cdff723d",
}
# The packages python-host.js loads (their dependencies come along from the lock file).
PACKAGES = ["beautifulsoup4", "soupsieve", "typing-extensions", "requests", "urllib3", "idna", "charset-normalizer",
            "certifi", "html5lib", "six", "webencodings", "lxml", "regex", "pycryptodome", "brotli",
            "ssl", "hashlib", "sqlite3"]
# Pure-Python packages Pyodide does not have, from PyPI (FanFicFare imports them).
PYPI = {
    "requests_file-3.0.1-py2.py3-none-any.whl": (
        "https://files.pythonhosted.org/packages/e1/d5/de8f089119205a09da657ed4784c584ede8381a0ce6821212a6d4ca47054/"
        "requests_file-3.0.1-py2.py3-none-any.whl", "d0f5eb94353986d998f80ac63c7f146a307728be051d4d1cd390dbdb59c10fa2"),
    "chardet-5.2.0-py3-none-any.whl": (
        "https://files.pythonhosted.org/packages/38/6f/f5fbc992a329ee4e0f288c1fe0e2ad9485ed064cac731ed2fe47dcc38cbf/"
        "chardet-5.2.0-py3-none-any.whl", "e1cf59446890a00105fe7b7912492ea04b6e6f06d4b742b2c788469e34c82970"),
    "html2text-2025.4.15-py3-none-any.whl": (
        "https://files.pythonhosted.org/packages/1d/84/1a0f9555fd5f2b1c924ff932d99b40a0f8a6b12f6dd625e2a47f415b00ea/"
        "html2text-2025.4.15-py3-none-any.whl", "00569167ffdab3d7767a4cdf589b7f57e777a5ed28d12907d8c58769ec734acc"),
}
MODULES = {
    "noveldb_mobile.py": HOST / "noveldb_mobile.py",
    "lncrawl_engine.py": ROOT / "noveldb" / "core" / "engines" / "lncrawl_engine.py",
    "fanficfare_engine.py": ROOT / "noveldb" / "core" / "engines" / "fanficfare_engine.py",
    "gallerydl_engine.py": ROOT / "noveldb" / "core" / "engines" / "gallerydl_engine.py",
    "generic.py": ROOT / "noveldb" / "core" / "engines" / "generic.py",
}
BUNDLES = {"lncrawl": ROOT / "lncrawl", "fanficfare": ROOT / "fanficfare", "gallerydl": ROOT / "gallerydl"}
CACHE = ROOT / "build" / "pyodide-cache"


def download(name: str, sha256: str, url: str = "") -> bytes:
    """One Pyodide file (or a PyPI wheel at `url`), from the cache when it is there, checked against its hash."""
    cached = CACHE / VERSION / name
    if cached.is_file() and hashlib.sha256(cached.read_bytes()).hexdigest() == sha256:
        return cached.read_bytes()
    with urllib.request.urlopen(url or CDN + name, timeout=120) as response:
        data = response.read()
    if hashlib.sha256(data).hexdigest() != sha256:
        raise SystemExit(f"{name}: the download does not match its hash")
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_bytes(data)
    return data


def pyodide_files() -> dict:
    files = {name: download(name, digest) for name, digest in CORE.items()}
    lock = json.loads(files["pyodide-lock.json"])["packages"]
    wanted, seen = list(PACKAGES), set()
    while wanted:
        name = wanted.pop()
        if name in seen:
            continue
        seen.add(name)
        package = lock[name]
        files[package["file_name"]] = download(package["file_name"], package["sha256"])
        wanted.extend(package.get("depends") or [])
    for name, (url, digest) in PYPI.items():
        files[name] = download(name, digest, url)
    return files


def page_files() -> dict:
    """The page, the modules and the bundles: everything but Pyodide."""
    out = {name: (HOST / name).read_bytes()
           for name in ("python-host.html", "python-host.js", "python-core.js", "python-worker.js")}
    for name, source in MODULES.items():
        out[f"py/{name}"] = source.read_bytes()
    for engine, folder in BUNDLES.items():
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        out[f"bundles/{engine}/manifest.json"] = (folder / "manifest.json").read_bytes()
        out[f"bundles/{engine}/{manifest['file']}"] = (folder / manifest["file"]).read_bytes()
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="only report page and module copies that are out of date")
    parser.add_argument("--out", type=Path, action="append", help="write here instead of the apps' folders")
    args = parser.parse_args()
    targets = args.out or TARGETS
    files = page_files()
    if args.check:
        stale = [str(t / name) for t in targets for name, data in files.items()
                 if not (t / name).is_file() or (t / name).read_bytes() != data]
        if stale:
            print("Out of date (run python scripts/fetch_python_host.py):\n  " + "\n  ".join(stale))
            return 1
        print("The Python engines folders are current.")
        return 0
    files.update({f"pyodide/{name}": data for name, data in pyodide_files().items()})
    for target in targets:
        if target.exists():
            shutil.rmtree(target)
        for name, data in files.items():
            path = target / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
    size = sum(len(d) for d in files.values()) / 1048576
    print(f"Wrote {len(files)} files ({size:.1f} MB) to {len(targets)} folder(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
