"""Merge translated KakaoPage titles back into kakao_titles_en.txt.

Reads: docs/data/kakao_titles_untranslated.txt (with column 3 now filled in)
Updates: docs/data/kakao_titles_en.txt (preserves existing, adds new translations)

Translated rows are sorted to the top, untranslated remain at the bottom.
"""
import os, sys
sys.stdout.reconfigure(encoding="utf-8")

MAIN = os.path.join("docs", "data", "kakao_titles_en.txt")
PATCH = os.path.join("docs", "data", "kakao_titles_untranslated.txt")

if not os.path.exists(MAIN):
    print(f"Error: {MAIN} not found."); sys.exit(1)
if not os.path.exists(PATCH):
    print(f"Error: {PATCH} not found."); sys.exit(1)

# Read new translations from the patch file
new_translations = {}
for line in open(PATCH, "r", encoding="utf-8"):
    parts = line.rstrip("\n").rstrip("\r").split("|||")
    if len(parts) >= 3 and parts[2].strip():
        new_translations[parts[0].strip()] = parts[2].strip()

print(f"  Found {len(new_translations)} new translations in {PATCH}")

# Read main file and apply translations
translated = []
untranslated = []
merged = 0
for line in open(MAIN, "r", encoding="utf-8"):
    parts = line.rstrip("\n").rstrip("\r").split("|||")
    nid = parts[0].strip()
    existing_en = parts[2].strip() if len(parts) >= 3 else ""
    title = parts[1] if len(parts) >= 2 else ""

    if not existing_en and nid in new_translations:
        existing_en = new_translations[nid]
        merged += 1

    row = f"{nid}|||{title}|||{existing_en}\n"
    if existing_en:
        translated.append(row)
    else:
        untranslated.append(row)

with open(MAIN, "w", encoding="utf-8") as f:
    f.writelines(translated)
    f.writelines(untranslated)

print(f"  Merged {merged} new translations into {MAIN}")
print(f"  Translated: {len(translated)}, Untranslated: {len(untranslated)}")
