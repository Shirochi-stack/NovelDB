"""Translate untranslated titles/descriptions using the Grok API.

Reads an untranslated file (id|||original or id|||original|||), chunks the rows
to stay within the Grok API token limits (using proper CJK-aware token estimation),
sends translation requests IN PARALLEL, and writes the translated results back to
the same file.

Chunking uses actual token estimation — CJK characters count as ~1 token each,
ASCII characters as ~0.25 tokens each — to stay within the model's context window.

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

# ── Token budget for chunking ──
# The model context window is 131k tokens total (input + output).
# For a reasoning model, output includes thinking tokens which can use 30-60%
# of the output budget. We need to be conservative.
#
# Budget allocation:
#   Input  = 65% of context  → ~85k tokens for the prompt
#   Output = 35% of context  → ~46k tokens for reasoning + translated lines
#
# The prompt includes ~200 tokens of system/instruction overhead.
MAX_CONTEXT_TOKENS = 131072
INPUT_BUDGET_RATIO = 0.65
PROMPT_OVERHEAD_TOKENS = 300  # system message + instruction text
MAX_INPUT_TOKENS = int(MAX_CONTEXT_TOKENS * INPUT_BUDGET_RATIO) - PROMPT_OVERHEAD_TOKENS

# For output, the reasoning model uses ~40-60% of output on thinking.
# We estimate effective output tokens for actual content:
OUTPUT_BUDGET_RATIO = 0.35
REASONING_OVERHEAD = 0.50  # 50% of output goes to thinking
EFFECTIVE_OUTPUT_TOKENS = int(MAX_CONTEXT_TOKENS * OUTPUT_BUDGET_RATIO * (1 - REASONING_OVERHEAD))

# Hard cap on lines per chunk (even if token budget allows more,
# very large batches cause the model to lose focus)
MAX_LINES_PER_CHUNK = {"titles": 1500, "descriptions": 400}

# Concurrency settings
MAX_WORKERS = 67
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


# ── Token estimation ──

def estimate_tokens(text):
    """Estimate the token count for a string using CJK-aware heuristics.

    Most LLM tokenizers (BPE-based) treat CJK characters as individual tokens
    since they're outside the common merge vocabulary. ASCII text compresses
    to ~1 token per 4 characters on average.

    This gives results within ~15% of actual tokenizer output for mixed
    CJK/English text, which is close enough for chunk sizing.
    """
    cjk = 0
    ascii_chars = 0
    for ch in text:
        cp = ord(ch)
        if (0x4E00 <= cp <= 0x9FFF      # CJK Unified Ideographs
            or 0x3400 <= cp <= 0x4DBF    # CJK Extension A
            or 0x20000 <= cp <= 0x2A6DF  # CJK Extension B
            or 0xF900 <= cp <= 0xFAFF    # CJK Compatibility Ideographs
            or 0xAC00 <= cp <= 0xD7AF    # Korean Hangul Syllables
            or 0x1100 <= cp <= 0x11FF    # Hangul Jamo
            or 0x3130 <= cp <= 0x318F    # Hangul Compatibility Jamo
            or 0x3040 <= cp <= 0x309F    # Hiragana
            or 0x30A0 <= cp <= 0x30FF    # Katakana
            or 0xFF00 <= cp <= 0xFFEF    # Fullwidth Forms
            or 0x3000 <= cp <= 0x303F):  # CJK Symbols and Punctuation
            cjk += 1
        else:
            ascii_chars += 1
    # CJK chars ≈ 1 token each, ASCII ≈ 0.25 tokens per char (4 chars/token)
    return cjk + max(1, ascii_chars // 4)


def estimate_output_tokens_per_line(rows, content_type):
    """Estimate how many output tokens each translated line will consume.

    Output format: id|||original|||english_translation
    The original text is echoed back, so output ≈ input + translation.
    For descriptions, translation length ≈ 0.8x the original CJK length.
    For titles, translation ≈ 1.5x the original (short titles expand in English).
    """
    if not rows:
        return 50  # fallback
    # Sample up to 100 rows to estimate
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
        # Output line = id tokens + delimiters (~5) + original + delimiters + translation
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


def call_grok(prompt, api_key, model=DEFAULT_MODEL):
    """Call the Grok API with robust retry logic.

    Rate-limit (429) responses have their own retry budget so they don't
    consume the general retry counter.  Uses a while-loop so that 429s
    truly don't decrement the attempt counter.
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
    attempt = 0

    while attempt < MAX_RETRIES:
        try:
            # Tuple timeout: (connect_timeout, read_timeout)
            # Connect must succeed in 30s, entire response must arrive in 600s
            resp = requests.post(GROK_API_URL, headers=headers, json=payload,
                                 timeout=(30, REQUEST_TIMEOUT))

            if resp.status_code == 429:
                rate_limit_hits += 1
                if rate_limit_hits > MAX_RATE_LIMIT_RETRIES:
                    raise RuntimeError(f"Rate-limited {rate_limit_hits} times, giving up")
                wait = min(120, 2 ** rate_limit_hits * 10)
                _tprint(f"    Rate limited (429 #{rate_limit_hits}), waiting {wait}s...")
                time.sleep(wait)
                # Don't count rate-limits against the normal retry budget
                continue

            if resp.status_code >= 500:
                wait = min(60, 2 ** attempt * 10)
                _tprint(f"    Server error {resp.status_code} on attempt {attempt + 1}, waiting {wait}s...")
                time.sleep(wait)
                attempt += 1
                continue

            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"].strip()
            usage = data.get("usage", {})
            _tprint(f"    Tokens: {usage.get('prompt_tokens', '?')} in, {usage.get('completion_tokens', '?')} out")
            return content

        except requests.exceptions.Timeout:
            _tprint(f"    Timeout on attempt {attempt + 1}/{MAX_RETRIES}, retrying...")
            time.sleep(10)
            attempt += 1
        except requests.exceptions.ConnectionError as e:
            wait = min(60, 2 ** attempt * 10)
            _tprint(f"    Connection error on attempt {attempt + 1}: {e}")
            _tprint(f"    Waiting {wait}s before retry...")
            time.sleep(wait)
            attempt += 1
        except RuntimeError:
            raise
        except Exception as e:
            _tprint(f"    Error on attempt {attempt + 1}: {e}")
            attempt += 1
            if attempt >= MAX_RETRIES:
                raise
            time.sleep(10)

    raise RuntimeError("All retries exhausted")


def chunk_rows(rows, content_type):
    """Split rows into chunks using actual token estimation.

    Each chunk is bounded by three constraints:
    1. Input tokens (measured) must fit within the model's input budget
    2. Estimated output tokens must fit within the effective output budget
    3. Line count must not exceed the hard cap for the content type
    """
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

    # Chunk the rows using token estimation
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
