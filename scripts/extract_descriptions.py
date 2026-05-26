"""Extract novel descriptions/synopses from novels_full.json.

Produces a single descriptions.txt file. If an existing descriptions.txt
is found, preserves any English translations (column 3) already present.

Usage:
    python scripts/extract_descriptions.py

Output:
    docs/data/descriptions.txt

Format: novel_id|||korean_synopsis|||english_translation
  - Column 3 (english) may be empty if not yet translated.
  - The site prefers column 3 when present, falls back to column 2.
  - Newlines in synopsis are replaced with literal \\n.
"""

import json, os, re, sys, tempfile, time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
FULL_DATA_REL = os.path.join("docs", "data", "novels_full.json")
OUTPUT_REL = os.path.join("docs", "data", "descriptions.txt")
FULL_DATA = ROOT / FULL_DATA_REL
OUTPUT = ROOT / OUTPUT_REL
DELIM = "|||"
SAFE_DELIM = "｜｜｜"


def sanitize_field(text):
    text = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    text = text.replace(DELIM, SAFE_DELIM)
    return text.replace("\n", "\\n")


def parse_row(line):
    if DELIM not in line:
        return line.strip(), "", ""
    nid, rest = line.split(DELIM, 1)
    if DELIM not in rest:
        return nid.strip(), rest, ""
    raw, en = rest.rsplit(DELIM, 1)
    return nid.strip(), raw, en


def atomic_write_lines(path, lines):
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            newline="",
            delete=False,
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
        ) as f:
            tmp_path = Path(f.name)
            f.writelines(lines)
            f.flush()
            os.fsync(f.fileno())
        deadline = time.time() + 30
        while True:
            try:
                os.replace(tmp_path, path)
                return
            except PermissionError:
                if time.time() >= deadline:
                    break
                time.sleep(1)

        # Some Windows readers allow writing but block delete/replace.
        # Fall back to rewriting the existing file in place after the safe
        # temp-file path has failed repeatedly.
        with path.open("r+", encoding="utf-8", newline="") as f:
            f.seek(0)
            with tmp_path.open("r", encoding="utf-8") as tmp:
                for chunk in iter(lambda: tmp.read(1024 * 1024), ""):
                    f.write(chunk)
            f.truncate()
            f.flush()
            os.fsync(f.fileno())
        tmp_path.unlink(missing_ok=True)
    except Exception:
        if tmp_path:
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass
        raise


def load_existing_rows():
    """Load existing data from descriptions.txt if it exists.

    Returns:
        translations: dict of {nid: english_text} for preserving translations
        full_rows: dict of {nid: "nid|||korean|||english"} for preserving
                   entries not found in the new novels_full.json
    """
    translations = {}
    full_rows = {}
    if OUTPUT.exists():
        with OUTPUT.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.rstrip("\r\n")
                if not line: continue
                nid, raw, en = parse_row(line)
                if not nid: continue
                raw = sanitize_field(raw)
                en = sanitize_field(en)
                full_rows[nid] = f"{nid}{DELIM}{raw}{DELIM}{en}"
                if en:
                    translations[nid] = en
        if translations:
            print(f"  Preserved {len(translations)} existing English translations")
    return translations, full_rows


def main():
    if not FULL_DATA.exists():
        print(f"Warning: {FULL_DATA_REL} not found, preserving existing descriptions.txt")
        return

    print(f"Loading {FULL_DATA_REL}...")
    with FULL_DATA.open("r", encoding="utf-8") as f:
        data = json.load(f)
    print(f"  {len(data)} novels loaded.")

    # Preserve existing translations and full rows
    existing_en, existing_rows = load_existing_rows()

    seen_ids = set()
    count = 0
    translated = []
    untranslated = []
    for entry in data:
        nid = entry.get("id")
        synopsis = entry.get("synopsis", "")
        if nid is None:
            continue
        if synopsis:
            seen_ids.add(str(nid))
            # Normalize newlines, then collapse multiple into one
            normed = synopsis.replace("\r\n", "\n").replace("\r", "\n")
            normed = re.sub(r"\n{2,}", "\n", normed).strip()
            flat = sanitize_field(normed)
            en = existing_en.get(str(nid), "")
            row = f"{nid}{DELIM}{flat}{DELIM}{en}\n"
            if en:
                translated.append(row)
            else:
                untranslated.append(row)
            count += 1

    # Preserve rows from existing descriptions.txt that weren't in novels_full.json
    # (e.g. R19 novels that can't be scraped without auth)
    preserved = 0
    for nid, row in existing_rows.items():
        if nid not in seen_ids:
            _, _, en = parse_row(row)
            en = sanitize_field(en)
            if en:
                translated.append(row + "\n")
            else:
                untranslated.append(row + "\n")
            preserved += 1
            count += 1

    atomic_write_lines(OUTPUT, translated + untranslated)

    size_kb = os.path.getsize(OUTPUT) / 1024
    print(f"  Wrote {count} descriptions to {OUTPUT_REL} ({size_kb:.0f} KB)")
    print(f"  Translated: {len(translated)}, Untranslated: {len(untranslated)}")
    if preserved:
        print(f"  Preserved {preserved} entries not in {FULL_DATA_REL} (R19/missing novels)")


if __name__ == "__main__":
    main()
