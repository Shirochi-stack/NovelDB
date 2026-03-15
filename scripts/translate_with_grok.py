"""Translate untranslated titles/descriptions using the xAI (Grok) API.

Simple, reliable translation script for CI/CD workflows.
Uses requests directly with proper timeouts — no internal retry on bad results.
Accepts whatever the model returns and moves on.

Environment:
    GROK_API_KEY  — required API key (set via GitHub secret)
    MODEL         — override model name (optional)

Usage:
    python scripts/translate_with_grok.py <input_file> [--lang korean|chinese] [--type titles|descriptions]
"""

import os, sys, json, time, argparse, requests, threading
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.stdout.reconfigure(encoding="utf-8")

import tiktoken
_enc = tiktoken.get_encoding("cl100k_base")

GROK_API_URL = "https://api.x.ai/v1/chat/completions"
DEFAULT_MODEL = "grok-4-1-fast-reasoning"

# ── Token budgets ──
# Grok has a 2M context window. These are for chunk sizing only.
MAX_INPUT_TOKENS = 200_000
EFFECTIVE_OUTPUT_TOKENS = 60_000

# Hard cap on lines per chunk
MAX_LINES_PER_CHUNK = {"titles": 1500, "descriptions": 400, "tags": 2000}

# Concurrency
MAX_WORKERS = 67
STAGGER_DELAY = 5

# Thread-safe print
_print_lock = threading.Lock()
def _tprint(*args, **kwargs):
    with _print_lock:
        print(*args, **kwargs)
        sys.stdout.flush()


def count_tokens(text):
    """Count tokens using tiktoken (cl100k_base)."""
    return len(_enc.encode(text))


def estimate_output_per_line(rows, content_type):
    """Estimate output tokens per translated line."""
    if not rows:
        return 50
    sample = rows[:100]
    total = 0
    for row in sample:
        parts = row.split("|||")
        original = parts[1].strip() if len(parts) >= 2 else ""
        orig_tokens = count_tokens(original)
        ratio = 0.8 if content_type == "descriptions" else 1.5
        total += 5 + orig_tokens + int(orig_tokens * ratio)
    return max(10, total // len(sample))



def build_prompt(rows, lang, content_type):
    """Build the user prompt — just the raw lines to translate."""
    return "\n".join(rows)



def chunk_rows(rows, content_type):
    """Split rows into chunks based on token budget."""
    max_lines = MAX_LINES_PER_CHUNK.get(content_type, 500)
    avg_out = estimate_output_per_line(rows, content_type)

    chunks = []
    cur = []
    cur_in = 0
    cur_out = 0

    for row in rows:
        row_in = count_tokens(row + "\n")
        row_out = avg_out

        if cur and (len(cur) >= max_lines
                    or cur_in + row_in > MAX_INPUT_TOKENS
                    or cur_out + row_out > EFFECTIVE_OUTPUT_TOKENS):
            chunks.append(cur)
            cur, cur_in, cur_out = [], 0, 0

        cur.append(row)
        cur_in += row_in
        cur_out += row_out

    if cur:
        chunks.append(cur)
    return chunks


def parse_response(text, original_rows):
    """Extract translations from API response."""
    translations = {}
    valid_ids = {row.split("|||")[0].strip() for row in original_rows}

    for line in text.split("\n"):
        line = line.strip()
        if not line or "|||" not in line:
            continue
        parts = line.split("|||")
        nid = parts[0].strip()
        if nid in valid_ids and len(parts) >= 3 and parts[2].strip():
            translations[nid] = parts[2].strip()

    return translations


SYSTEM_PROMPT_DEFAULT = """You are translating text to English. Each line has this exact 3-column format:

ID|||ORIGINAL|||ENGLISH

INPUT example:
390198|||어쩌다 보니 캄피오네 가 되버린 주인공의 모험기|||

OUTPUT example:
390198|||어쩌다 보니 캄피오네 가 되버린 주인공의 모험기|||The Adventure of a Protagonist Who Accidentally Became a Campione

Rules:
- Column 1 (ID): Copy EXACTLY, do not change
- Column 2 (ORIGINAL): Copy EXACTLY, do not change — this MUST remain in the original language
- Column 3 (ENGLISH): Write your English translation here
- Use ||| as the ONLY delimiter between columns — every line must have exactly two |||
- Translate naturally, not robotically
- Romanize proper nouns (e.g. 김철수 → Kim Cheolsu)
- If text is already English, keep as-is
- Literal \\n in column 2 are line breaks — do NOT remove them, copy them exactly
- Output ALL lines — do not skip, merge, or reorder
- Output ONLY translated lines — no commentary, headers, or explanations"""

SYSTEM_PROMPT_TAGS = """You are translating Korean web novel tags/genres into English. Each line has this exact 3-column format:

ID|||KOREAN_TAG|||ENGLISH_TAG

INPUT example:
42|||현대판타지|||
15|||블루아카이브|||
99|||먼치킨|||

OUTPUT example:
42|||현대판타지|||Modern Fantasy
15|||블루아카이브|||Blue Archive
99|||먼치킨|||Munchkin/OP MC

Rules:
- Column 1 (ID): Copy EXACTLY, do not change
- Column 2 (KOREAN_TAG): Copy EXACTLY, do not change
- Column 3 (ENGLISH_TAG): Write the English translation here
- Use ||| as the ONLY delimiter — every line must have exactly two |||
- These are web novel genre tags, so translate in that context (e.g. 회귀 = Regression, 빙의 = Possession, 성장 = Growth)
- If a tag is a proper noun or brand name, romanize it (e.g. 블루아카이브 → Blue Archive, 원신 → Genshin)
- If you cannot translate a tag meaningfully, romanize it to Latin script (e.g. 야스 → Yasu)
- ALL output must be in Latin script — no Korean/CJK characters in column 3
- If text is already in English/Latin, keep as-is
- Keep translations SHORT — these are tags, not sentences. 1-3 words ideal
- Output EVERY line — you MUST NOT skip any lines. Every input line needs an output line
- Output ONLY translated lines — no commentary, headers, or explanations"""


def call_api(prompt, api_key, model, content_type="titles"):
    """Single API call with proper timeout. No retries — accept what we get."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    system_prompt = SYSTEM_PROMPT_TAGS if content_type == "tags" else SYSTEM_PROMPT_DEFAULT

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.3,
    }

    # (connect_timeout=30s, read_timeout=300s)
    resp = requests.post(GROK_API_URL, headers=headers, json=payload,
                         timeout=(30, 300))
    resp.raise_for_status()

    data = resp.json()
    content = data["choices"][0]["message"]["content"].strip()
    usage = data.get("usage", {})
    _tprint(f"    Tokens: {usage.get('prompt_tokens', '?')} in, {usage.get('completion_tokens', '?')} out")
    return content


def process_chunk(idx, chunk, total, lang, content_type, api_key, model):
    """Process one chunk. Single attempt, accept whatever comes back."""
    _tprint(f"\n  Chunk {idx+1}/{total} ({len(chunk)} lines)...")

    try:
        prompt = build_prompt(chunk, lang, content_type)
        response = call_api(prompt, api_key, model, content_type)
        translations = parse_response(response, chunk)

        _tprint(f"  Chunk {idx+1}/{total}: got {len(translations)} translations (expected {len(chunk)})")
        missing = len(chunk) - len(translations)
        if missing > 0:
            _tprint(f"  Chunk {idx+1}/{total}: WARNING — {missing} lines not translated")

        return idx, translations, len(chunk)

    except Exception as e:
        _tprint(f"  Chunk {idx+1}/{total}: FAILED — {e}")
        return idx, {}, len(chunk)


def main():
    parser = argparse.ArgumentParser(description="Translate with Grok API")
    parser.add_argument("input_file", help="Path to untranslated file")
    parser.add_argument("--lang", default="korean", choices=["korean", "chinese"])
    parser.add_argument("--type", dest="content_type", default="titles",
                        choices=["titles", "descriptions", "tags"])
    parser.add_argument("--model", default=None)
    parser.add_argument("--workers", type=int, default=MAX_WORKERS)
    parser.add_argument("--delay", type=float, default=STAGGER_DELAY)
    args = parser.parse_args()

    model = args.model or os.environ.get("MODEL", DEFAULT_MODEL)
    api_key = os.environ.get("GROK_API_KEY")
    if not api_key:
        print("Error: GROK_API_KEY not set"); sys.exit(1)
    if not os.path.exists(args.input_file):
        print(f"Error: {args.input_file} not found"); sys.exit(1)

    # Read untranslated rows
    rows = []
    with open(args.input_file, "r", encoding="utf-8") as f:
        for line in f:
            s = line.rstrip("\r\n")
            if not s: continue
            parts = s.split("|||")
            nid = parts[0].strip()
            if not nid or not nid.isdigit(): continue
            if len(parts) >= 3 and parts[2].strip(): continue
            rows.append(s)

    if not rows:
        print("No untranslated rows found. Nothing to do.")
        return

    print(f"Found {len(rows)} untranslated rows in {args.input_file}")
    print(f"Using model: {model}")

    chunks = chunk_rows(rows, args.content_type)
    sizes = [len(c) for c in chunks]
    avg_out = estimate_output_per_line(rows, args.content_type)

    print(f"Split into {len(chunks)} chunks (lines per chunk: {min(sizes)}-{max(sizes)})")
    print(f"Token budgets — input: {MAX_INPUT_TOKENS:,} | output (effective): {EFFECTIVE_OUTPUT_TOKENS:,} | est. output/line: {avg_out}")
    print(f"Parallel mode: {args.workers} workers, {args.delay}s stagger delay")

    # Translate in parallel
    all_translations = {}
    total_expected = 0
    failed = []

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {}
        for i, chunk in enumerate(chunks):
            if i > 0:
                time.sleep(args.delay)
            fut = pool.submit(process_chunk, i, chunk, len(chunks),
                              args.lang, args.content_type, api_key, model)
            futures[fut] = i

        for fut in as_completed(futures):
            idx, translations, size = fut.result()
            all_translations.update(translations)
            total_expected += size
            if not translations:
                failed.append(idx + 1)

    print(f"\nTotal translations: {len(all_translations)} / {total_expected}")
    if failed:
        print(f"Failed chunks: {sorted(failed)}")

    # Write results back
    output_lines = []
    with open(args.input_file, "r", encoding="utf-8") as f:
        for line in f:
            s = line.rstrip("\r\n")
            if not s:
                output_lines.append(s + "\n")
                continue
            parts = s.split("|||")
            nid = parts[0].strip()
            if len(parts) >= 3 and parts[2].strip():
                output_lines.append(s + "\n")
                continue
            if nid in all_translations:
                original = parts[1] if len(parts) >= 2 else ""
                output_lines.append(f"{nid}|||{original}|||{all_translations[nid]}\n")
            else:
                output_lines.append(s + "\n")

    with open(args.input_file, "w", encoding="utf-8") as f:
        f.writelines(output_lines)

    print(f"Updated {args.input_file}")


if __name__ == "__main__":
    main()
