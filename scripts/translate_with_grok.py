"""Translate untranslated titles/descriptions using the Unified API Client.

Uses the UnifiedClient from unified_api_client.py for reliable API communication
with built-in retry logic, rate limiting, and multi-provider support.

Reads an untranslated file (id|||original or id|||original|||), chunks the rows
to stay within the API token limits (using CJK-aware token estimation),
sends translation requests IN PARALLEL, and writes the translated results back
to the same file.

Environment:
    GROK_API_KEY  — required API key (set via GitHub secret)
    MODEL         — override model name (optional, default: grok-4-1-fast-reasoning)

Usage:
    python scripts/translate_with_grok.py <input_file> [--lang korean|chinese] [--type titles|descriptions]

Examples:
    python scripts/translate_with_grok.py docs/data/sfacg_titles_untranslated.txt --lang chinese --type titles
    python scripts/translate_with_grok.py docs/data/titles_untranslated.txt --lang korean --type titles
    python scripts/translate_with_grok.py docs/data/descriptions_untranslated.txt --lang korean --type descriptions
"""

import os, sys, json, time, argparse, threading
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.stdout.reconfigure(encoding="utf-8")

# Add scripts dir to path so we can import unified_api_client
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

# Suppress debug payload saving in CI
os.environ.setdefault("DEBUG_SAVE_REQUEST_PAYLOADS", "0")
# Suppress HTTP logging noise in CI
os.environ.setdefault("GRACEFUL_STOP_HTTP_SUPPRESS", "1")

from unified_api_client import UnifiedClient

DEFAULT_MODEL = "grok-4-1-fast-reasoning"

# ── Token budget for chunking ──
# Grok models have a 2M token context window.
# We use conservative fixed budgets for reliable chunking.
MAX_INPUT_TOKENS = 200_000   # plenty of room for large prompts
EFFECTIVE_OUTPUT_TOKENS = 60_000  # effective output after reasoning overhead

# Hard cap on lines per chunk
MAX_LINES_PER_CHUNK = {"titles": 1500, "descriptions": 400}

# Concurrency
MAX_WORKERS = 67
STAGGER_DELAY = 5

# Thread-safe print lock
_print_lock = threading.Lock()

def _tprint(*args, **kwargs):
    """Thread-safe print."""
    with _print_lock:
        print(*args, **kwargs)
        sys.stdout.flush()


# ── Token counting (tiktoken) ──

import tiktoken
_enc = tiktoken.get_encoding("cl100k_base")

def estimate_tokens(text):
    """Count tokens using tiktoken (cl100k_base, GPT-4/Grok compatible)."""
    return len(_enc.encode(text))


def estimate_output_tokens_per_line(rows, content_type):
    """Estimate output tokens per translated line."""
    if not rows:
        return 50
    sample = rows[:100]
    total_tokens = 0
    for row in sample:
        parts = row.split("|||")
        original = parts[1].strip() if len(parts) >= 2 else ""
        orig_tokens = estimate_tokens(original)
        if content_type == "descriptions":
            translation_tokens = int(orig_tokens * 0.8)
        else:
            translation_tokens = int(orig_tokens * 1.5)
        total_tokens += 5 + orig_tokens + translation_tokens
    return max(10, total_tokens // len(sample))


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


def chunk_rows(rows, content_type):
    """Split rows into chunks using token estimation."""
    max_lines = MAX_LINES_PER_CHUNK.get(content_type, 500)
    avg_output_per_line = estimate_output_tokens_per_line(rows, content_type)

    chunks = []
    current_chunk = []
    current_input_tokens = 0
    current_output_tokens = 0

    for row in rows:
        row_input_tokens = estimate_tokens(row + "\n")
        row_output_tokens = avg_output_per_line

        would_exceed = (
            len(current_chunk) >= max_lines
            or current_input_tokens + row_input_tokens > MAX_INPUT_TOKENS
            or current_output_tokens + row_output_tokens > EFFECTIVE_OUTPUT_TOKENS
        )

        if current_chunk and would_exceed:
            chunks.append(current_chunk)
            current_chunk = []
            current_input_tokens = 0
            current_output_tokens = 0

        current_chunk.append(row)
        current_input_tokens += row_input_tokens
        current_output_tokens += row_output_tokens

    if current_chunk:
        chunks.append(current_chunk)

    return chunks


def parse_response(response_text, original_rows):
    """Parse the API response and extract translations."""
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
    """Process a single chunk using UnifiedClient."""
    _tprint(f"\n  Chunk {chunk_idx + 1}/{total_chunks} ({len(chunk)} lines)...")

    try:
        # Create a per-thread client instance for thread safety
        client = UnifiedClient(api_key=api_key, model=model, output_dir="/tmp/translate_output")

        # Disable internal retry — accept whatever the model returns on first try
        client._disable_internal_retry = True
        client.request_timeout = 300  # 5 min max per chunk

        prompt = build_prompt(chunk, lang, content_type)

        messages = [
            {"role": "system", "content": "You are a professional translator. You translate novel titles and descriptions accurately and naturally. Output ONLY the translated lines in the exact format requested."},
            {"role": "user", "content": prompt},
        ]

        # Single-shot call — no internal retries, just accept what comes back
        response_text, finish_reason = client.send(
            messages,
            temperature=0.3,
            context=f"translate_chunk_{chunk_idx}",
        )

        if not response_text:
            _tprint(f"  Chunk {chunk_idx + 1}/{total_chunks}: EMPTY RESPONSE")
            return chunk_idx, {}, len(chunk)

        translations = parse_response(response_text, chunk)

        _tprint(f"  Chunk {chunk_idx + 1}/{total_chunks}: got {len(translations)} translations (expected {len(chunk)})")

        missing = len(chunk) - len(translations)
        if missing > 0:
            _tprint(f"  Chunk {chunk_idx + 1}/{total_chunks}: WARNING — {missing} lines not translated")

        if finish_reason and finish_reason != "stop":
            _tprint(f"  Chunk {chunk_idx + 1}/{total_chunks}: finish_reason={finish_reason}")

        return chunk_idx, translations, len(chunk)

    except Exception as e:
        _tprint(f"  Chunk {chunk_idx + 1}/{total_chunks}: FAILED — {e}")
        return chunk_idx, {}, len(chunk)


def main():
    parser = argparse.ArgumentParser(description="Translate with Unified API Client")
    parser.add_argument("input_file", help="Path to untranslated file")
    parser.add_argument("--lang", default="korean", choices=["korean", "chinese"],
                        help="Source language (default: korean)")
    parser.add_argument("--type", dest="content_type", default="titles",
                        choices=["titles", "descriptions"],
                        help="Content type (default: titles)")
    parser.add_argument("--model", default=None,
                        help=f"Model to use (default: {DEFAULT_MODEL}, or MODEL env var)")
    parser.add_argument("--workers", type=int, default=MAX_WORKERS,
                        help=f"Max parallel workers (default: {MAX_WORKERS})")
    parser.add_argument("--delay", type=float, default=STAGGER_DELAY,
                        help=f"Seconds between launching each request (default: {STAGGER_DELAY})")
    args = parser.parse_args()

    # Resolve model: CLI flag > MODEL env var > default
    model = args.model or os.environ.get("MODEL", DEFAULT_MODEL)

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
    print(f"Using model: {model}")

    # Chunk the rows
    chunks = chunk_rows(rows, args.content_type)
    chunk_sizes = [len(c) for c in chunks]
    avg_output_per_line = estimate_output_tokens_per_line(rows, args.content_type)

    print(f"Split into {len(chunks)} chunks (lines per chunk: {min(chunk_sizes)}-{max(chunk_sizes)})")
    print(f"Token budgets — input: {MAX_INPUT_TOKENS:,} | output (effective): {EFFECTIVE_OUTPUT_TOKENS:,} | est. output/line: {avg_output_per_line}")
    print(f"Parallel mode: {args.workers} workers, {args.delay}s stagger delay")

    # ── Translate chunks in parallel ──
    all_translations = {}
    total_expected = 0
    failed_chunks = []

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {}
        for i, chunk in enumerate(chunks):
            if i > 0:
                time.sleep(args.delay)
            future = pool.submit(
                process_chunk, i, chunk, len(chunks),
                args.lang, args.content_type, api_key, model,
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
