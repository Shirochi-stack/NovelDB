"""Extract untranslated description rows from sfacg_descriptions.txt.

Creates: docs/data/sfacg_descriptions_untranslated.txt (only rows needing translation)
Skips:
  - Blank lines, empty IDs, non-numeric IDs
  - Rows where column 3 already has English text
  - Rows where column 2 already contains English (wrong delimiter like || instead of |||)
  - Junk/trivial entries (≤5 meaningful characters)
Also fixes rows with || delimiter: moves English from col2 into col3 in the main file.
"""
import gzip, os, sys, re
sys.stdout.reconfigure(encoding="utf-8")

SRC = os.path.join("docs", "data", "sfacg_descriptions.txt")
OUT = os.path.join("docs", "data", "sfacg_descriptions_untranslated.txt")

# Decompress .gz if only the compressed version exists (CI commits .gz only)
if not os.path.exists(SRC) and os.path.exists(SRC + ".gz"):
    print(f"Decompressing {SRC}.gz -> {SRC}")
    with gzip.open(SRC + ".gz", "rb") as gz_in:
        with open(SRC, "wb") as f_out:
            f_out.write(gz_in.read())

if not os.path.exists(SRC):
    print(f"Error: {SRC} not found."); sys.exit(1)

def is_mostly_english(text):
    """Check if text is predominantly English/ASCII.
    
    Returns False if ANY CJK characters are present — that indicates
    a garbled/partial translation that needs retranslation.
    """
    if not text:
        return False
    alpha = [c for c in text if c.isalpha()]
    if not alpha:
        return False
    # If any CJK chars exist, it's not "already English" — it's garbled
    for c in alpha:
        cp = ord(c)
        if (0x4E00 <= cp <= 0x9FFF or 0x3400 <= cp <= 0x4DBF
            or 0xAC00 <= cp <= 0xD7AF or 0x3040 <= cp <= 0x30FF):
            return False
    ascii_alpha = sum(1 for c in alpha if ord(c) < 128)
    return ascii_alpha / len(alpha) > 0.6

def has_cjk(text):
    return any(
        0x3400 <= ord(c) <= 0x4DBF or
        0x4E00 <= ord(c) <= 0x9FFF or
        0xF900 <= ord(c) <= 0xFAFF
        for c in text
    )

def has_ascii_letter(text):
    return any(("a" <= c.lower() <= "z") for c in text)

def is_valid_english_translation(text):
    text = (text or "").strip()
    return bool(text) and not has_cjk(text) and has_ascii_letter(text)

def clean_field(text):
    return (text or "").replace("|", " ").replace("\r", " ").replace("\n", " ").strip()

def is_trivial(text):
    """Check if text is junk/trivial (very short or no meaningful content)."""
    # Strip whitespace, punctuation, emojis, special chars
    meaningful = re.sub(r'[\s\W\d_]+', '', text, flags=re.UNICODE)
    return len(meaningful) <= 5

# Pass 1: Read all lines, fix double-pipe issues, identify untranslated
lines = []
fixed_count = 0
with open(SRC, "r", encoding="utf-8") as f:
    for line in f:
        stripped = line.rstrip("\r\n")
        if not stripped:
            lines.append(stripped)
            continue
        parts = stripped.split("|||", 2)
        nid = parts[0].strip()
        if not nid or not nid.isdigit():
            lines.append(stripped)
            continue

        col2 = parts[1] if len(parts) >= 2 else ""
        col3 = parts[2].strip() if len(parts) >= 3 else ""

        # Fix: if col3 is empty but col2 contains || with English after it
        if not col3 and "||" in col2:
            sub_parts = col2.split("||", 1)
            if len(sub_parts) == 2 and sub_parts[1].strip():
                potential_en = sub_parts[1].strip()
                if is_mostly_english(potential_en):
                    # Move English to col3
                    new_line = f"{nid}|||{sub_parts[0]}|||{potential_en}"
                    lines.append(new_line)
                    fixed_count += 1
                    continue

        lines.append(stripped)

# Write back fixed main file
if fixed_count > 0:
    with open(SRC, "w", encoding="utf-8") as f:
        for line in lines:
            f.write(line + "\n")
    print(f"Fixed {fixed_count} rows with || delimiter (moved English to col3)")

# Pass 2: Extract genuinely untranslated rows
count = 0
skipped_english = 0
skipped_junk = 0
invalid_existing = 0
with open(SRC, "r", encoding="utf-8") as f, open(OUT, "w", encoding="utf-8") as out:
    for line in f:
        stripped = line.rstrip("\r\n")
        if not stripped: continue
        parts = stripped.split("|||", 2)
        nid = parts[0].strip()
        if not nid or not nid.isdigit(): continue

        col2 = parts[1] if len(parts) >= 2 else ""
        col3 = parts[2].strip() if len(parts) >= 3 else ""
        source = col2
        if has_cjk(col3) and (is_trivial(col2) or not has_cjk(col2)):
            source = col3

        # Already translated in col3. Empty, CJK, or punctuation-only content
        # is not a valid translation and must be re-queued.
        if is_valid_english_translation(col3):
            continue
        if col3:
            invalid_existing += 1

        # Col2 is already in English — no translation needed
        if is_mostly_english(source):
            skipped_english += 1
            continue

        # Trivial/junk content
        if is_trivial(source):
            skipped_junk += 1
            continue

        out.write(f"{nid}|||{clean_field(source)}|||\n")
        count += 1

print(f"Extracted {count} genuinely untranslated rows to {OUT}")
print(f"  Skipped {skipped_english} rows (already English in col2)")
print(f"  Skipped {skipped_junk} rows (trivial/junk content)")
if invalid_existing:
    print(f"  Re-queued {invalid_existing} rows with non-English/CJK column 3")
