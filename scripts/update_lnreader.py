"""Build the LNReader engine bundle (lnreader/lnreader-lib.js) from upstream.

LNReader (https://github.com/LNReader/lnreader-plugins, MIT) publishes its source plugins, compiled to
CommonJS, on the `plugins/v3.0.0` branch with an index (`.dist/plugins.min.json`). This packs every
plugin into one script the app injects into a page, between scripts/lnreader/prefix.js (the plugins'
libraries and the network transport) and scripts/lnreader/bridge.js (the window.__LNR_* entry points).
The libraries the plugins require (cheerio, htmlparser2, dayjs, @noble/ciphers, protobufjs) are bundled
with esbuild from scripts/lnreader/package.json, so this needs Node.js and npm.

    python scripts/update_lnreader.py                    # newest commit of the plugins branch
    python scripts/update_lnreader.py --ref <sha|branch>
    python scripts/update_lnreader.py --validate         # load it in Chromium first
    python scripts/update_lnreader.py --release-dir out  # also write the files the update release hosts
    python scripts/update_lnreader.py --print-key        # just print "<commit>-<shim>" and exit
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import secret_guard  # noqa: E402  (keeps credentials out of the bundle)
from source_names import dumps  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SHIM_DIR = ROOT / "scripts" / "lnreader"
OUT_DIR = ROOT / "lnreader"
UPSTREAM = "LNReader/lnreader-plugins"
DEFAULT_REF = "plugins/v3.0.0"
INDEX = ".dist/plugins.min.json"
API = 1  # bump when the window.__LNR_* contract changes; apps ignore bundles with another value


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

    headers = {"User-Agent": "NovelDB-lnreader-update"}
    if accept:
        headers["Accept"] = accept
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token and url.startswith("https://api.github.com/"):
        headers["Authorization"] = f"Bearer {token}"
    for attempt in range(4):  # raw.githubusercontent.com drops a connection now and then
        try:
            response = requests.get(url, headers=headers, timeout=120)
            response.raise_for_status()
            return response.content
        except requests.ConnectionError:
            if attempt == 3:
                raise
    raise AssertionError("unreachable")


def shim_key() -> str:
    digest = hashlib.sha256()
    for name in ("prefix.js", "bridge.js", "vendor.js", "package-lock.json"):
        # Line endings depend on the checkout (Windows or the Linux runner), not the shim.
        digest.update((SHIM_DIR / name).read_bytes().replace(b"\r\n", b"\n"))
    digest.update(secret_guard.shim_part())
    return digest.hexdigest()[:12]


def resolve_commit(ref: str) -> tuple[str, str]:
    """(sha, committer date in UTC ISO form) of a branch, tag or commit upstream."""
    data = json.loads(_get(f"https://api.github.com/repos/{UPSTREAM}/commits/{urllib.parse.quote(ref, safe='')}",
                           "application/vnd.github+json"))
    return data["sha"], _utc(data["commit"]["committer"]["date"])


def _utc(value: str) -> str:
    when = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _raw(sha: str, path: str) -> str:
    return f"https://raw.githubusercontent.com/{UPSTREAM}/{sha}/{urllib.parse.quote(path)}"


def plugin_path(entry: dict) -> str:
    """The plugin's file in the repository, from the index's raw address (`.../<branch>/<path>`)."""
    url = urllib.parse.unquote(entry["url"])
    marker = "/.js/"
    at = url.find(marker)
    if at < 0:
        raise ValueError(f"unexpected plugin address {entry['url']!r}")
    return url[at + 1:]


def fetch_plugins(sha: str) -> tuple[list[dict], dict[str, str]]:
    index = json.loads(_get(_raw(sha, INDEX)))
    if not isinstance(index, list):
        raise SystemExit("The LNReader index is not a list")

    def one(entry):
        return entry["id"], _get(_raw(sha, plugin_path(entry))).decode("utf-8")

    with ThreadPoolExecutor(8) as pool:
        code = dict(pool.map(one, index))
    return index, code


def vendor() -> str:
    """The plugins' libraries as one browser script (esbuild IIFE)."""
    npm = shutil.which("npm") or shutil.which("npm.cmd")
    npx = shutil.which("npx") or shutil.which("npx.cmd")
    if not npm or not npx:
        raise SystemExit("Node.js and npm are needed to bundle the LNReader libraries")
    subprocess.run([npm, "ci", "--no-audit", "--no-fund", "--ignore-scripts"], cwd=SHIM_DIR, check=True)
    with tempfile.TemporaryDirectory(prefix="lnr-vendor-") as temp:
        out = Path(temp) / "vendor.js"
        subprocess.run([npx, "esbuild", "vendor.js", "--bundle", "--platform=browser", "--format=iife",
                        "--global-name=__lnr_vendor", "--minify", "--target=es2020", "--legal-comments=none",
                        f"--outfile={out}"],
                       cwd=SHIM_DIR, check=True)
        return out.read_text(encoding="utf-8")


def build(sha: str, date: str, ref: str, index: list[dict], code: dict[str, str], libraries: str,
          excluded: set[str]) -> tuple[str, dict]:
    meta = {"engine": "lnreader", "commit": sha, "date": date, "ref": ref, "shim": shim_key()}
    prefix = (SHIM_DIR / "prefix.js").read_text(encoding="utf-8")
    prefix = prefix.replace("{{COMMIT}}", sha).replace("{{DATE}}", date).replace("{{META}}", dumps(meta))
    parts = [prefix, '\n__lnr_file = "libraries";\n', libraries, "\n"]
    kept = []
    for entry in index:
        if entry["id"] in excluded or entry["id"] not in code:
            continue
        info = {key: entry.get(key, "") for key in ("id", "name", "site", "lang", "version")}
        parts.append(f"\n__lnr_file = {json.dumps('plugin ' + entry['id'])};\n"
                     f"__lnr_define({dumps(info, ensure_ascii=False)}, "
                     f"function (module, exports, require, fetch) {{\n{code[entry['id']]}\n}});\n")
        kept.append(entry)
    parts.append((SHIM_DIR / "bridge.js").read_text(encoding="utf-8"))
    text = "".join(parts).replace("\r\n", "\n")
    meta.update({"api": API, "plugins": len(kept), "excluded": sorted(excluded),
                 "sites": sorted({entry["site"] for entry in kept})})
    return text, meta


def validate(text: str, ids: list[str]) -> dict:
    """Load the bundle in Chromium and build every plugin: {"ready", "error", "failed": {id: message}}."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            page.goto("about:blank")
            try:
                page.evaluate(text)
            except Exception as e:  # a syntax error: nothing ran
                return {"ready": False, "error": {"file": "(whole bundle)", "message": str(e)[:500]}, "failed": {}}
            state = page.evaluate("() => ({ready: !!window.__LNR_READY, error: window.__LNR_LOAD_ERROR || null})")
            if not state["ready"]:
                return dict(state, failed={})
            failed = page.evaluate("""(ids) => {
                const failed = {};
                for (const id of ids) {
                    try { window.__LNR_instance(id); } catch (e) { failed[id] = String(e && e.message || e); }
                }
                return failed;
            }""", ids)
            return dict(state, failed=failed)
        finally:
            browser.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ref", default=DEFAULT_REF, help="upstream branch, tag or commit of the built plugins")
    parser.add_argument("--out", type=Path, default=OUT_DIR, help="where lnreader-lib.js and manifest.json go")
    parser.add_argument("--validate", action="store_true", help="load the bundle in Chromium before writing it")
    parser.add_argument("--release-dir", type=Path, help="also write the update release files here")
    parser.add_argument("--print-key", action="store_true", help='print "<commit>-<shim>" and exit')
    args = parser.parse_args(argv)

    sha, date = resolve_commit(args.ref)
    if args.print_key:
        print(f"{sha}-{shim_key()}")
        return 0
    index, code = fetch_plugins(sha)
    libraries = vendor()
    excluded: set[str] = set()
    text, meta = build(sha, date, args.ref, index, code, libraries, excluded)
    if args.validate:
        result = validate(text.replace("window.__LNR_API = LNR_API;",
                                       "window.__LNR_API = LNR_API; window.__LNR_instance = __lnr_instance;"),
                          [entry["id"] for entry in index])
        if not result["ready"]:
            error = result["error"] or {"file": "?", "message": "did not finish loading"}
            raise SystemExit(f"LNReader bundle failed to load in {error['file']}: {error['message']}")
        for plugin_id, message in sorted(result["failed"].items()):
            print(f"Leaving out {plugin_id}: {message}", file=sys.stderr)
        excluded = set(result["failed"])
        if excluded:
            text, meta = build(sha, date, args.ref, index, code, libraries, excluded)
    # The plugins branch holds only build output; the license is on the source branch.
    license_text = _get(_raw("master", "LICENSE")).decode("utf-8")

    text, removed = secret_guard.redact(text, "lnreader-lib.js")
    secret_guard.report(removed)
    data = text.encode("utf-8")
    meta.update({"file": "lnreader-lib.js", "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                 "built": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")})
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "lnreader-lib.js").write_bytes(data)
    (args.out / "manifest.json").write_text(dumps(meta, indent=2) + "\n", encoding="utf-8", newline="\n")
    (args.out / "LICENSE.md").write_text(license_text, encoding="utf-8", newline="\n")
    if args.release_dir:
        args.release_dir.mkdir(parents=True, exist_ok=True)
        name = f"lnreader-lib-{sha[:12]}-{meta['shim']}.js"
        shutil.copyfile(args.out / "lnreader-lib.js", args.release_dir / name)
        (args.release_dir / "lnreader-manifest.json").write_text(
            dumps(dict(meta, file=name), indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"LNReader {sha[:12]} ({date}): {meta['plugins']} plugins, {len(meta['excluded'])} left out, "
          f"{len(data) / 1e6:.1f} MB -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
