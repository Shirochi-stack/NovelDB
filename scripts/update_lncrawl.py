"""Build the lightnovel-crawler engine bundle (lncrawl/lncrawl-sources.zip) from upstream.

lightnovel-crawler (https://github.com/lncrawl/lightnovel-crawler, GPL-3.0) has 400+ Python crawlers in
`sources/`, written against its crawler core (`lncrawl/core`, `lncrawl/templates`). This packs the
sources and that core, unchanged apart from two import lines, with NovelDB's stand-ins from
scripts/lncrawl/runtime: the request layer (`scraper`, which sends every request through the app),
the app context, the data models and a few helpers. Each source is imported in a separate Python
process; ones that fail (a missing third-party module, say) are left out. `index.json` in the zip
lists every crawler's sites and abilities, and the manifest lists the sites for the app.

    python scripts/update_lncrawl.py                    # newest commit on the default branch
    python scripts/update_lncrawl.py --ref <sha|branch>
    python scripts/update_lncrawl.py --release-dir out  # also write the files the update release hosts
    python scripts/update_lncrawl.py --print-key        # just print "<commit>-<shim>" and exit
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
import zipfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import secret_guard  # noqa: E402  (keeps credentials out of the bundle)

ROOT = Path(__file__).resolve().parent.parent
SHIM_DIR = ROOT / "scripts" / "lncrawl"
RUNTIME = SHIM_DIR / "runtime"
OUT_DIR = ROOT / "lncrawl"
UPSTREAM = "lncrawl/lightnovel-crawler"
DEFAULT_REF = "dev"
API = 1  # bump when the runtime contract (index.json, Scraper.fetcher) changes

# The crawler core files used as they are, and the import lines swapped for NovelDB's stand-ins.
CORE = ("__init__.py", "crawler.py", "template.py", "legacy.py", "cleaner.py", "taskman.py", "tiers.py")
PATCHES = {
    "lncrawl/core/crawler.py": [("from pydantic.networks import HttpUrl", "from ..utils.text_tools import HttpUrl")],
    "lncrawl/core/taskman.py": [
        ("from tqdm import tqdm", "from ..utils.progress import tqdm"),
        ("from concurrent.futures import Future, ThreadPoolExecutor, as_completed",
         "from concurrent.futures import Future, as_completed\n\n"
         "from ..utils.inline import InlineExecutor as ThreadPoolExecutor"),
    ],
}


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

    headers = {"User-Agent": "NovelDB-lncrawl-update"}
    if accept:
        headers["Accept"] = accept
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token and url.startswith("https://api.github.com/"):
        headers["Authorization"] = f"Bearer {token}"
    for attempt in range(4):
        try:
            response = requests.get(url, headers=headers, timeout=180)
            response.raise_for_status()
            return response.content
        except requests.ConnectionError:
            if attempt == 3:
                raise
    raise AssertionError("unreachable")


def shim_key() -> str:
    digest = hashlib.sha256()
    for path in sorted(RUNTIME.rglob("*.py")) + [SHIM_DIR / "index_sources.py"]:
        digest.update(path.relative_to(SHIM_DIR).as_posix().encode())
        digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
    digest.update(json.dumps(PATCHES, sort_keys=True).encode())
    digest.update(secret_guard.shim_part())
    return digest.hexdigest()[:12]


def resolve_commit(ref: str) -> tuple[str, str]:
    data = json.loads(_get(f"https://api.github.com/repos/{UPSTREAM}/commits/{ref}", "application/vnd.github+json"))
    when = datetime.fromisoformat(data["commit"]["committer"]["date"].replace("Z", "+00:00"))
    return data["sha"], when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def download_source(sha: str, into: Path) -> Path:
    archive = tarfile.open(fileobj=io.BytesIO(_get(f"https://codeload.github.com/{UPSTREAM}/tar.gz/{sha}")))
    members = []
    for member in archive.getmembers():
        # Only plain files and folders on relative paths: an archive is untrusted input.
        if member.name.startswith("/") or ".." in Path(member.name).parts or not (member.isfile() or member.isdir()):
            continue
        members.append(member)
    archive.extractall(into, members=members, filter="data")
    roots = [p for p in into.iterdir() if p.is_dir()]
    if len(roots) != 1:
        raise SystemExit("Unexpected archive layout")
    return roots[0]


def assemble(upstream: Path, into: Path) -> None:
    shutil.copytree(RUNTIME, into, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__"))
    for name in CORE:
        shutil.copyfile(upstream / "lncrawl" / "core" / name, into / "lncrawl" / "core" / name)
    shutil.copytree(upstream / "lncrawl" / "templates", into / "lncrawl" / "templates", dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copyfile(upstream / "lncrawl" / "utils" / "event_lock.py", into / "lncrawl" / "utils" / "event_lock.py")
    shutil.copytree(upstream / "sources", into / "sources", ignore=shutil.ignore_patterns("__pycache__", "*.md"))
    for relative, swaps in PATCHES.items():
        path = into / relative
        text = path.read_text(encoding="utf-8")
        for old, new in swaps:
            if old not in text:
                raise SystemExit(f"{relative} no longer has {old!r}; update PATCHES")
            text = text.replace(old, new)
        path.write_text(text, encoding="utf-8")
    shutil.copyfile(upstream / "LICENSE", into / "LICENSE")


def index_sources(folder: Path) -> dict:
    """Import every source in a fresh interpreter: {"crawlers": [...], "failed": {file: error}}."""
    result = subprocess.run([sys.executable, str(SHIM_DIR / "index_sources.py"), str(folder)],
                            capture_output=True, text=True, encoding="utf-8", timeout=900)
    if result.returncode != 0:
        raise SystemExit(f"Indexing the sources failed:\n{result.stderr[-3000:]}")
    return json.loads(result.stdout)


def pack(folder: Path, index: dict, out: Path) -> None:
    failed = set(index["failed"])
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(folder.rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            relative = path.relative_to(folder).as_posix()
            if relative in failed:
                continue
            info = zipfile.ZipInfo(relative, date_time=(2020, 1, 1, 0, 0, 0))  # reproducible
            info.compress_type = zipfile.ZIP_DEFLATED
            content = path.read_bytes().replace(b"\r\n", b"\n")
            if path.suffix == ".py":
                text, removed = secret_guard.redact(content.decode("utf-8", "replace"), relative)
                secret_guard.report(removed)
                content = text.encode("utf-8")
            archive.writestr(info, content)
        info = zipfile.ZipInfo("index.json", date_time=(2020, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        archive.writestr(info, json.dumps({"api": API, "crawlers": index["crawlers"]}, indent=1, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ref", default=DEFAULT_REF, help="upstream branch, tag or commit")
    parser.add_argument("--out", type=Path, default=OUT_DIR, help="where lncrawl-sources.zip and manifest.json go")
    parser.add_argument("--release-dir", type=Path, help="also write the update release files here")
    parser.add_argument("--print-key", action="store_true", help='print "<commit>-<shim>" and exit')
    parser.add_argument("--validate", action="store_true", help="accepted for the workflow; the build always checks")
    args = parser.parse_args(argv)

    sha, date = resolve_commit(args.ref)
    if args.print_key:
        print(f"{sha}-{shim_key()}")
        return 0
    with tempfile.TemporaryDirectory(prefix="lncrawl-") as temp:
        upstream = download_source(sha, Path(temp) / "src")
        folder = Path(temp) / "bundle"
        assemble(upstream, folder)
        index = index_sources(folder)
        for module, error in sorted(index["failed"].items()):
            print(f"Leaving out {module}: {error}", file=sys.stderr)
        args.out.mkdir(parents=True, exist_ok=True)
        pack(folder, index, args.out / "lncrawl-sources.zip")
        license_text = (folder / "LICENSE").read_text(encoding="utf-8")

    data = (args.out / "lncrawl-sources.zip").read_bytes()
    crawlers = [c for c in index["crawlers"] if not c.get("disabled")]
    meta = {"engine": "lncrawl", "commit": sha, "date": date, "ref": args.ref, "shim": shim_key(), "api": API,
            "crawlers": len(crawlers), "excluded": sorted(index["failed"]),
            "sites": sorted({url for c in crawlers for url in c["base_urls"]}),
            "file": "lncrawl-sources.zip", "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
            "built": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    (args.out / "manifest.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8", newline="\n")
    (args.out / "LICENSE").write_text(license_text, encoding="utf-8", newline="\n")
    if args.release_dir:
        args.release_dir.mkdir(parents=True, exist_ok=True)
        name = f"lncrawl-sources-{sha[:12]}-{meta['shim']}.zip"
        shutil.copyfile(args.out / "lncrawl-sources.zip", args.release_dir / name)
        (args.release_dir / "lncrawl-manifest.json").write_text(
            json.dumps(dict(meta, file=name), indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"lightnovel-crawler {sha[:12]} ({date}): {len(crawlers)} crawlers, {len(meta['sites'])} sites, "
          f"{len(index['failed'])} left out, {len(data) / 1e6:.1f} MB -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
