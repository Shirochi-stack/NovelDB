"""Translate untranslated titles/descriptions using the Grok API.

Reads an untranslated file (id|||original or id|||original|||), chunks the rows
to stay within the Grok API output token limit (with 30% safety margin), sends
translation requests IN PARALLEL, and writes the translated results back to the
same file.

The script chunks at line boundaries to avoid splitting mid-sentence.
Parallel requests are staggered by a configurable delay (default 5s) to avoid
rate-limit storms.

Environment:
    GROK_API_KEY  — required API key (set via GitHub secret)

Usage:
    python scripts/translate_with_grok.py <input_file> [--lang korean|chinese] [--type titles|descriptions]

Examples:
    python scripts/translate_with_grok.py docs/data/sfacg_titles_untranslated.txt --lang chinese --type titles
    python scripts/translate_with_grok.py docs/data/titles_untranslated.txt --lang korean --type titles
    python scripts/translate_with_grok.py docs/data/descriptions_untranslated.txt --lang korean --type descriptions
"""

import os, sys, json, time, argparse, requests, threading
from concurrent.futures import ThreadPoolExecutor, as_completed

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

# Concurrency settings
MAX_WORKERS = 30
STAGGER_DELAY = 5  # seconds between launching each parallel request
MAX_RETRIES = 5
MAX_RATE_LIMIT_RETRIES = 8  # separate budget for consecutive 429s
REQUEST_TIMEOUT = 600  # 10 minutes for large chunks

# Thread-safe print lock
_print_lock = threading.Lock()

def _tprint(*args, **kwargs):
    """Thread-safe print."""
    with _print_lock:
        print(*args, **kwargs)
        sys.stdout.flush()


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


def call_grok(prompt, api_key, model=DEFAULT_MODEL):
    """Call the Grok API with robust retry logic.

    Rate-limit (429) responses have their own retry budget so they don't
    consume the general retry counter.
    """
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

    rate_limit_hits = 0

    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.post(GROK_API_URL, headers=headers, json=payload, timeout=REQUEST_TIMEOUT)

            if resp.status_code == 429:
                rate_limit_hits += 1
                if rate_limit_hits > MAX_RATE_LIMIT_RETRIES:
                    raise RuntimeError(f"Rate-limited {rate_limit_hits} times, giving up")
                wait = min(120, 2 ** rate_limit_hits * 10)
                _tprint(f"    Rate limited (attempt {attempt + 1}), waiting {wait}s...")
                time.sleep(wait)
                # Don't count rate-limits against the normal retry budget
                attempt = max(0, attempt - 1)
                continue

            if resp.status_code >= 500:
                wait = min(60, 2 ** attempt * 10)
                _tprint(f"    Server error {resp.status_code} on attempt {attempt + 1}, waiting {wait}s...")
                time.sleep(wait)
                continue

            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"].strip()
            usage = data.get("usage", {})
            _tprint(f"    Tokens: {usage.get('prompt_tokens', '?')} in, {usage.get('completion_tokens', '?')} out")
            return content

        except requests.exceptions.Timeout:
            _tprint(f"    Timeout on attempt {attempt + 1}, retrying...")
            time.sleep(10)
        except requests.exceptions.ConnectionError as e:
            wait = min(60, 2 ** attempt * 10)
            _tprint(f"    Connection error on attempt {attempt + 1}: {e}")
            _tprint(f"    Waiting {wait}s before retry...")
            time.sleep(wait)
        except RuntimeError:
            raise
        except Exception as e:
            _tprint(f"    Error on attempt {attempt + 1}: {e}")
            if attempt < MAX_RETRIES - 1:
                time.sleep(10)
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


def process_chunk(chunk_idx, chunk, total_chunks, lang, content_type, api_key, model):
    """Process a single chunk: build prompt, call API, parse response.

    Returns (chunk_idx, translations_dict, len(chunk)).
    """
    _tprint(f"\n  Chunk {chunk_idx + 1}/{total_chunks} ({len(chunk)} lines)...")

    try:
        prompt = build_prompt(chunk, lang, content_type)
        response = call_grok(prompt, api_key, model=model)
        translations = parse_response(response, chunk)

        _tprint(f"  Chunk {chunk_idx + 1}/{total_chunks}: got {len(translations)} translations (expected {len(chunk)})")

        missing = len(chunk) - len(translations)
        if missing > 0:
            _tprint(f"  Chunk {chunk_idx + 1}/{total_chunks}: WARNING — {missing} lines not translated")

        return chunk_idx, translations, len(chunk)

    except Exception as e:
        _tprint(f"  Chunk {chunk_idx + 1}/{total_chunks}: FAILED — {e}")
        return chunk_idx, {}, len(chunk)


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
    parser.add_argument("--workers", type=int, default=MAX_WORKERS,
                        help=f"Max parallel workers (default: {MAX_WORKERS})")
    parser.add_argument("--delay", type=float, default=STAGGER_DELAY,
                        help=f"Seconds between launching each request (default: {STAGGER_DELAY})")
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
    max_lines = MAX_OUTPUT_CHARS // AVG_CHARS_PER_LINE.get(args.content_type, 100)
    print(f"Split into {len(chunks)} chunks (max ~{max_lines} lines per chunk)")
    print(f"Parallel mode: {args.workers} workers, {args.delay}s stagger delay")

    # ── Translate chunks in parallel ──
    all_translations = {}
    total_expected = 0
    failed_chunks = []

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {}
        for i, chunk in enumerate(chunks):
            if i > 0:
                time.sleep(args.delay)  # stagger each submission
            future = pool.submit(
                process_chunk, i, chunk, len(chunks),
                args.lang, args.content_type, api_key, args.model,
            )
            futures[future] = i

        for future in as_completed(futures):
            chunk_idx, translations, chunk_size = future.result()
            all_translations.update(translations)
            total_expected += chunk_size
            if not translations:
                failed_chunks.append(chunk_idx + 1)

    print(f"\nTotal translations: {len(all_translations)} / {total_expected}")
    if failed_chunks:
        print(f"Failed chunks: {sorted(failed_chunks)}")

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
