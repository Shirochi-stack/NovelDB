"""Build the HaruNeko engine bundle (haruneko/haruneko-lib.js) from upstream.

HaruNeko (https://github.com/manga-download/haruneko, Unlicense) is a manga and webtoon downloader
whose 800+ website plugins are TypeScript. This bundles them with esbuild (scripts/haruneko/build.mjs)
together with NovelDB's platform layer (scripts/haruneko/src): requests and browser tabs go through
the app, and the window.__HN_* entry points speak the other engines' result shapes. HaruNeko's DRM
decryption scripts and image descrambler are left out (those websites stop with a clear error).
Needs Node.js and npm.

    python scripts/update_haruneko.py                    # newest commit on the default branch
    python scripts/update_haruneko.py --ref <sha|branch>
    python scripts/update_haruneko.py --release-dir out  # also write the files the update release hosts
    python scripts/update_haruneko.py --print-key        # just print "<commit>-<shim>" and exit
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
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import secret_guard  # noqa: E402  (keeps credentials out of the bundle)

ROOT = Path(__file__).resolve().parent.parent
SHIM_DIR = ROOT / "scripts" / "haruneko"
OUT_DIR = ROOT / "haruneko"
UPSTREAM = "manga-download/haruneko"
DEFAULT_REF = "master"
API = 1  # bump when the window.__HN_* contract changes; apps ignore bundles with another value


def _get(url: str, accept: str = "") -> bytes:
    try:
        import requests
    except ImportError:  # the workflow's first step runs before anything is installed
        import urllib.request

        request = urllib.request.Request(url, headers={"User-Agent": "NovelDB-engine-update",
                                                       **({"Accept": accept} if accept else {})})
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        if token and url.startswith("https://api.github.com/"):
            request.add_header("Authorization", f"Bearer {token}")
        with urllib.request.urlopen(request, timeout=300) as response:
            return response.read()

    headers = {"User-Agent": "NovelDB-haruneko-update"}
    if accept:
        headers["Accept"] = accept
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token and url.startswith("https://api.github.com/"):
        headers["Authorization"] = f"Bearer {token}"
    for attempt in range(4):
        try:
            response = requests.get(url, headers=headers, timeout=300)
            response.raise_for_status()
            return response.content
        except requests.ConnectionError:
            if attempt == 3:
                raise
    raise AssertionError("unreachable")


def shim_key() -> str:
    digest = hashlib.sha256()
    for path in sorted((SHIM_DIR / "src").glob("*.ts")) + [SHIM_DIR / "build.mjs", SHIM_DIR / "package-lock.json"]:
        digest.update(path.name.encode())
        digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
    digest.update(secret_guard.shim_part())
    return digest.hexdigest()[:12]


def resolve_commit(ref: str) -> tuple[str, str]:
    data = json.loads(_get(f"https://api.github.com/repos/{UPSTREAM}/commits/{ref}", "application/vnd.github+json"))
    when = datetime.fromisoformat(data["commit"]["committer"]["date"].replace("Z", "+00:00"))
    return data["sha"], when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def download_source(sha: str, into: Path) -> Path:
    archive = tarfile.open(fileobj=io.BytesIO(_get(f"https://codeload.github.com/{UPSTREAM}/tar.gz/{sha}")))
    def wanted(member) -> bool:
        # Only plain files and folders on relative paths: an archive is untrusted input.
        parts = Path(member.name).parts
        return not member.name.startswith("/") and ".." not in parts and (member.isfile() or member.isdir())

    members = [m for m in archive.getmembers() if wanted(m)]
    archive.extractall(into, members=members, filter="data")
    roots = [p for p in into.iterdir() if p.is_dir()]
    if len(roots) != 1:
        raise SystemExit("Unexpected archive layout")
    return roots[0]


def bundle(checkout: Path) -> str:
    npm = shutil.which("npm") or shutil.which("npm.cmd")
    node = shutil.which("node") or shutil.which("node.exe")
    if not npm or not node:
        raise SystemExit("Node.js and npm are needed to build the HaruNeko bundle")
    subprocess.run([npm, "ci", "--no-audit", "--no-fund", "--ignore-scripts"], cwd=SHIM_DIR, check=True)
    with tempfile.TemporaryDirectory(prefix="hn-out-") as temp:
        out = Path(temp) / "haruneko.js"
        subprocess.run([node, "build.mjs", str(checkout), str(out)], cwd=SHIM_DIR, check=True)
        return out.read_text(encoding="utf-8")


def wrap(code: str, meta: dict) -> str:
    header = (
        "/*\n"
        " * HaruNeko website plugins, packaged for NovelDB.\n"
        " * Generated by scripts/update_haruneko.py. Do not edit; rebuild instead.\n"
        " *\n"
        f" * HaruNeko: https://github.com/manga-download/haruneko (Unlicense), commit {meta['commit']}, {meta['date']}.\n"
        " * protobufjs (BSD-3-Clause), devalue (MIT), exifr (MIT).\n"
        " * DRM decryption and image descrambling are not included.\n"
        " */\n"
    )
    return (f"{header}if (!window.__HN_READY) {{\nwindow.__HN_META = {json.dumps(meta)};\ntry {{\n{code}\n}} catch (error) {{\n"
            "    window.__HN_LOAD_ERROR = { file: 'bundle', message: String((error && error.message) || error) };\n"
            "    console.error('[HN] failed to load: ' + error);\n}\n}\n")


def validate(text: str) -> dict:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            page.goto("about:blank")
            try:
                page.evaluate(text)
            except Exception as e:
                return {"ready": False, "error": str(e)[:500]}
            if not page.evaluate("window.__HN_READY === true"):
                return {"ready": False, "error": page.evaluate("JSON.stringify(window.__HN_LOAD_ERROR || null)")}
            return {"ready": True, "sites": json.loads(page.evaluate("window.__HN_sites()"))}
        finally:
            browser.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ref", default=DEFAULT_REF, help="upstream branch, tag or commit")
    parser.add_argument("--source", type=Path, help="use a local HaruNeko checkout (its commit is still looked up)")
    parser.add_argument("--out", type=Path, default=OUT_DIR, help="where haruneko-lib.js and manifest.json go")
    parser.add_argument("--release-dir", type=Path, help="also write the update release files here")
    parser.add_argument("--print-key", action="store_true", help='print "<commit>-<shim>" and exit')
    parser.add_argument("--validate", action="store_true", help="accepted for the workflow; the build always checks")
    args = parser.parse_args(argv)

    sha, date = resolve_commit(args.ref)
    if args.print_key:
        print(f"{sha}-{shim_key()}")
        return 0
    meta = {"engine": "haruneko", "commit": sha, "date": date, "ref": args.ref, "shim": shim_key(), "api": API}
    with tempfile.TemporaryDirectory(prefix="haruneko-") as temp:
        checkout = args.source or download_source(sha, Path(temp))
        code = bundle(checkout)
        license_text = (checkout / "UNLICENSE").read_text(encoding="utf-8") if (checkout / "UNLICENSE").is_file() else ""
    text = wrap(code, meta)
    result = validate(text)
    if not result["ready"]:
        raise SystemExit(f"The HaruNeko bundle failed to load: {result['error']}")
    sites = result["sites"]
    text, removed = secret_guard.redact(text, "haruneko-lib.js")
    secret_guard.report(removed)
    data = text.encode("utf-8")
    meta.update({"websites": len(sites), "sites": sorted({site["url"] for site in sites}), "file": "haruneko-lib.js",
                 "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                 "built": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")})
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "haruneko-lib.js").write_bytes(data)
    (args.out / "manifest.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8", newline="\n")
    (args.out / "LICENSE").write_text(license_text, encoding="utf-8", newline="\n")
    if args.release_dir:
        args.release_dir.mkdir(parents=True, exist_ok=True)
        name = f"haruneko-lib-{sha[:12]}-{meta['shim']}.js"
        shutil.copyfile(args.out / "haruneko-lib.js", args.release_dir / name)
        (args.release_dir / "haruneko-manifest.json").write_text(
            json.dumps(dict(meta, file=name), indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"HaruNeko {sha[:12]} ({date}): {len(sites)} websites, {len(data) / 1e6:.1f} MB -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
