"""Stage the website with runtime synopsis shards, checking references and the Pages size limit."""

import argparse
import json
import re
import shutil
from pathlib import Path


PAGES_LIMIT = 1_000_000_000
BULK_DESCRIPTIONS = re.compile(r"(?:[a-z]+_)?descriptions(?:(?:_chunk_\d+)?\.txt(?:\.gz)?|_chunk_manifest\.json)$")


def site_files(source):
    return [path for path in source.rglob("*") if path.is_file()
            and not (path.parent == source / "data" and BULK_DESCRIPTIONS.fullmatch(path.name))]


def validate_references(source, files):
    available = {path.relative_to(source).as_posix() for path in files}
    manifests = sorted(path for path in (source / "data").glob("*_chunk_manifest.json")
                       if "data/" + path.name in available)
    if not manifests:
        raise ValueError("No catalog manifests were found")
    for path in manifests:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        required = list(manifest["files"])
        required.extend(manifest[key] for key in ("previewUrl", "previewSortsUrl", "topUrl") if manifest.get(key))
        for kind in ("description", "details"):
            prefix = manifest.get(kind + "ShardPrefix")
            count = manifest.get(kind + "ShardCount", 0)
            if prefix:
                required.extend(f"{prefix}{index:03d}.json.gz" for index in range(count))
        missing = [name for name in required if "data/" + name not in available]
        if missing:
            raise ValueError(f"{path.name} references missing files: {', '.join(missing)}")


def stage(source, output, limit=PAGES_LIMIT):
    source, output = Path(source).resolve(), Path(output).resolve()
    if not source.is_dir():
        raise ValueError("The site source directory does not exist")
    if output == source or source in output.parents:
        raise ValueError("The staging directory must be outside the source directory")
    if output.exists():
        raise ValueError("Use a new staging directory")
    files = site_files(source)
    validate_references(source, files)
    size = sum(path.stat().st_size for path in files)
    if size > limit:
        raise ValueError(f"The staged site is {size:,} bytes, above the {limit:,}-byte Pages limit")
    for path in files:
        target = output / path.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    original = sum(path.stat().st_size for path in source.rglob("*") if path.is_file())
    print(f"Pages site: {len(files):,} files, {size:,} bytes (limit {limit:,}).")
    print(f"Bulk synopsis exports excluded from the deployment: {original - size:,} bytes.")
    return size


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("docs"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    stage(args.source, args.output)
