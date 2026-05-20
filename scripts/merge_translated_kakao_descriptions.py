"""Merge translated KakaoPage descriptions back into kakao_descriptions.txt.

Accepts translated rows in kakao_descriptions_untranslated.txt and writes the
remaining untranslated rows back to the same patch file.
"""

import os, sys

sys.stdout.reconfigure(encoding="utf-8")

MAIN = os.path.join("docs", "data", "kakao_descriptions.txt")
PATCH = os.path.join("docs", "data", "kakao_descriptions_untranslated.txt")
OUT = PATCH


def has_cjk(text):
    """Return True when text still contains Korean, Chinese, or Japanese chars."""
    return any(
        "\u3040" <= c <= "\u30ff" or
        "\u3400" <= c <= "\u4dbf" or
        "\u4e00" <= c <= "\u9fff" or
        "\uf900" <= c <= "\ufaff" or
        "\uac00" <= c <= "\ud7af"
        for c in text
    )


if not os.path.exists(MAIN):
    print(f"Error: {MAIN} not found.")
    sys.exit(1)
if not os.path.exists(PATCH):
    print(f"Error: {PATCH} not found.")
    sys.exit(1)


def is_latin(text):
    if not text:
        return False
    return sum(1 for c in text if c.isascii() or ord(c) < 0x3000) / len(text) > 0.7


new_translations = {}
skipped = 0
for line in open(PATCH, "r", encoding="utf-8"):
    stripped = line.rstrip("\r\n")
    if not stripped:
        continue
    parts = stripped.split("|||")
    nid = parts[0].strip()
    if not nid or not nid.isdigit():
        skipped += 1
        continue
    if len(parts) >= 3 and parts[2].strip() and not has_cjk(parts[2].strip()):
        new_translations[nid] = parts[2].strip()
    elif len(parts) >= 2 and parts[1].strip() and is_latin(parts[1].strip()) and not has_cjk(parts[1].strip()):
        new_translations[nid] = parts[1].strip()

print(f"  Found {len(new_translations)} new translations in {PATCH}")
if skipped:
    print(f"  Skipped {skipped} invalid rows (non-numeric ID)")

entries = {}
merged = 0
cleaned = 0
for line in open(MAIN, "r", encoding="utf-8"):
    stripped = line.rstrip("\r\n")
    if not stripped:
        continue
    parts = stripped.split("|||")
    nid = parts[0].strip()
    if not nid or not nid.isdigit() or len(parts) < 3:
        cleaned += 1
        continue

    korean = parts[1] if len(parts) >= 2 else ""
    en = parts[2].strip() if len(parts) >= 3 else ""
    if en and has_cjk(en):
        en = ""
    if not korean.strip() and not en:
        cleaned += 1
        continue

    if not en and nid in new_translations:
        en = new_translations[nid]
        merged += 1

    if nid in entries:
        if en and not entries[nid][1]:
            entries[nid] = (korean, en)
    else:
        entries[nid] = (korean, en)

translated = []
untranslated = []
for nid, (korean, en) in entries.items():
    row = f"{nid}|||{korean}|||{en}\n"
    if en:
        translated.append(row)
    else:
        untranslated.append(row)

with open(MAIN, "w", encoding="utf-8") as f:
    f.writelines(translated)
    f.writelines(untranslated)

print(f"  Merged {merged} new translations into {MAIN}")
if cleaned:
    print(f"  Cleaned {cleaned} invalid rows from {MAIN}")
print(f"  Translated: {len(translated)}, Untranslated: {len(untranslated)}")

with open(OUT, "w", encoding="utf-8") as f:
    f.writelines(untranslated)
print(f"  Updated {OUT} ({len(untranslated)} rows)")
