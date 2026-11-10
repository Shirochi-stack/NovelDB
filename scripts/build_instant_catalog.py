"""Package the local title index using the repository's normalized catalogue and wire serialization."""
import argparse
from contextlib import ExitStack
import gzip
import json
from pathlib import Path
import subprocess
import tempfile
from zipfile import ZipFile, ZipInfo, ZIP_STORED

try:
    from .source_names import dumps
    from .nu_titles import TitleIndex, PUBLISHERS, raw_path
except ImportError:
    from source_names import dumps
    from nu_titles import TitleIndex, PUBLISHERS, raw_path

ROOT = Path(__file__).resolve().parents[1]


def build(data, destination):
    (ROOT / "artifacts").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="instant-", dir=ROOT / "artifacts") as temporary:
        folder = Path(temporary)
        index = TitleIndex.load()
        aliases = {}
        for source in PUBLISHERS:
            for row in json.loads(raw_path(source, data).read_text(encoding="utf-8")):
                match = index.match(row[1], row[2], source)
                if match:
                    aliases[f"{source}/{row[0]}"] = match["aliases"]
        alias_file = folder / "aliases.input"
        alias_file.write_text(dumps(aliases, ensure_ascii=False), encoding="utf-8")
        subprocess.run(["node", "--max-old-space-size=8192", str(ROOT / "scripts/noveldb_index/instant.cjs"),
                        str(data), str(folder), str(alias_file)], check=True)
        destination.parent.mkdir(parents=True, exist_ok=True)
        targets = [destination.with_name(f"{destination.stem}-{part}{destination.suffix}") for part in range(2)]
        pending = [target.with_suffix(".tmp") for target in targets]
        with ExitStack() as stack:
            archives = [stack.enter_context(ZipFile(path, "w", compression=ZIP_STORED)) for path in pending]
            for path in sorted(folder.rglob("*.json")):
                value = json.loads(path.read_text(encoding="utf-8"))
                raw = dumps(value, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
                name = path.relative_to(folder).as_posix()
                part = int(Path(name).stem) % 2 if "/" in name else 0
                info = ZipInfo(name + ".gz", date_time=(1980, 1, 1, 0, 0, 0))
                archives[part].writestr(info, gzip.compress(raw, compresslevel=6, mtime=0))
        for source, target in zip(pending, targets):
            source.replace(target)
    size = sum(target.stat().st_size for target in targets)
    print(f"Local catalogue archives {size / 1048576:.1f} MiB", flush=True)


def build_starter(destination):
    """Embed a bounded popular-title index before the search UI initializes."""
    selected = set(range(256))
    rows = []
    for part in range(2):
        with ZipFile(destination.with_name(f"{destination.stem}-{part}{destination.suffix}")) as archive:
            for name in archive.namelist():
                if not name.startswith("c/"):
                    continue
                start = int(Path(name).stem.split(".")[0]) * 128
                for offset, row in enumerate(json.loads(gzip.decompress(archive.read(name)))):
                    if start + offset in selected or row[16]:
                        # The first frame needs identity, cover and title. Full metadata follows.
                        row[6] = row[6][:3]
                        row[9] = 0
                        row[12] = row[13] = None
                        rows.append((start + offset, row))
    rows.sort(key=lambda entry: entry[0])
    selected.update(number for number, row in [entry for entry in rows if entry[1][16]][:512])
    rows = [entry for entry in rows if entry[0] in selected]
    with tempfile.TemporaryDirectory(prefix="starter-", dir=ROOT / "artifacts") as temporary:
        folder = Path(temporary)
        source = folder / "rows.input"
        output = folder / "starter.output"
        source.write_text(dumps([row for _, row in rows], ensure_ascii=True), encoding="utf-8")
        subprocess.run(["node", str(ROOT / "scripts/noveldb_index/starter.cjs"), str(source), str(output)], check=True)
        value = json.loads(output.read_text(encoding="utf-8"))
        with ZipFile(destination.with_name(f"{destination.stem}-0{destination.suffix}")) as archive:
            value["top"] = json.loads(gzip.decompress(archive.read("top.json.gz")))[:12]
        target = ROOT / "noveldb/qt/catalog/generic/instant-boot.js"
        target.write_text("window.NovelDBInstantBoot=" + dumps(value, ensure_ascii=True, separators=(",", ":")) + ";\n", encoding="utf-8")
        print(f"Startup catalogue {len(rows)} books, {target.stat().st_size / 1024:.0f} KiB", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=ROOT / "docs/data")
    parser.add_argument("--output", type=Path, default=ROOT / "assets/instant-catalog.zip")
    parser.add_argument("--starter-only", action="store_true", help="refresh the resident first-page subset from existing archives")
    args = parser.parse_args()
    if not args.starter_only:
        build(args.data, args.output)
    build_starter(args.output)
