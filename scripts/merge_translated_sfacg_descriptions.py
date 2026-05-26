"""Merge translated SFACG descriptions back into sfacg_descriptions.txt.

Deduplicates by ID, validates IDs are numeric, prefers translated entries.
Handles both 2-column (id|||english) and 3-column (id|||chinese|||english) formats.
Auto-extracts remaining untranslated after merge.
"""
import os, sys
sys.stdout.reconfigure(encoding="utf-8")

MAIN = os.path.join("docs", "data", "sfacg_descriptions.txt")
PATCH = os.path.join("docs", "data", "sfacg_descriptions_untranslated.txt")
OUT = PATCH

if not os.path.exists(MAIN):
    print(f"Error: {MAIN} not found."); sys.exit(1)
if not os.path.exists(PATCH):
    print(f"Error: {PATCH} not found."); sys.exit(1)

def has_cjk(text):
    return any(
        "\u3400" <= c <= "\u4dbf" or
        "\u4e00" <= c <= "\u9fff" or
        "\uf900" <= c <= "\ufaff"
        for c in text
    )

def has_ascii_letter(text):
    return any(("a" <= c.lower() <= "z") for c in text)

def is_valid_english(text):
    text = (text or "").strip()
    return bool(text) and not has_cjk(text) and has_ascii_letter(text)

# Read new translations from patch file
new_translations = {}
skipped = 0
for line in open(PATCH, "r", encoding="utf-8"):
    stripped = line.rstrip("\r\n")
    if not stripped: continue
    parts = stripped.split("|||", 2)
    nid = parts[0].strip()
    if not nid or not nid.isdigit():
        skipped += 1; continue
    if len(parts) >= 3 and is_valid_english(parts[2]):
        new_translations[nid] = parts[2].strip()
    elif len(parts) >= 2 and is_valid_english(parts[1]):
        new_translations[nid] = parts[1].strip()

print(f"  Found {len(new_translations)} new translations in {PATCH}")
if skipped: print(f"  Skipped {skipped} invalid rows (non-numeric ID)")

# Read main file, deduplicate by ID, apply translations
entries = {}
merged = 0
cleaned = 0
for line in open(MAIN, "r", encoding="utf-8"):
    stripped = line.rstrip("\r\n")
    if not stripped: continue
    parts = stripped.split("|||", 2)
    nid = parts[0].strip()
    if not nid or not nid.isdigit():
        cleaned += 1; continue
    if len(parts) < 3:
        cleaned += 1; continue
    if not parts[1].strip() and (len(parts) < 3 or not parts[2].strip()):
        cleaned += 1; continue
    en = parts[2].strip() if len(parts) >= 3 else ""
    chinese = parts[1] if len(parts) >= 2 else ""

    if not is_valid_english(en) and nid in new_translations:
        en = new_translations[nid]
        merged += 1
    elif not is_valid_english(en):
        en = ""

    if nid in entries:
        if en and not entries[nid][1]:
            entries[nid] = (chinese, en)
    else:
        entries[nid] = (chinese, en)

translated = []
untranslated = []
for nid, (chinese, en) in entries.items():
    row = f"{nid}|||{chinese}|||{en}\n"
    if is_valid_english(en):
        translated.append(row)
    else:
        untranslated.append(row)

with open(MAIN, "w", encoding="utf-8") as f:
    f.writelines(translated)
    f.writelines(untranslated)

print(f"  Merged {merged} new translations into {MAIN}")
if cleaned: print(f"  Cleaned {cleaned} invalid rows from {MAIN}")
print(f"  Translated: {len(translated)}, Untranslated: {len(untranslated)}")

with open(OUT, "w", encoding="utf-8") as f:
    f.writelines(untranslated)
print(f"  Updated {OUT} ({len(untranslated)} rows)")
