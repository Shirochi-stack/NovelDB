"""Extract untranslated title rows from titles_en.txt for Novelpia.

Skips blank lines, empty IDs, and non-numeric IDs.
"""
import os, sys
sys.stdout.reconfigure(encoding="utf-8")

SRC = os.path.join("docs", "data", "titles_en.txt")
OUT = os.path.join("docs", "data", "titles_untranslated.txt")

if not os.path.exists(SRC):
    print(f"Error: {SRC} not found."); sys.exit(1)

count = 0
with open(SRC, "r", encoding="utf-8") as f, open(OUT, "w", encoding="utf-8") as out:
    for line in f:
        stripped = line.rstrip("\r\n")
        if not stripped: continue
        parts = stripped.split("|||")
        nid = parts[0].strip()
        if not nid or not nid.isdigit(): continue
        if len(parts) < 3 or not parts[2].strip():
            out.write(stripped + "\n")
            count += 1

print(f"Extracted {count} untranslated rows to {OUT}")
