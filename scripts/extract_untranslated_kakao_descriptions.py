"""Extract untranslated description rows from kakao_descriptions.txt.

Creates: docs/data/kakao_descriptions_untranslated.txt
"""

import gzip, os, re, sys

sys.stdout.reconfigure(encoding="utf-8")

SRC = os.path.join("docs", "data", "kakao_descriptions.txt")
OUT = os.path.join("docs", "data", "kakao_descriptions_untranslated.txt")


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


if not os.path.exists(SRC) and os.path.exists(SRC + ".gz"):
    print(f"Decompressing {SRC}.gz -> {SRC}")
    with gzip.open(SRC + ".gz", "rb") as gz_in:
        with open(SRC, "wb") as f_out:
            f_out.write(gz_in.read())

if not os.path.exists(SRC):
    print(f"Error: {SRC} not found.")
    sys.exit(1)


def is_mostly_english(text):
    if not text:
        return False
    if has_cjk(text):
        return False
    alpha = [c for c in text if c.isalpha()]
    if not alpha:
        return False
    for c in alpha:
        cp = ord(c)
        if 0xAC00 <= cp <= 0xD7AF or 0x4E00 <= cp <= 0x9FFF:
            return False
    ascii_alpha = sum(1 for c in alpha if ord(c) < 128)
    return ascii_alpha / len(alpha) > 0.6


def is_trivial(text):
    meaningful = re.sub(r"[\s\W\d_]+", "", text, flags=re.UNICODE)
    return len(meaningful) <= 5


lines = []
fixed_count = 0
with open(SRC, "r", encoding="utf-8") as f:
    for line in f:
        stripped = line.rstrip("\r\n")
        if not stripped:
            lines.append(stripped)
            continue
        parts = stripped.split("|||")
        nid = parts[0].strip()
        if not nid or not nid.isdigit():
            lines.append(stripped)
            continue

        col2 = parts[1] if len(parts) >= 2 else ""
        col3 = parts[2].strip() if len(parts) >= 3 else ""

        if (not col3 or has_cjk(col3)) and "||" in col2:
            sub_parts = col2.split("||", 1)
            if len(sub_parts) == 2 and is_mostly_english(sub_parts[1].strip()):
                lines.append(f"{nid}|||{sub_parts[0]}|||{sub_parts[1].strip()}")
                fixed_count += 1
                continue

        lines.append(stripped)

if fixed_count:
    with open(SRC, "w", encoding="utf-8") as f:
        for line in lines:
            f.write(line + "\n")
    print(f"Fixed {fixed_count} rows with || delimiter")

count = 0
skipped_english = 0
skipped_junk = 0
with open(SRC, "r", encoding="utf-8") as f, open(OUT, "w", encoding="utf-8") as out:
    for line in f:
        stripped = line.rstrip("\r\n")
        if not stripped:
            continue
        parts = stripped.split("|||")
        nid = parts[0].strip()
        if not nid or not nid.isdigit():
            continue

        col2 = parts[1] if len(parts) >= 2 else ""
        col3 = parts[2].strip() if len(parts) >= 3 else ""
        if col3 and not has_cjk(col3):
            continue
        if is_mostly_english(col2):
            skipped_english += 1
            continue
        if is_trivial(col2):
            skipped_junk += 1
            continue

        out.write(f"{nid}|||{col2}|||\n")
        count += 1

print(f"Extracted {count} genuinely untranslated rows to {OUT}")
print(f"  Skipped {skipped_english} rows (already English in col2)")
print(f"  Skipped {skipped_junk} rows (trivial/junk content)")
