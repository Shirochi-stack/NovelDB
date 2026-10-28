"""Build the novel-downloader rules bundle (rules-lib.js) from the official repository.

novel-downloader (https://github.com/404-novel-project/novel-downloader, AGPL-3.0) is a userscript.
The app runs its site rules in "bridge mode": src/index.ts is patched to expose getRule(),
getHtmlDOM() and cleanDOM() on window instead of starting the userscript's own interface, and the
app's gm_stubs.js and bridge.js wrap them. This is the same build patch_rules.ps1 does on Windows.

    python scripts/update_nd_rules.py                      # newest upstream commit -> rules-lib.js here
    python scripts/update_nd_rules.py --source <checkout>  # build an existing checkout
    python scripts/update_nd_rules.py --validate           # load it in Chromium before writing it
    python scripts/update_nd_rules.py --out build/nd --no-sanitize --release-dir out   # the update release files
    python scripts/update_nd_rules.py --print-key          # just print "<commit>-<patch>" and exit

Requires git, Node.js and npm.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UPSTREAM = "404-novel-project/novel-downloader"
REPO_URL = f"https://github.com/{UPSTREAM}.git"
API = 1  # the window.__ND_* contract bridge.js relies on; apps ignore bundles with another value

BRIDGE_IMPORTS = (
    'import { getRule } from "./router/download";',
    'import { getHtmlDOM } from "./lib/http";',
    'import { cleanDOM } from "./lib/cleanDOM";',
)
BRIDGE_EXPORTS = """  (window as any).__ND_getRule = getRule;
  (window as any).__ND_getHtmlDOM = getHtmlDOM;
  try { (window as any).__ND_cleanDOM = cleanDOM; } catch(e) {}
  (window as any).__ND_READY = true;
  log.info("[Init] Bridge mode - skipping UI init, getRule exposed.");"""
# Since late 2026 index.ts only imports bootstrap/top.ts, whose run() starts the userscript.
RUN_START = "export async function run() {\n"
BRIDGE_RUN = """export async function run() {
  // Bridge mode: skip full UI init, just expose internals for external callers.
  if ((window as any).__ND_BRIDGE_MODE) {
""" + BRIDGE_EXPORTS + """
    return;
  }
"""
STOCK_START = re.compile(
    r'if \(document\.readyState === "loading"\) \{\s*document\.addEventListener\("DOMContentLoaded", main\);'
    r'\s*\} else \{\s*(?://[^\n]*\s*)?main\(\);\s*\}')
BRIDGE_START = """// Bridge mode: skip full UI init, just expose internals for external callers.
// Exports:
//   __ND_getRule()    - returns the rule class/instance matching window.location
//   __ND_getHtmlDOM() - fetches a URL, returns { doc, charset } DOM object
//   __ND_cleanDOM()   - sanitises a DOM element for EPUB-safe XHTML output
if ((window as any).__ND_BRIDGE_MODE) {
""" + BRIDGE_EXPORTS + """
} else if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", main);
} else {
  // noinspection JSIgnoredPromiseFromCall
  main();
}"""
TS_LOADER = 'use: ["ts-loader"],'
TS_LOADER_FAST = 'use: [{ loader: "ts-loader", options: { transpileOnly: true } }],'


def patch_key() -> str:
    """Changes when the build patch or the app's wrappers change."""
    digest = hashlib.sha256((BRIDGE_START + BRIDGE_RUN + "".join(BRIDGE_IMPORTS) + TS_LOADER_FAST).encode())
    return digest.hexdigest()[:12]


def _run(args: list[str], cwd: Path | None = None) -> str:
    print("+", " ".join(args), file=sys.stderr)
    tool = shutil.which(args[0]) or args[0]  # npm is npm.cmd on Windows
    done = subprocess.run([tool, *args[1:]], cwd=cwd, text=True, capture_output=True,
                          encoding="utf-8", errors="replace")
    if done.returncode != 0:
        sys.stderr.write(done.stdout[-4000:] + done.stderr[-4000:])
        raise SystemExit(f"{args[0]} failed with exit code {done.returncode}")
    return done.stdout.strip()


def _utc(value: str) -> str:
    when = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def remote_head(ref: str) -> str:
    out = _run(["git", "ls-remote", REPO_URL, ref])
    if not out:
        raise SystemExit(f"{ref} not found in {REPO_URL}")
    return out.split()[0]


def clone(ref: str, into: Path) -> Path:
    # Full history: the bundle's version number is the commit count.
    _run(["git", "clone", "--filter=blob:none", "--no-checkout", REPO_URL, str(into)])
    _run(["git", "checkout", "--quiet", ref], cwd=into)
    return into


def _add_imports(text: str, prefix: str) -> str:
    for line in BRIDGE_IMPORTS:
        line = line.replace('"./', f'"{prefix}')
        if line not in text:
            text = line + "\n" + text
    return text


def patch(source: Path) -> None:
    index = source / "src" / "index.ts"
    top = source / "src" / "bootstrap" / "top.ts"
    text = index.read_text(encoding="utf-8")
    if "__ND_BRIDGE_MODE" in text or STOCK_START.search(text):
        # The original layout: index.ts starts the userscript itself.
        text = _add_imports(text, "./")
        if "__ND_BRIDGE_MODE" not in text:
            text = STOCK_START.sub(lambda _m: BRIDGE_START, text, count=1)
        index.write_text(text, encoding="utf-8")
    elif top.is_file():
        text = top.read_text(encoding="utf-8").replace("\r\n", "\n")
        if "__ND_BRIDGE_MODE" not in text:
            if text.count(RUN_START) != 1:
                raise SystemExit("src/bootstrap/top.ts changed upstream: the bridge-mode patch no longer applies")
            text = _add_imports(text.replace(RUN_START, BRIDGE_RUN, 1), "../")
        top.write_text(text, encoding="utf-8")
    else:
        raise SystemExit("src/index.ts changed upstream: the bridge-mode patch no longer applies")
    # Older checkouts type-check with ts-loader, which fails on the patch; esbuild-loader never does.
    config = source / "webpack.config.js"
    text = config.read_text(encoding="utf-8")
    if TS_LOADER in text:
        config.write_text(text.replace(TS_LOADER, TS_LOADER_FAST), encoding="utf-8")


def build(source: Path) -> Path:
    if not (source / "node_modules").is_dir():
        # npm, as patch_rules.ps1 uses (upstream pins Yarn 3, which needs corepack).
        _run(["npm", "install", "--no-audit", "--no-fund", "--loglevel=error"], cwd=source)
    _run(["npx", "webpack", "build", "-c", "webpack.config.js"], cwd=source)
    bundle = source / "dist" / "bundle.user.js"
    if not bundle.is_file():
        raise SystemExit("webpack did not write dist/bundle.user.js")
    return bundle


def rule_hosts(text: str) -> list[str]:
    """The hosts getRule()'s switch accepts."""
    end = text.find('throw new Error("Not Found Rule!")')
    start = text.rfind("switch(", 0, end)
    return re.findall(r'case"([^"]+)":', text[start:end]) if end > 0 and start > 0 else []


def validate(text: str, host: str) -> dict:
    """Load gm_stubs.js + the bundle + bridge.js in Chromium and look up the rule for `host`."""
    from playwright.sync_api import sync_playwright

    stubs = (ROOT / "gm_stubs.js").read_text(encoding="utf-8")
    bridge = (ROOT / "bridge.js").read_text(encoding="utf-8")
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            page.route(f"https://{host}/**", lambda route: route.fulfill(
                body="<html><body><h1>Book</h1></body></html>", content_type="text/html"))
            page.goto(f"https://{host}/book/1.html")
            page.evaluate(stubs)
            page.evaluate(text)
            page.evaluate(bridge)
            return page.evaluate("""async () => {
                const out = {ready: window.__ND_READY === true, bridge: window.__ND_BRIDGE_READY === true};
                try { out.rule = !!(await window.__ND_getRule()); } catch (e) { out.error = String(e); }
                return out;
            }""")
        finally:
            browser.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ref", default="master", help="upstream branch, tag or commit")
    parser.add_argument("--source", type=Path, help="build this checkout (patched in place)")
    parser.add_argument("--out", type=Path, default=ROOT, help="where rules-lib.js goes")
    parser.add_argument("--no-sanitize", action="store_true",
                        help="skip scripts/codename_policy.py sanitize-rules (the repository copy needs it)")
    parser.add_argument("--validate", action="store_true", help="load the bundle in Chromium before writing it")
    parser.add_argument("--release-dir", type=Path, help="also write the update release files here")
    parser.add_argument("--print-key", action="store_true", help='print "<commit>-<patch>" and exit')
    args = parser.parse_args(argv)

    if args.print_key:
        sha = _run(["git", "rev-parse", "HEAD"], cwd=args.source) if args.source else remote_head(args.ref)
        print(f"{sha}-{patch_key()}")
        return 0

    with tempfile.TemporaryDirectory(prefix="nd-") as temp:
        source = args.source or clone(args.ref, Path(temp) / "src")
        sha = _run(["git", "rev-parse", "HEAD"], cwd=source)
        date = _utc(_run(["git", "log", "-1", "--format=%cI"], cwd=source))
        patch(source)
        text = build(source).read_text(encoding="utf-8")
    match = re.search(r"@version\s+(\S+)", text)
    version = match.group(1) if match else ""
    hosts = rule_hosts(text)
    if len(hosts) < 100:
        raise SystemExit(f"Only {len(hosts)} rule hosts found; the bundle layout changed")
    if args.validate:
        result = validate(text, next(h for h in hosts if "\\" not in h))
        if not (result.get("ready") and result.get("bridge") and result.get("rule")):
            raise SystemExit(f"novel-downloader bundle failed to load: {result}")

    args.out.mkdir(parents=True, exist_ok=True)
    target = args.out / "rules-lib.js"
    target.write_text(text, encoding="utf-8", newline="\n")
    if not args.no_sanitize:
        subprocess.run([sys.executable, str(ROOT / "scripts" / "codename_policy.py"), "sanitize-rules",
                        str(target)], check=True)
    data = target.read_bytes()
    meta = {"engine": "novel_downloader", "api": API, "commit": sha, "date": date, "ref": args.ref,
            "version": version, "shim": patch_key(), "hosts": len(hosts), "file": "rules-lib.js",
            "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
            "built": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    if args.release_dir:
        args.release_dir.mkdir(parents=True, exist_ok=True)
        name = f"rules-lib-{version or sha[:12]}-{meta['shim']}.js"
        shutil.copyfile(target, args.release_dir / name)
        (args.release_dir / "novel-downloader-manifest.json").write_text(
            json.dumps(dict(meta, file=name), indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"novel-downloader {version} ({sha[:12]}, {date}): {len(hosts)} rule hosts, "
          f"{len(data) / 1e6:.1f} MB -> {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
