"""Write a release's update-<platform>.json, which the app updaters read (noveldb/core/app_update.py).

    python scripts/update_manifest.py --platform windows --tag v98 --build 1793193554 --label none \\
        --commit <sha> [--existing old.json] --out update-windows.json FILE...

Each FILE gets an entry with its variant, architecture and kind (from its name), size and SHA-256. Entries of
an existing manifest for files not rebuilt now are kept, so a run that rebuilds one variant leaves the others.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

SCHEMA = 1
PLATFORMS = ("windows", "macos", "linux", "android", "ios")
DESKTOP = re.compile(r"^NovelDB\d+(?P<variant>_Lite|_TurboLite)?-(?P<os>windows|macos|linux)-(?P<arch>x86_64|arm64)"
                     r"(?:_[A-Za-z0-9._-]+)?\.(?P<ext>exe|zip|dmg|AppImage)$")
MOBILE = {
    "android": (re.compile(r"^NovelDB-universal(?:_[A-Za-z0-9._-]+)?\.apk$"), "apk"),
    "ios": (re.compile(r"^NovelDB-unsigned(?:_[A-Za-z0-9._-]+)?\.ipa$"), "ipa"),
}
VARIANTS = {None: "full", "_Lite": "lite", "_TurboLite": "turbolite"}
KINDS = {"exe": "exe", "zip": "zip", "dmg": "dmg", "AppImage": "appimage"}


def describe(name: str, platform: str) -> dict | None:
    """variant, arch and kind of a release file, or None when it is not this platform's app."""
    if platform in MOBILE:
        pattern, kind = MOBILE[platform]
        return {"variant": "universal", "arch": "universal", "kind": kind} if pattern.match(name) else None
    match = DESKTOP.match(name)
    if not match or match["os"] != platform:
        return None
    return {"variant": VARIANTS[match["variant"]], "arch": match["arch"], "kind": KINDS[match["ext"]]}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_manifest(platform: str, tag: str, build: int, label: str, commit: str, files: list[Path],
                   existing: dict | None = None, repo: str = "Shirochi-stack/NovelDB") -> dict:
    entries = []
    for path in files:
        info = describe(path.name, platform)
        if info is None:
            raise SystemExit(f"{path.name} is not a {platform} app file")
        entries.append({"name": path.name, **info, "build": build, "label": label or "none", "commit": commit,
                        "size": path.stat().st_size, "sha256": sha256(path)})
    rebuilt = {(e["variant"], e["arch"], e["kind"]) for e in entries}
    kept = []
    if existing and existing.get("schema") == SCHEMA and existing.get("platform") == platform \
            and existing.get("tag") == tag:
        kept = [e for e in existing.get("assets") or []
                if isinstance(e, dict) and (e.get("variant"), e.get("arch"), e.get("kind")) not in rebuilt]
    assets = sorted(kept + entries, key=lambda e: (e["kind"], e["variant"], e["arch"]))
    return {"schema": SCHEMA, "platform": platform, "tag": tag,
            "release_url": f"https://github.com/{repo}/releases/tag/{tag}", "assets": assets}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--platform", required=True, choices=PLATFORMS)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--build", required=True, type=int)
    parser.add_argument("--label", default="none")
    parser.add_argument("--commit", default="")
    parser.add_argument("--repo", default="Shirochi-stack/NovelDB")
    parser.add_argument("--existing", default="", help="the manifest already on the release, if any")
    parser.add_argument("--out", required=True)
    parser.add_argument("files", nargs="+", type=Path)
    args = parser.parse_args(argv)
    if args.build <= 0:
        parser.error("--build must be a positive Unix time")
    existing = None
    if args.existing and Path(args.existing).is_file():
        try:
            existing = json.loads(Path(args.existing).read_text(encoding="utf-8"))
        except ValueError:
            existing = None
    manifest = build_manifest(args.platform, args.tag, args.build, args.label, args.commit, args.files,
                              existing, args.repo)
    Path(args.out).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")
    for entry in manifest["assets"]:
        print(f"{entry['name']}: {entry['variant']} {entry['arch']} {entry['kind']} build {entry['build']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
