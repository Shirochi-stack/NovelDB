"""Build the WebToEpub engine bundle (webtoepub/webtoepub-lib.js) from upstream.

WebToEpub (https://github.com/dteviot/WebToEpub, GPL-3.0) is a browser extension. This packs its
site parsers and their helper classes into one script the app injects into a page, between
scripts/webtoepub/prefix.js (stand-ins for the extension APIs, and the network transport) and
scripts/webtoepub/bridge.js (the window.__W2E_* entry points). The extension's own popup code,
library and EPUB packing are left out.

    python scripts/update_webtoepub.py                    # newest commit on the default branch
    python scripts/update_webtoepub.py --ref <sha|branch>
    python scripts/update_webtoepub.py --source <clone>   # a local checkout instead of a download
    python scripts/update_webtoepub.py --validate         # load it in Chromium first; parsers that
                                                          # break loading are left out
    python scripts/update_webtoepub.py --release-dir out  # also write the files the update release hosts
    python scripts/update_webtoepub.py --print-key        # just print "<commit>-<shim>" and exit
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SHIM_DIR = ROOT / "scripts" / "webtoepub"
OUT_DIR = ROOT / "webtoepub"
UPSTREAM = "dteviot/WebToEpub"
DEFAULT_REF = "ExperimentalTabMode"
API = 1  # bump when the window.__W2E_* contract changes; apps ignore bundles with another value

# Popup code, the reading library and EPUB packing: the app does those itself.
SKIP = {"js/main.js", "js/Library.js", "js/EpubPacker.js"}
# Third-party scripts the popup loads from node_modules: (package, file in the package, name the code uses).
THIRD_PARTY = {
    "dompurify": ("dist/purify.min.js", "DOMPurify"),
    "@zip.js/zip.js": ("dist/zip-no-worker.min.js", "zip"),
}


def _get(url: str, accept: str = "") -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "NovelDB-webtoepub-update"})
    if accept:
        request.add_header("Accept", accept)
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token and url.startswith("https://api.github.com/"):
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read()


def shim_key() -> str:
    digest = hashlib.sha256()
    for name in ("prefix.js", "bridge.js"):
        # Line endings depend on the checkout (Windows or the Linux runner), not the shim.
        digest.update((SHIM_DIR / name).read_bytes().replace(b"\r\n", b"\n"))
    return digest.hexdigest()[:12]


def resolve_commit(ref: str) -> tuple[str, str]:
    """(sha, committer date in UTC ISO form) of a branch, tag or commit upstream."""
    data = json.loads(_get(f"https://api.github.com/repos/{UPSTREAM}/commits/{ref}",
                           "application/vnd.github+json"))
    return data["sha"], _utc(data["commit"]["committer"]["date"])


def _utc(value: str) -> str:
    when = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def download_source(sha: str, into: Path) -> Path:
    archive = zipfile.ZipFile(io.BytesIO(_get(f"https://codeload.github.com/{UPSTREAM}/zip/{sha}")))
    for member in archive.namelist():
        # Only plain relative paths: an archive is untrusted input.
        if member.startswith("/") or ".." in Path(member).parts:
            raise SystemExit(f"Refusing archive member {member!r}")
    archive.extractall(into)
    roots = [p for p in into.iterdir() if p.is_dir()]
    if len(roots) != 1:
        raise SystemExit("Unexpected archive layout")
    return roots[0]


def local_source(path: Path) -> tuple[Path, str, str]:
    def git(*args: str) -> str:
        return subprocess.check_output(["git", "-C", str(path), *args], text=True).strip()

    return path, git("rev-parse", "HEAD"), _utc(git("log", "-1", "--format=%cI"))


def script_order(root: Path) -> list[str]:
    """Every script popup.html loads, in its order (parsers can build on earlier ones)."""
    html = (root / "plugin" / "popup.html").read_text(encoding="utf-8")
    return [src for src in re.findall(r"<script\b[^>]*\bsrc=\"([^\"]*)\"", html) if src]


def messages(root: Path) -> dict:
    """English UI strings, for chrome.i18n.getMessage (error texts the parsers raise)."""
    data = json.loads((root / "plugin" / "_locales" / "en" / "messages.json").read_text(encoding="utf-8"))
    out = {}
    for key, entry in data.items():
        item = {"message": entry.get("message", "")}
        placeholders = {name.lower(): value.get("content", "")
                        for name, value in (entry.get("placeholders") or {}).items()}
        if placeholders:
            item["placeholders"] = placeholders
        out[key] = item
    return out


def _version(spec: str) -> str:
    match = re.search(r"\d+(\.\d+)*", spec or "")
    if not match:
        raise SystemExit(f"Cannot read a version from {spec!r}")
    return match.group(0)


def third_party(root: Path) -> list[tuple[str, str]]:
    """(label, code) for each library, wrapped so it never sets a global on the page."""
    deps = json.loads((root / "package.json").read_text(encoding="utf-8")).get("dependencies", {})
    out = []
    for package, (path, name) in THIRD_PARTY.items():
        version = _version(deps.get(package, ""))
        code = _get(f"https://cdn.jsdelivr.net/npm/{package}@{version}/{path}").decode("utf-8")
        # UMD builds take the CommonJS branch when "module" and "exports" exist.
        wrapped = (f"const {name} = (function () {{\nvar module = {{ exports: {{}} }};\n"
                   f"var exports = module.exports;\nvar define = undefined;\n{code}\n"
                   f"return module.exports;\n}})();\n")
        out.append((f"{package}@{version}", wrapped))
    return out


def build(root: Path, sha: str, date: str, ref: str, excluded: set[str],
          libraries: list[tuple[str, str]]) -> tuple[str, dict]:
    manifest_version = json.loads((root / "plugin" / "manifest.json").read_text(encoding="utf-8"))["version"]
    meta = {"engine": "webtoepub", "commit": sha, "date": date, "ref": ref, "version": manifest_version,
            "shim": shim_key()}
    prefix = (SHIM_DIR / "prefix.js").read_text(encoding="utf-8")
    prefix = (prefix.replace("{{COMMIT}}", sha).replace("{{DATE}}", date)
              .replace("{{META}}", json.dumps(meta))
              .replace("{{MESSAGES}}", json.dumps(messages(root), ensure_ascii=False)))
    parts = [prefix]
    for label, code in libraries:
        parts.append(f'\n__w2e_file = "{label}";\n{code}')
    scripts = []
    for src in script_order(root):
        if src in SKIP or src in excluded or not src.startswith("js/"):
            continue
        code = (root / "plugin" / src).read_text(encoding="utf-8-sig")
        parts.append(f'\n__w2e_file = "{src}";\n{code}\n')
        scripts.append(src)
    parts.append((SHIM_DIR / "bridge.js").read_text(encoding="utf-8"))
    text = "".join(parts).replace("\r\n", "\n")
    meta.update({"api": API, "scripts": len(scripts), "excluded": sorted(excluded),
                 "libraries": [label for label, _code in libraries]})
    return text, meta


def validate(text: str) -> dict:
    """Load the bundle in Chromium: {"ready", "error": {file, message} or None, "info"}."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            page.goto("about:blank")
            try:
                page.evaluate(text)
            except Exception as e:  # a syntax error: nothing ran
                return {"ready": False, "error": {"file": "(whole bundle)", "message": str(e)[:500]}, "info": None}
            return page.evaluate(
                "() => ({ready: !!window.__W2E_READY, error: window.__W2E_LOAD_ERROR || null,"
                " info: window.__W2E_READY ? JSON.parse(window.__W2E_info()) : null})")
        finally:
            browser.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ref", default=DEFAULT_REF, help="upstream branch, tag or commit")
    parser.add_argument("--source", type=Path, help="use a local WebToEpub checkout")
    parser.add_argument("--out", type=Path, default=OUT_DIR, help="where webtoepub-lib.js and manifest.json go")
    parser.add_argument("--validate", action="store_true", help="load the bundle in Chromium before writing it")
    parser.add_argument("--release-dir", type=Path, help="also write the update release files here")
    parser.add_argument("--print-key", action="store_true", help='print "<commit>-<shim>" and exit')
    args = parser.parse_args(argv)

    if args.print_key:
        sha = local_source(args.source)[1] if args.source else resolve_commit(args.ref)[0]
        print(f"{sha}-{shim_key()}")
        return 0

    with tempfile.TemporaryDirectory(prefix="w2e-") as temp:
        if args.source:
            root, sha, date = local_source(args.source)
        else:
            sha, date = resolve_commit(args.ref)
            root = download_source(sha, Path(temp))
        libraries = third_party(root)
        excluded: set[str] = set()
        while True:
            text, meta = build(root, sha, date, args.ref, excluded, libraries)
            if not args.validate:
                break
            result = validate(text)
            if result["ready"]:
                meta["sites"] = result["info"]["sites"]
                break
            error = result["error"] or {"file": "?", "message": "did not finish loading"}
            if not str(error["file"]).startswith("js/parsers/") or len(excluded) >= 20:
                raise SystemExit(f"WebToEpub bundle failed to load in {error['file']}: {error['message']}")
            print(f"Leaving out {error['file']}: {error['message']}", file=sys.stderr)
            excluded.add(error["file"])
        license_text = (root / "LICENSE.md").read_text(encoding="utf-8")

    data = text.encode("utf-8")
    meta.update({"file": "webtoepub-lib.js", "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                 "built": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")})
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "webtoepub-lib.js").write_bytes(data)
    (args.out / "manifest.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8", newline="\n")
    (args.out / "LICENSE.md").write_text(license_text, encoding="utf-8", newline="\n")
    if args.release_dir:
        args.release_dir.mkdir(parents=True, exist_ok=True)
        name = f"webtoepub-lib-{sha[:12]}-{meta['shim']}.js"
        shutil.copyfile(args.out / "webtoepub-lib.js", args.release_dir / name)
        (args.release_dir / "webtoepub-manifest.json").write_text(
            json.dumps(dict(meta, file=name), indent=2) + "\n", encoding="utf-8", newline="\n")
    sites = meta.get("sites", "?")
    print(f"WebToEpub {sha[:12]} ({date}): {meta['scripts']} scripts, {sites} sites, "
          f"{len(data) / 1e6:.1f} MB -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
