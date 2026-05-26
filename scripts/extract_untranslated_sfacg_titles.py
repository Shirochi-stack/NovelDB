"""Extract untranslated title rows from sfacg_titles_en.txt for SFACG.

Skips blank lines, empty IDs, and non-numeric IDs.
"""
import os, sys
sys.stdout.reconfigure(encoding="utf-8")

SRC = os.path.join("docs", "data", "sfacg_titles_en.txt")
OUT = os.path.join("docs", "data", "sfacg_titles_untranslated.txt")

if not os.path.exists(SRC):
    print(f"Error: {SRC} not found."); sys.exit(1)

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

def clean_field(text):
    return (text or "").replace("|", " ").replace("\r", " ").replace("\n", " ").strip()

count = 0
invalid_existing = 0
with open(SRC, "r", encoding="utf-8") as f, open(OUT, "w", encoding="utf-8") as out:
    for line in f:
        stripped = line.rstrip("\r\n")
        if not stripped: continue
        parts = stripped.split("|||", 2)
        nid = parts[0].strip()
        if not nid or not nid.isdigit(): continue
        title = parts[1] if len(parts) >= 2 else ""
        en = parts[2].strip() if len(parts) >= 3 else ""
        if not is_valid_english(en):
            if en:
                invalid_existing += 1
            out.write(f"{nid}|||{clean_field(title)}|||\n")
            count += 1

print(f"Extracted {count} untranslated rows to {OUT}")
if invalid_existing:
    print(f"  Re-queued {invalid_existing} rows with non-English/CJK column 3")
