"""Build on-demand title shards: each work's English titles by source and ID.

A catalog chunk holds tens of thousands of titles, too many to read for the sake
of one work. A title shard holds about a thousand, and is placed with the
synopsis shards' numeric-modulo-v1 algorithm, so a reader that knows a source and
an ID fetches one small file and looks the ID up:

    {source}_titles_shard_{index:03d}.json.gz      index = abs(int(id)) % 128

    {"titles": {"<id>": "English title"},
     "nu": {"<id>": {"title": "NovelUpdates title", "url": "https://..."}}}

`titles` is the machine translation the catalog displays, exactly as its chunks
hold it. `nu` is there for a work confirmed on NovelUpdates (scripts/nu_titles.py):
its title there and the address of its page. A work without an English title of
either kind is absent.

The website does not read these (its chunks already hold every title), so they
are published on the data branch and left out of the Pages deployment.

Usage:
    python scripts/chunk_titles.py --data-dir docs/data --state-dir metadata/state
"""
import argparse
import gzip
import json
from pathlib import Path

try:
    from . import source_names
    from .chunk_and_compress import load_translations_file
    from .chunk_descriptions import shard_index
    from .metadata_common import atomic_bytes, atomic_json
    from .nu_titles import ADDRESSES_NAME, SOURCES, corpus_path
except ImportError:
    import source_names
    from chunk_and_compress import load_translations_file
    from chunk_descriptions import shard_index
    from metadata_common import atomic_bytes, atomic_json
    from nu_titles import ADDRESSES_NAME, SOURCES, corpus_path

ROOT = Path(__file__).resolve().parents[1]
SHARDS = 128


def read_addresses(path):
    """{source: {id: {title, url}}} as scripts/nu_titles.py recorded it, or nothing yet."""
    path = Path(path)
    if not path.exists():
        return {}
    data = json.loads(gzip.decompress(path.read_bytes()))
    if data.get("version") != 1 or not isinstance(data.get("sources"), dict):
        raise ValueError("Invalid verified title addresses")
    return data["sources"]


def build(source, data_dir, addresses, shards=SHARDS):
    """Write one source's shards and manifest. Returns the manifest."""
    data_dir = Path(data_dir)
    titles = load_translations_file(str(corpus_path(source, data_dir)))
    buckets = [{"titles": {}, "nu": {}} for _ in range(shards)]
    for novel_id in sorted(titles):
        buckets[shard_index(novel_id, shards)]["titles"][novel_id] = titles[novel_id]
    verified = 0
    for novel_id, entry in sorted(addresses.get(source, {}).items()):
        title, url = entry.get("title"), entry.get("url")
        if isinstance(title, str) and title and isinstance(url, str) and url:
            buckets[shard_index(novel_id, shards)]["nu"][str(novel_id)] = {"title": title, "url": url}
            verified += 1
    files = []
    for index, bucket in enumerate(buckets):
        name = f"{source}_titles_shard_{index:03d}.json.gz"
        raw = source_names.dumps(bucket, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        atomic_bytes(data_dir / name, gzip.compress(raw, compresslevel=9, mtime=0))
        files.append(name)
    manifest = {"shards": shards, "titles": len(titles), "verified": verified,
                "algorithm": "numeric-modulo-v1", "format": "id-to-title-json", "files": files}
    atomic_json(data_dir / f"{source}_titles_shard_manifest.json", manifest)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", choices=(*SOURCES, "all"), default="all")
    parser.add_argument("--data-dir", type=Path, default=ROOT / "docs/data")
    parser.add_argument("--state-dir", type=Path, default=ROOT / "metadata/state")
    args = parser.parse_args()
    addresses = read_addresses(args.state_dir / ADDRESSES_NAME)
    for source in SOURCES if args.source == "all" else (args.source,):
        if not corpus_path(source, args.data_dir).exists():
            print(f"{source}: no title corpus, skipped", flush=True)
            continue
        manifest = build(source, args.data_dir, addresses)
        size = sum((args.data_dir / name).stat().st_size for name in manifest["files"])
        print(f"{source}: {manifest['titles']:,} titles, {manifest['verified']:,} verified, "
              f"{size / 1048576:.1f} MiB in {manifest['shards']} shards", flush=True)


if __name__ == "__main__":
    main()
