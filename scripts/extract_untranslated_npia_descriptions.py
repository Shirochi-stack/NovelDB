"""Extract untranslated description rows from descriptions.txt for Novelpia.

Creates: docs/data/descriptions_untranslated.txt (only rows missing column 3)
Skips blank lines, empty IDs, and non-numeric IDs.
"""
import os, sys, tempfile, time
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
SRC_REL = os.path.join("docs", "data", "descriptions.txt")
OUT_REL = os.path.join("docs", "data", "descriptions_untranslated.txt")
SRC = ROOT / SRC_REL
OUT = ROOT / OUT_REL
DELIM = "|||"

if not SRC.exists():
    print(f"Error: {SRC_REL} not found."); sys.exit(1)


def parse_row(line):
    if DELIM not in line:
        return line.strip(), "", ""
    nid, rest = line.split(DELIM, 1)
    if DELIM not in rest:
        return nid.strip(), rest, ""
    raw, en = rest.rsplit(DELIM, 1)
    return nid.strip(), raw, en


def replace_or_rewrite(tmp_path, path):
    deadline = time.time() + 30
    while True:
        try:
            os.replace(tmp_path, path)
            return
        except PermissionError:
            if time.time() >= deadline:
                break
            time.sleep(1)

    with path.open("r+", encoding="utf-8", newline="") as f:
        f.seek(0)
        with tmp_path.open("r", encoding="utf-8") as tmp:
            for chunk in iter(lambda: tmp.read(1024 * 1024), ""):
                f.write(chunk)
        f.truncate()
        f.flush()
        os.fsync(f.fileno())
    tmp_path.unlink(missing_ok=True)


count = 0
tmp_path = None
try:
    with SRC.open("r", encoding="utf-8") as f, tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        newline="",
        delete=False,
        dir=OUT.parent,
        prefix=f".{OUT.name}.",
        suffix=".tmp",
    ) as out:
        tmp_path = Path(out.name)
        for line in f:
            stripped = line.rstrip("\r\n")
            if not stripped: continue
            nid, _raw, en = parse_row(stripped)
            if not nid or not nid.isdigit(): continue
            if not en.strip():
                out.write(stripped + "\n")
                count += 1
        out.flush()
        os.fsync(out.fileno())
    replace_or_rewrite(tmp_path, OUT)
except Exception:
    if tmp_path:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
    raise

print(f"Extracted {count} untranslated rows to {OUT_REL}")
