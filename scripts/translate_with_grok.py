"""Translate untranslated titles/descriptions using the Grok API.

Reads an untranslated file (id|||original or id|||original|||), chunks the rows
to stay within the Grok API output token limit (with 30% safety margin), sends
translation requests, and writes the translated results back to the same file.

The script chunks at line boundaries to avoid splitting mid-sentence.

Environment:
    GROK_API_KEY  — required API key (set via GitHub secret)

Usage:
    python scripts/translate_with_grok.py <input_file> [--lang korean|chinese] [--type titles|descriptions]

Examples:
    python scripts/translate_with_grok.py docs/data/sfacg_titles_untranslated.txt --lang chinese --type titles
    python scripts/translate_with_grok.py docs/data/titles_untranslated.txt --lang korean --type titles
    python scripts/translate_with_grok.py docs/data/descriptions_untranslated.txt --lang korean --type descriptions
"""

import os, sys, json, time, argparse, requests

sys.stdout.reconfigure(encoding="utf-8")

GROK_API_URL = "https://api.x.ai/v1/chat/completions"
DEFAULT_MODEL = "grok-4.20-beta-0309-reasoning"
MAX_OUTPUT_TOKENS = 131072  # 128k
SAFETY_MARGIN = 0.30  # 30% margin
EFFECTIVE_OUTPUT_TOKENS = int(MAX_OUTPUT_TOKENS * (1 - SAFETY_MARGIN))

# Rough estimate: 1 token ≈ 3.5 chars for mixed CJK/English output
CHARS_PER_TOKEN = 3.5
MAX_OUTPUT_CHARS = int(EFFECTIVE_OUTPUT_TOKENS * CHARS_PER_TOKEN)

# Each output line is roughly: id|||original|||translated
# Average translated title ~ 50 chars, so ~80 chars per output line
# Average translated description ~ 200 chars, so ~250 chars per output line
AVG_CHARS_PER_LINE = {"titles": 80, "descriptions": 250}


def build_prompt(rows, lang, content_type):
    """Build the translation prompt for a batch of rows."""
    lang_name = {"korean": "Korean", "chinese": "Chinese"}.get(lang, lang.title())

    if content_type == "descriptions":
        task_desc = f"""Translate each {lang_name} novel synopsis/description to natural English.
Keep the same line format: id|||original|||English translation
Preserve \\n markers in descriptions as-is (they represent line breaks).
Do NOT skip any lines. Translate EVERY line."""
    else:
        task_desc = f"""Translate each {lang_name} novel title to natural English.
Keep the same line format: id|||original|||English translation
Do NOT skip any lines. Translate EVERY line."""

    lines_text = "\n".join(rows)

    return f"""{task_desc}

IMPORTANT RULES:
- Output ONLY the translated lines, nothing else. No commentary, no headers.
- Keep the exact same id and original text, only add the English translation after the third |||
- If a line already has a translation in column 3, keep it as-is.
- Maintain the exact ||| delimiter format.

Input ({len(rows)} lines):
{lines_text}"""


def call_grok(prompt, api_key, model=DEFAULT_MODEL, max_retries=3):
    """Call the Grok API with retry logic."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are a professional translator. You translate novel titles and descriptions accurately and naturally. Output ONLY the translated lines in the exact format requested."},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": MAX_OUTPUT_TOKENS,
        "temperature": 0.3,
    }

    for attempt in range(max_retries):
        try:
            resp = requests.post(GROK_API_URL, headers=headers, json=payload, timeout=300)
            if resp.status_code == 429:
                wait = min(60, 2 ** attempt * 10)
                print(f"  Rate limited, waiting {wait}s...")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"].strip()
            usage = data.get("usage", {})
            print(f"  Tokens used: {usage.get('prompt_tokens', '?')} in, {usage.get('completion_tokens', '?')} out")
            return content
        except requests.exceptions.Timeout:
            print(f"  Timeout on attempt {attempt + 1}, retrying...")
            time.sleep(5)
        except Exception as e:
            print(f"  Error on attempt {attempt + 1}: {e}")
            if attempt < max_retries - 1:
                time.sleep(5)
            else:
                raise
    raise RuntimeError("All retries exhausted")


def chunk_rows(rows, content_type):
    """Split rows into chunks that fit within the output token limit."""
    avg_chars = AVG_CHARS_PER_LINE.get(content_type, 100)
    max_lines_per_chunk = max(1, MAX_OUTPUT_CHARS // avg_chars)

    chunks = []
    for i in range(0, len(rows), max_lines_per_chunk):
        chunks.append(rows[i:i + max_lines_per_chunk])

    return chunks


def parse_response(response_text, original_rows):
    """Parse the API response and extract translations.

    Returns a dict of {id: english_translation}.
    """
    translations = {}
    original_ids = {row.split("|||")[0].strip() for row in original_rows}

    for line in response_text.split("\n"):
        line = line.strip()
        if not line or "|||" not in line:
            continue
        parts = line.split("|||")
        nid = parts[0].strip()
        if not nid or nid not in original_ids:
            continue
        if len(parts) >= 3 and parts[2].strip():
            translations[nid] = parts[2].strip()

    return translations


def main():
    parser = argparse.ArgumentParser(description="Translate with Grok API")
    parser.add_argument("input_file", help="Path to untranslated file")
    parser.add_argument("--lang", default="korean", choices=["korean", "chinese"],
                        help="Source language (default: korean)")
    parser.add_argument("--type", dest="content_type", default="titles",
                        choices=["titles", "descriptions"],
                        help="Content type (default: titles)")
    parser.add_argument("--model", default=DEFAULT_MODEL,
                        help=f"Grok model to use (default: {DEFAULT_MODEL})")
    args = parser.parse_args()

    api_key = os.environ.get("GROK_API_KEY")
    if not api_key:
        print("Error: GROK_API_KEY environment variable not set")
        sys.exit(1)

    if not os.path.exists(args.input_file):
        print(f"Error: {args.input_file} not found")
        sys.exit(1)

    # Read untranslated rows
    rows = []
    with open(args.input_file, "r", encoding="utf-8") as f:
        for line in f:
            stripped = line.rstrip("\r\n")
            if not stripped:
                continue
            parts = stripped.split("|||")
            nid = parts[0].strip()
            if not nid or not nid.isdigit():
                continue
            # Skip already translated
            if len(parts) >= 3 and parts[2].strip():
                continue
            rows.append(stripped)

    if not rows:
        print("No untranslated rows found. Nothing to do.")
        return

    print(f"Found {len(rows)} untranslated rows in {args.input_file}")

    # Chunk the rows
    chunks = chunk_rows(rows, args.content_type)
    print(f"Split into {len(chunks)} chunks (max ~{MAX_OUTPUT_CHARS // AVG_CHARS_PER_LINE.get(args.content_type, 100)} lines per chunk)")

    # Translate each chunk
    all_translations = {}
    for i, chunk in enumerate(chunks):
        print(f"\nChunk {i + 1}/{len(chunks)} ({len(chunk)} lines)...")
        prompt = build_prompt(chunk, args.lang, args.content_type)
        response = call_grok(prompt, api_key, model=args.model)
        translations = parse_response(response, chunk)
        all_translations.update(translations)
        print(f"  Got {len(translations)} translations (expected {len(chunk)})")

        missing = len(chunk) - len(translations)
        if missing > 0:
            print(f"  WARNING: {missing} lines were not translated")

        # Brief pause between API calls
        if i < len(chunks) - 1:
            time.sleep(2)

    print(f"\nTotal translations: {len(all_translations)} / {len(rows)}")

    # Write results back to the input file with translations filled in
    output_lines = []
    with open(args.input_file, "r", encoding="utf-8") as f:
        for line in f:
            stripped = line.rstrip("\r\n")
            if not stripped:
                output_lines.append(stripped + "\n")
                continue
            parts = stripped.split("|||")
            nid = parts[0].strip()
            # Already translated? Keep as-is
            if len(parts) >= 3 and parts[2].strip():
                output_lines.append(stripped + "\n")
                continue
            # Has new translation?
            if nid in all_translations:
                original = parts[1] if len(parts) >= 2 else ""
                output_lines.append(f"{nid}|||{original}|||{all_translations[nid]}\n")
            else:
                output_lines.append(stripped + "\n")

    with open(args.input_file, "w", encoding="utf-8") as f:
        f.writelines(output_lines)

    print(f"Updated {args.input_file}")


if __name__ == "__main__":
    main()
