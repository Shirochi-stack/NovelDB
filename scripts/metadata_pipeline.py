"""Prepare, translate, package, and promote the three anonymous metadata sources.

Imports are side-effect free. Collection and paid translation are explicit CLI
operations; prepare/build/promote operate exclusively on local metadata.
"""

from __future__ import annotations

import argparse
import gzip
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ("naver", "joara", "munpia")
SHARDS = 128
FIELDS = {
    "title": ("titles_en.txt", "titles_untranslated.txt"),
    "synopsis": ("descriptions.txt", "descriptions_untranslated.txt"),
}


def common():
    # Delay the import so CLI help and static tooling do not load adapters.
    if __package__:
        from . import metadata_common
    else:
        import metadata_common
    return metadata_common


def source_name(source):
    if source not in SOURCES:
        raise ValueError(f"Unknown metadata source: {source}")
    return source


def clean_field(text):
    """Escape the interchange format; the normalized state retains raw text."""
    return json.dumps(str(text or ""), ensure_ascii=False)[1:-1].replace("|", r"\u007c")


def decode_field(text):
    """Decode escapes once while tolerating unescaped quotes from a translator."""
    text = str(text or "")
    try:
        return json.loads('"' + text + '"')
    except json.JSONDecodeError:
        return re.sub(r'\\(?:["\\/bfnrt]|u[0-9a-fA-F]{4})',
                      lambda match: json.loads('"' + match.group(0) + '"'), text)


def valid_english(text):
    text = str(text or "").strip()
    return bool(text) and any(c.isascii() and c.isalpha() for c in text) and not any(
        "\u3040" <= c <= "\u30ff" or "\u3400" <= c <= "\u4dbf"
        or "\u4e00" <= c <= "\u9fff" or "\uf900" <= c <= "\ufaff"
        or "\uac00" <= c <= "\ud7af" for c in text
    )


def read_corpus(path):
    path = Path(path)
    if not path.exists() and path.with_name(path.name + ".gz").exists():
        path = path.with_name(path.name + ".gz")
    if not path.exists():
        return {}
    opener = gzip.open if path.suffix == ".gz" else open
    result = {}
    with opener(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            parts = line.rstrip("\r\n").split("|||", 2)
            if len(parts) != 3 or not parts[0].strip():
                continue
            result[parts[0].strip()] = (parts[1], parts[2].strip())
    return result


def read_tags(path):
    path = Path(path)
    if not path.exists() and path.with_name(path.name + ".gz").exists():
        path = path.with_name(path.name + ".gz")
    if not path.exists():
        return {}
    opener = gzip.open if path.suffix == ".gz" else open
    result = {}
    with opener(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            parts = line.rstrip("\r\n").split("|||", 1)
            if len(parts) == 2 and parts[0] and parts[1].strip():
                result[parts[0]] = parts[1].strip()
    return result


def read_extra_tags(path):
    path = Path(path)
    if not path.exists():
        return {}
    data = read_gzip_json(path)
    if not isinstance(data, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in data.items()):
        raise ValueError("Extra tag translations must be a JSON string map")
    return data


def known_tags(output_dir, shared_tags=None):
    shared_path = Path(shared_tags or ROOT / "docs/data/tags_en.txt")
    tags = read_tags(shared_path)
    for path, reader in ((shared_path.parent / "tags_extra.json.gz", read_extra_tags),
                         (Path(output_dir) / "tags_en.txt", read_tags),
                         (Path(output_dir) / "tags_extra.json.gz", read_extra_tags)):
        for tag, english in reader(path).items():
            tags.setdefault(tag, english)
    return tags


def write_tags(output_dir, tags):
    """Keep the existing text map compatible; special strings use a JSON map."""
    ordinary, extra = {}, {}
    for tag, english in sorted(tags.items()):
        target = extra if any(token in tag or token in english for token in ("|||", "\n", "\r")) else ordinary
        target[tag] = english
    if ordinary:
        text = "".join(f"{tag}|||{english}\n" for tag, english in ordinary.items())
        common().atomic_text(Path(output_dir) / "tags_en.txt", text)
        atomic_bytes(Path(output_dir) / "tags_en.txt.gz", gzip.compress(text.encode("utf-8"), compresslevel=6, mtime=0))
    if extra or (Path(output_dir) / "tags_extra.json.gz").exists():
        gzip_json(Path(output_dir) / "tags_extra.json.gz", extra)


def atomic_bytes(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.",
                                         suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def gzip_json(path, data):
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    atomic_bytes(path, gzip.compress(raw, compresslevel=6, mtime=0))


def state_for(source, state_dir):
    state_dir = Path(state_dir).resolve()
    if state_dir.is_relative_to((ROOT / "docs").resolve()):
        raise ValueError("Durable metadata state must remain outside the website directory")
    state = common().load_state(source_name(source), state_dir)
    if state.get("source") != source:
        raise ValueError("State source does not match requested source")
    if not state.get("records"):
        raise ValueError(f"No normalized {source} records are available")
    return state


def ordered_records(state):
    return sorted(state["records"].items(), key=lambda item: str(item[0]))


def active_translation(record, field):
    translation = record.get("translations", {}).get(field, {})
    if (isinstance(translation, dict)
            and translation.get("original") == str(record.get(field) or "")
            and valid_english(translation.get("english"))):
        return translation["english"].strip()
    return ""


def expire_translation(record, field):
    translations = record.setdefault("translations", {})
    old = translations.get(field)
    if not isinstance(old, dict) or old.get("original") == str(record.get(field) or ""):
        return
    record.setdefault("translation_history", []).append({
        **old, "field": field, "invalidated_at": common().utc_now(),
    })
    translations.pop(field, None)


def write_field_corpus(state, field, output_dir, write_pending=True):
    source = state["source"]
    master_name, pending_name = FIELDS[field]
    translated, pending = [], []
    for nid, record in ordered_records(state):
        if not record.get("title"):
            continue
        original = clean_field(record.get(field))
        if not original and field == "synopsis":
            continue
        english = clean_field(active_translation(record, field))
        row = f"{nid}|||{original}|||{english}\n"
        (translated if english else pending).append(row)
    common().atomic_text(Path(output_dir) / f"{source}_{master_name}", "".join(translated + pending))
    if write_pending:
        common().atomic_text(Path(output_dir) / f"{source}_{pending_name}", "".join(pending))


def prepare(source, output_dir, state_dir, shared_tags=None, fields=None):
    """Create source-local corpora while invalidating changed-original English."""
    source_name(source)
    output_dir = Path(output_dir)
    state = state_for(source, state_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    chosen = tuple(fields) if fields is not None else tuple(FIELDS)
    for field in chosen:
        master = read_corpus(output_dir / f"{source}_{FIELDS[field][0]}")
        for nid, record in ordered_records(state):
            had_translation = field in record.get("translations", {})
            expire_translation(record, field)
            # Bootstrap a matching existing master only; never revive an English
            # value whose recorded original has just changed.
            original, english = master.get(str(nid), (None, ""))
            english = decode_field(english)
            if (not had_translation and field not in record.get("translations", {})
                    and original == clean_field(record.get(field)) and valid_english(english)):
                record.setdefault("translations", {})[field] = {
                    "original": str(record.get(field) or ""), "english": english,
                    "translated_at": common().utc_now(),
                }
        write_field_corpus(state, field, output_dir)
    if fields is None:
        known = known_tags(output_dir, shared_tags)
        for tag, english in state.get("tag_translations", {}).items():
            known.setdefault(tag, english)
        counts = {}
        for _, record in ordered_records(state):
            for tag in set(record.get("tags") or []):
                tag = str(tag)
                if tag and tag not in known:
                    counts[tag] = counts.get(tag, 0) + 1
        pending_tags = sorted(counts, key=lambda tag: (-counts[tag], tag))
        common().atomic_text(output_dir / f"{source}_tags_untranslated.txt", "".join(
            f"{i}|||{clean_field(tag)}|||\n" for i, tag in enumerate(pending_tags)
        ))
    common().save_state(state, Path(state_dir))
    return state


def merge(source, output_dir, state_dir, shared_tags=None):
    """Accept English only for a patch whose source-local original still matches."""
    output_dir = Path(output_dir)
    state = state_for(source, state_dir)
    accepted, rejected = 0, 0
    for field, (_, pending_name) in FIELDS.items():
        patches = read_corpus(output_dir / f"{source}_{pending_name}")
        for nid, record in ordered_records(state):
            expire_translation(record, field)
            if str(nid) not in patches:
                continue
            original, english = patches[str(nid)]
            english = decode_field(english)
            if not valid_english(english):
                continue
            if original != clean_field(record.get(field)):
                rejected += 1
                continue
            if not active_translation(record, field):
                record.setdefault("translations", {})[field] = {
                    "original": str(record.get(field) or ""), "english": english,
                    "translated_at": common().utc_now(),
                }
                accepted += 1
        write_field_corpus(state, field, output_dir)
    tags = known_tags(output_dir, shared_tags)
    for tag, english in state.get("tag_translations", {}).items():
        tags.setdefault(tag, english)
    source_tags = {str(tag) for record in state["records"].values()
                   for tag in record.get("tags", [])}
    pending_tags = read_corpus(output_dir / f"{source}_tags_untranslated.txt")
    for original, english in pending_tags.values():
        original, english = decode_field(original), decode_field(english)
        if original in source_tags and valid_english(english):
            tags.setdefault(original, english)
    write_tags(output_dir, tags)
    state.setdefault("tag_translations", {}).update({
        tag: tags[tag] for tag in source_tags if tag in tags
    })
    common().atomic_text(output_dir / f"{source}_tags_untranslated.txt", "".join(
        f"{nid}|||{original}|||\n" for nid, (original, _) in pending_tags.items()
        if decode_field(original) not in tags
    ))
    common().save_state(state, Path(state_dir))
    return {"accepted": accepted, "rejected_stale": rejected}


def run_python(script, args, **kwargs):
    subprocess.run([sys.executable, str(ROOT / "scripts" / script), *map(str, args)],
                   check=True, cwd=ROOT, **kwargs)


def translate(source, output_dir, options=()):
    """The only pipeline operation that calls the configured translation API."""
    source_name(source)
    output_dir = Path(output_dir)
    for suffix, kind in (("titles_untranslated.txt", "titles"),
                         ("descriptions_untranslated.txt", "descriptions"),
                         ("tags_untranslated.txt", "tags")):
        path = output_dir / f"{source}_{suffix}"
        if not path.exists():
            continue
        if not any(not valid_english(decode_field(english)) for _, english in read_corpus(path).values()):
            continue
        run_python("translate_with_grok.py", [path, "--lang", "korean", "--type", kind, *options])


def top_payload(state, rows):
    by_id = {str(row[0]): row for row in rows}
    selected = []
    seen = set()
    for board in state.get("boards", {}).values():
        entries = sorted(board.get("records", []), key=lambda row: (row.get("rank") or 10**9, str(row.get("id"))))
        for entry in entries:
            nid = str(entry.get("id", ""))
            if nid in by_id and nid not in seen and entry.get("rank", 0) > 0:
                selected.append(by_id[nid])
                seen.add(nid)
            if len(selected) == 100:
                break
        if len(selected) == 100:
            break
    translations, descriptions = {}, {}
    for row in selected:
        nid = str(row[0])
        record = state["records"][nid]
        english = active_translation(record, "title")
        if english:
            translations[nid] = english
        synopsis = active_translation(record, "synopsis") or record.get("synopsis")
        if synopsis:
            descriptions[nid] = synopsis
    return {"novels": selected, "translations": translations, "descriptions": descriptions}


def build(source, output_dir, state_dir):
    source_name(source)
    output_dir = Path(output_dir).resolve()
    state = state_for(source, state_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = common().export_rows(state)
    if not rows:
        raise ValueError("Refusing to build an empty catalog")
    for field in FIELDS:
        for record in state["records"].values():
            expire_translation(record, field)
        write_field_corpus(state, field, output_dir, write_pending=False)
    common().save_state(state, Path(state_dir))
    catalog = output_dir / f"{source}_novels.json"
    tags = known_tags(output_dir)
    for tag, english in state.get("tag_translations", {}).items():
        tags.setdefault(tag, english)
    write_tags(output_dir, tags)
    common().atomic_json(catalog, rows)
    synopsis = output_dir / f"{source}_descriptions.txt"
    run_python("gzip_text_files.py", [synopsis])
    run_python("chunk_descriptions.py", [synopsis, "--prefix", f"{source}_descriptions_shard",
               "--output-dir", output_dir, "-n", SHARDS])
    run_python("chunk_and_compress.py", ["--input", catalog, "--prefix", f"{source}_chunk",
               "--output-dir", output_dir, "-n", max(1, math.ceil(len(rows) / 20000)),
               "--translations", output_dir / f"{source}_titles_en.txt"])
    manifest_path = output_dir / f"{source}_chunk_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    # The legacy builders read escaped interchange strings. Only new-source
    # JSON artifacts are rewritten with raw state text for correct display.
    for name in manifest["files"]:
        payload = read_gzip_json(output_dir / name)
        payload["translations"] = {
            str(row[0]): active_translation(state["records"][str(row[0])], "title")
            for row in payload["novels"]
            if active_translation(state["records"][str(row[0])], "title")
        }
        gzip_json(output_dir / name, payload)
    for index in range(SHARDS):
        path = output_dir / f"{source}_descriptions_shard_{index:03d}.json.gz"
        payload = read_gzip_json(path)
        payload = {nid: active_translation(state["records"][nid], "synopsis") or state["records"][nid].get("synopsis", "")
                   for nid in payload}
        gzip_json(path, payload)
    gzip_json(output_dir / f"{source}_top.json.gz", top_payload(state, rows))
    manifest.update({
        "format": "metadata-v1", "source": source,
        "descriptionShardCount": SHARDS,
        "descriptionShardPrefix": f"{source}_descriptions_shard_",
        "topUrl": f"{source}_top.json.gz",
        "boards": {key: {field: board.get(field) for field in ("label", "observed_at", "stale")}
                   for key, board in state.get("boards", {}).items()},
        "coverage": state.get("coverage", {}),
    })
    common().atomic_json(manifest_path, manifest)
    return manifest


def read_gzip_json(path):
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return json.load(handle)


def validate_artifacts(source, output_dir):
    """Return a strict source-only file list after checking artifact joins."""
    source_name(source)
    output_dir = Path(output_dir).resolve()
    manifest_name = f"{source}_chunk_manifest.json"
    manifest = json.loads((output_dir / manifest_name).read_text(encoding="utf-8"))
    if manifest.get("format") != "metadata-v1" or manifest.get("source") != source:
        raise ValueError("Wrong catalog manifest source or format")
    rows = json.loads((output_dir / f"{source}_novels.json").read_text(encoding="utf-8"))
    if not rows or any(not isinstance(row, list) or len(row) != 16 for row in rows):
        raise ValueError("Catalog must contain nonempty metadata-v1 rows")
    json.dumps(rows, allow_nan=False)
    ids = [str(row[0]) for row in rows]
    if len(set(ids)) != len(ids) or any(not nid for nid in ids):
        raise ValueError("Catalog IDs must be unique and nonempty")
    hosts = {"naver": {"novel.naver.com"}, "joara": {"www.joara.com", "joara.com"},
             "munpia": {"www.munpia.com", "munpia.com"}}
    for row in rows:
        link = urlsplit(str(row[11] or ""))
        if not row[1] or link.scheme != "https" or link.hostname not in hosts[source]:
            raise ValueError("Catalog needs a title and source-owned canonical HTTPS URL")
    if manifest.get("totalEntries") != len(rows):
        raise ValueError("Catalog count disagrees with manifest")
    files = manifest.get("files", [])
    if not files or manifest.get("chunks") != len(files) or len(set(files)) != len(files):
        raise ValueError("Invalid catalog chunk file list")
    if any(not re.fullmatch(re.escape(source) + r"_chunk_\d+\.json\.gz", name) for name in files):
        raise ValueError("Catalog manifest contains a foreign or unsafe file path")
    concatenated = []
    for name in files:
        payload = read_gzip_json(output_dir / name)
        if not isinstance(payload, dict) or not isinstance(payload.get("novels"), list):
            raise ValueError("Catalog chunks must contain embedded novels objects")
        concatenated.extend(payload["novels"])
        chunk_ids = {str(row[0]) for row in payload["novels"]}
        if not set(payload.get("translations", {})).issubset(chunk_ids):
            raise ValueError("A title translation belongs to a different chunk")
    if concatenated != rows:
        raise ValueError("Catalog chunks disagree with original rows")
    shard_manifest_name = f"{source}_descriptions_shard_manifest.json"
    shard_manifest = json.loads((output_dir / shard_manifest_name).read_text(encoding="utf-8"))
    expected_shards = [f"{source}_descriptions_shard_{index:03d}.json.gz" for index in range(SHARDS)]
    if (shard_manifest.get("shards") != SHARDS or shard_manifest.get("files") != expected_shards
            or shard_manifest.get("algorithm") != "numeric-modulo-v1"
            or manifest.get("descriptionShardCount") != SHARDS
            or manifest.get("descriptionShardPrefix") != f"{source}_descriptions_shard_"):
        raise ValueError("Invalid synopsis shard manifest")
    known_ids = set(ids)
    for index, name in enumerate(expected_shards):
        descriptions = read_gzip_json(output_dir / name)
        if not isinstance(descriptions, dict) or not set(descriptions).issubset(known_ids):
            raise ValueError("A synopsis shard contains unknown source IDs")
        for nid in descriptions:
            if nid.isdigit():
                shard = int(nid) % SHARDS
            else:
                shard = 0
                for char in nid:
                    shard = (shard * 31 + ord(char)) & 0xFFFFFFFF
                shard %= SHARDS
            if shard != index:
                raise ValueError("A synopsis is stored in the wrong shard")
    top_name = f"{source}_top.json.gz"
    if manifest.get("topUrl") != top_name:
        raise ValueError("Invalid top artifact path")
    top = read_gzip_json(output_dir / top_name)
    top_ids = [str(row[0]) for row in top.get("novels", [])]
    if len(top_ids) > 100 or len(set(top_ids)) != len(top_ids) or not set(top_ids).issubset(known_ids):
        raise ValueError("Invalid top-bundle source IDs")
    by_id = dict(zip(ids, rows))
    if any(row != by_id[str(row[0])] for row in top.get("novels", [])):
        raise ValueError("Top-bundle rows disagree with catalog")
    if not set(top.get("translations", {})).issubset(top_ids) or not set(top.get("descriptions", {})).issubset(top_ids):
        raise ValueError("Top translations or synopses have unknown IDs")
    required = [f"{source}_novels.json", f"{source}_titles_en.txt", f"{source}_descriptions.txt",
                f"{source}_descriptions.txt.gz", *files, *expected_shards, top_name]
    for name in required:
        path = output_dir / name
        if not path.is_file() or path.resolve().parent != output_dir:
            raise ValueError(f"Missing or external artifact: {name}")
    optional = [f"{source}_titles_untranslated.txt", f"{source}_descriptions_untranslated.txt",
                f"{source}_tags_untranslated.txt"]
    required.extend(name for name in optional if (output_dir / name).is_file())
    for name in required + [shard_manifest_name, manifest_name]:
        if (output_dir / name).resolve().parent != output_dir:
            raise ValueError("Artifact resolves outside its source directory")
    # Copy manifests last so their referenced data has already been installed.
    return required + [shard_manifest_name, manifest_name], manifest


def promote(source, output_dir, target_dir, include_tags=False, allow_partial=False):
    output_dir, target_dir = Path(output_dir).resolve(), Path(target_dir).resolve()
    files, manifest = validate_artifacts(source, output_dir)
    coverage = manifest.get("coverage") or {}
    if not allow_partial and not (coverage.get("complete") is True or coverage.get("has_complete_baseline") is True):
        raise ValueError("Promotion needs a complete catalog baseline or explicit --allow-partial for validated progress with coverage reporting")
    if coverage.get("mode") == "sample" and target_dir.is_relative_to((ROOT / "docs").resolve()):
        raise ValueError("Sample artifacts may be promoted only into an isolated preview directory")
    target_dir.mkdir(parents=True, exist_ok=True)
    if include_tags:
        for name in ("tags_en.txt", "tags_en.txt.gz", "tags_extra.json.gz"):
            if (output_dir / name).exists() and (output_dir / name).resolve().parent != output_dir:
                raise ValueError("Shared tags resolve outside staging")
        # Merge against the destination again, preserving durable translations.
        tags = read_tags(target_dir / "tags_en.txt")
        for tag, english in read_extra_tags(target_dir / "tags_extra.json.gz").items():
            tags.setdefault(tag, english)
        for tag, english in known_tags(output_dir, output_dir / "tags_en.txt").items():
            tags.setdefault(tag, english)
        write_tags(target_dir, tags)
    for name in files:
        origin, target = output_dir / name, target_dir / name
        if origin.resolve().parent != output_dir or target.resolve().parent != target_dir:
            raise ValueError("Promotion path escapes its source or target directory")
        if origin != target:
            atomic_bytes(target, origin.read_bytes())
    return files


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "translate", "merge", "build", "promote", "run"))
    parser.add_argument("--source", required=True, choices=SOURCES)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--state-dir", type=Path)
    parser.add_argument("--target-dir", type=Path, default=ROOT / "docs/data")
    parser.add_argument("--shared-tags", type=Path, default=ROOT / "docs/data/tags_en.txt")
    parser.add_argument("--include-tags", action="store_true")
    parser.add_argument("--allow-partial", action="store_true")
    parser.add_argument("--mode", choices=("sample", "catalog", "rankings"), default="catalog")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--max-runtime", type=int)
    parser.add_argument("--dry-run", action="store_true", help="Validate run configuration without collection or downstream writes")
    parser.add_argument("--translate", action="store_true", help="Explicitly call translation API during run")
    args, extra = parser.parse_known_args(argv)
    output = args.output_dir.resolve()
    state_dir = (args.state_dir or output / ".state").resolve()
    if args.command not in ("run", "translate") and extra:
        parser.error("unrecognized arguments: " + " ".join(extra))
    if args.dry_run and args.command != "run":
        parser.error("--dry-run applies to the run command")
    if args.command == "run":
        options = ["--output-dir", output, "--state-dir", state_dir, "--mode", args.mode]
        if args.max_runtime is not None:
            options.extend(["--max-runtime", args.max_runtime])
        if args.resume:
            options.append("--resume")
        if args.dry_run:
            options.append("--dry-run")
        run_python(f"scrape_{args.source}.py", [*options, *extra])
        if args.dry_run:
            return 0
        prepare(args.source, output, state_dir, args.shared_tags)
        if args.translate:
            translate(args.source, output)
            merge(args.source, output, state_dir, args.shared_tags)
        build(args.source, output, state_dir)
    elif args.command == "prepare":
        prepare(args.source, output, state_dir, args.shared_tags)
    elif args.command == "translate":
        translate(args.source, output, extra)
    elif args.command == "merge":
        result = merge(args.source, output, state_dir, args.shared_tags)
        print(json.dumps(result))
    elif args.command == "build":
        build(args.source, output, state_dir)
    elif args.command == "promote":
        files = promote(args.source, output, args.target_dir, args.include_tags, args.allow_partial)
        print(f"Promoted {len(files)} {args.source} artifacts to {args.target_dir}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, FileNotFoundError) as exc:
        print(f"Metadata pipeline: {exc}", file=sys.stderr)
        raise SystemExit(1)
