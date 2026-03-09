"""Extract untranslated description rows from descriptions.txt for Novelpia.

Creates: docs/data/descriptions_untranslated.txt (only rows missing column 3)
"""
import os, sys
sys.stdout.reconfigure(encoding="utf-8")

SRC = os.path.join("docs", "data", "descriptions.txt")
OUT = os.path.join("docs", "data", "descriptions_untranslated.txt")

if not os.path.exists(SRC):
    print(f"Error: {SRC} not found."); sys.exit(1)

count = 0
with open(SRC, "r", encoding="utf-8") as f, open(OUT, "w", encoding="utf-8") as out:
    for line in f:
        parts = line.rstrip("\n").rstrip("\r").split("|||")
        if len(parts) < 3 or not parts[2].strip():
            out.write(line)
            count += 1

print(f"Extracted {count} untranslated rows to {OUT}")
