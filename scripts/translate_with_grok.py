"""Translate untranslated titles/descriptions using an OpenAI-compatible API.

Simple, reliable translation script for CI/CD workflows.
Uses requests directly with proper timeouts - no internal retry on bad results.
Accepts whatever the model returns and moves on.

Environment:
    TRANSLATION_API_KEY       - preferred API key for any provider
    OPENAI_API_KEY            - fallback API key
    GROK_API_KEY              - fallback API key for xAI/Grok
    DEEPSEEK_API_KEY          - fallback API key for DeepSeek
    TRANSLATION_API_BASE_URL  - OpenAI-compatible base URL; leave blank
                               to infer from model/key name
    TRANSLATION_OUTPUT_TOKEN_LIMIT - API completion token limit
                                     (default: 80000)
    TRANSLATION_COMPRESSION_FACTOR - chunk divisor for output token limit
                                     (default: 2.0)
    MODEL                    - override model name (optional)

Usage:
    python scripts/translate_with_grok.py <input_file> [--lang korean|chinese] [--type titles|descriptions]
"""

import os, sys, json, time, argparse, requests, threading
from concurrent.futures import ThreadPoolExecutor, FIRST_COMPLETED, wait

sys.stdout.reconfigure(encoding="utf-8")

import tiktoken
_enc = tiktoken.get_encoding("cl100k_base")

DEFAULT_API_BASE_URL = "https://api.x.ai/v1"
OPENAI_API_BASE_URL = "https://api.openai.com/v1"
DEEPSEEK_API_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "grok-4-1-fast-reasoning"

# API output cap and derived chunk size.
DEFAULT_OUTPUT_TOKEN_LIMIT = 80_000
DEFAULT_COMPRESSION_FACTOR = 2.0

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


def build_prompt(rows, lang, content_type):
    """Build the user prompt — just the raw lines to translate."""
    return "\n".join(rows)



def chunk_rows(rows, chunk_token_limit):
    """Pack complete rows into chunks up to a soft token limit.

    Rows are never split. A single row larger than the limit is kept as one
    chunk because there is no safe row boundary inside it.
    """
    chunks = []
    cur = []
    cur_tokens = 0

    for row in rows:
        row_tokens = count_tokens(row + "\n")

        if cur and cur_tokens + row_tokens > chunk_token_limit:
            chunks.append(cur)
            cur = []
            cur_tokens = 0

        cur.append(row)
        cur_tokens += row_tokens

    if cur:
        chunks.append(cur)
    return chunks


def chunk_token_counts(chunks):
    return [count_tokens("\n".join(chunk)) for chunk in chunks]


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


def write_translations(input_file, translations):
    """Persist known translations into the source patch file atomically."""
    if not translations:
        return

    output_lines = []
    with open(input_file, "r", encoding="utf-8") as f:
        for line in f:
            s = line.rstrip("\r\n")
            newline = "\n" if line.endswith("\n") else ""
            if not s:
                output_lines.append(s + newline)
                continue
            parts = s.split("|||")
            nid = parts[0].strip()
            if len(parts) >= 3 and parts[2].strip():
                output_lines.append(s + newline)
                continue
            if nid in translations:
                original = parts[1] if len(parts) >= 2 else ""
                output_lines.append(
                    f"{nid}|||{original}|||{translations[nid]}\n"
                )
            else:
                output_lines.append(s + newline)

    tmp_file = f"{input_file}.tmp"
    with open(tmp_file, "w", encoding="utf-8") as f:
        f.writelines(output_lines)
    os.replace(tmp_file, input_file)


FORMAT_RULES = """Format rules:
- Every input line has exactly this 3-column format: ID|||ORIGINAL|||ENGLISH
- Column 1 (ID): copy exactly.
- Column 2 (ORIGINAL): copy exactly; keep it in the original language.
- Column 3 (ENGLISH): fill only this column with the English translation.
- Use ||| as the only delimiter; every output line must have exactly two |||.
- Preserve row order. Do not skip, merge, split, renumber, or add rows.
- Literal \\n sequences inside column 2 must remain literal \\n sequences.
- Output only translated rows. No markdown, notes, headers, or commentary."""


def build_system_prompt(content_type, lang):
    source_language = {
        "korean": "Korean",
        "chinese": "Chinese",
    }.get(lang, lang or "the source language")

    if content_type == "titles":
        return f"""You translate {source_language} web novel titles into natural English titles.

Title translation goals:
- Make the title sound like a real English web novel title, not a word-by-word gloss.
- Preserve genre signals, jokes, and common tropes like regression, reincarnation, possession, academy, dungeon, hunter, villainess, and overpowered protagonist.
- Keep titles concise and readable. Do not add summaries or explanations.
- Romanize names, places, fandoms, brands, and invented terms when translation would be awkward.
- Use Title Case unless the title naturally needs sentence case.
- If the original is already English or a Latin-script proper noun, keep it as-is.

{FORMAT_RULES}"""

    if content_type == "descriptions":
        return f"""You translate {source_language} web novel descriptions and synopses into fluent English.

Description translation goals:
- Preserve the full meaning, tone, genre hooks, warnings, and promotional style.
- Write natural English prose. Avoid stiff machine-translation phrasing.
- Keep paragraph breaks and literal \\n markers exactly where they appear in column 2.
- Do not censor, summarize, soften, add spoilers, or invent details.
- Romanize character names, place names, series titles, and proper nouns consistently.
- If text is already English, keep it as-is.

{FORMAT_RULES}"""

    return f"""You translate {source_language} web novel tags and genre labels into English.

Tag translation goals:
- Keep each tag short: usually 1-3 words.
- Prefer common English genre/tag wording used by web novel readers.
- Translate tropes in context, not literally.
- Romanize proper nouns, franchise names, memes, or untranslatable slang.
- Column 3 should be Latin-script English whenever possible.
- Use concise Title Case tags such as Modern Fantasy, Regression, Possession, Academy, Dungeon, Overpowered MC.
- If the tag is already English or a Latin-script name, keep it as-is.

{FORMAT_RULES}"""


def normalize_chat_completions_url(base_url):
    """Return a /chat/completions endpoint for an OpenAI-compatible base URL."""
    base_url = (base_url or "").strip().rstrip("/")
    if not base_url:
        raise ValueError("base_url is required after provider routing")
    if base_url.endswith("/chat/completions"):
        return base_url
    if base_url.endswith("/v1"):
        return f"{base_url}/chat/completions"
    return f"{base_url}/v1/chat/completions"


def infer_api_base_url(model):
    """Infer provider route from model name or configured API-key variable."""
    hint = " ".join([
        model or "",
        os.environ.get("TRANSLATION_API_KEY_NAME", ""),
    ]).lower()

    if "deepseek" in hint:
        return DEEPSEEK_API_BASE_URL
    if "grok" in hint or "xai" in hint or "x-ai" in hint:
        return DEFAULT_API_BASE_URL
    if "openai" in hint or "gpt-" in hint or "o1" in hint or "o3" in hint:
        return OPENAI_API_BASE_URL

    if os.environ.get("DEEPSEEK_API_KEY"):
        return DEEPSEEK_API_BASE_URL
    if os.environ.get("GROK_API_KEY") or os.environ.get("XAI_API_KEY"):
        return DEFAULT_API_BASE_URL
    if os.environ.get("OPENAI_API_KEY"):
        return OPENAI_API_BASE_URL

    return DEFAULT_API_BASE_URL


def resolve_api_key(api_key_env=None):
    """Resolve API key from a named env var or common provider fallbacks."""
    if api_key_env:
        key = os.environ.get(api_key_env)
        if not key:
            print(f"Error: {api_key_env} not set")
            sys.exit(1)
        return key

    for env_name in (
        "TRANSLATION_API_KEY",
        "OPENAI_API_KEY",
        "GROK_API_KEY",
        "DEEPSEEK_API_KEY",
    ):
        key = os.environ.get(env_name)
        if key:
            return key

    print(
        "Error: no API key set. Use TRANSLATION_API_KEY, OPENAI_API_KEY, "
        "GROK_API_KEY, DEEPSEEK_API_KEY, or --api-key-env."
    )
    sys.exit(1)


def resolve_api_base_url(model, cli_base_url=None):
    manual_base_url = (
        cli_base_url
        or os.environ.get("TRANSLATION_API_BASE_URL")
        or os.environ.get("OPENAI_BASE_URL")
        or os.environ.get("GROK_API_BASE_URL")
        or os.environ.get("DEEPSEEK_API_BASE_URL")
    )
    if manual_base_url and manual_base_url.strip():
        return manual_base_url
    return infer_api_base_url(model)


def env_int(name):
    value = os.environ.get(name)
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        print(f"Error: {name} must be an integer, got {value!r}")
        sys.exit(1)


def env_float(name):
    value = os.environ.get(name)
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        print(f"Error: {name} must be a number, got {value!r}")
        sys.exit(1)


def derive_chunk_token_limit(output_token_limit, compression_factor):
    if output_token_limit <= 0:
        print("Error: output token limit must be greater than 0")
        sys.exit(1)
    if compression_factor <= 0:
        print("Error: compression factor must be greater than 0")
        sys.exit(1)
    return max(1, int(output_token_limit / compression_factor))


def call_api(prompt, api_key, model, api_url, content_type="titles",
             lang="korean", output_token_limit=None):
    """Single API call with proper timeout. No retries — accept what we get."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    system_prompt = build_system_prompt(content_type, lang)

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.3,
    }
    if output_token_limit:
        payload["max_tokens"] = output_token_limit

    # (connect_timeout=30s, read_timeout=900s)
    resp = requests.post(api_url, headers=headers, json=payload,
                         timeout=(30, 900))
    resp.raise_for_status()

    data = resp.json()
    content = data["choices"][0]["message"]["content"].strip()
    usage = data.get("usage", {})
    _tprint(f"    Tokens: {usage.get('prompt_tokens', '?')} in, {usage.get('completion_tokens', '?')} out")
    return content


def process_chunk(idx, chunk, total, lang, content_type, api_key, model,
                  api_url, output_token_limit=None):
    """Process one chunk. Single attempt, accept whatever comes back."""
    _tprint(f"\n  Chunk {idx+1}/{total} ({len(chunk)} lines)...")

    try:
        prompt = build_prompt(chunk, lang, content_type)
        response = call_api(
            prompt, api_key, model, api_url, content_type,
            lang, output_token_limit
        )
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
    parser = argparse.ArgumentParser(
        description="Translate with an OpenAI-compatible chat API"
    )
    parser.add_argument("input_file", help="Path to untranslated file")
    parser.add_argument("--lang", default="korean", choices=["korean", "chinese"])
    parser.add_argument("--type", dest="content_type", default="titles",
                        choices=["titles", "descriptions", "tags"])
    parser.add_argument("--model", default=None)
    parser.add_argument(
        "--api-base-url",
        default=None,
        help=(
            "OpenAI-compatible base URL or full /chat/completions URL "
            "(blank: infer from model/API-key name)"
        ),
    )
    parser.add_argument(
        "--api-key-env",
        default=None,
        help=(
            "Environment variable containing the API key. If omitted, checks "
            "TRANSLATION_API_KEY, OPENAI_API_KEY, GROK_API_KEY, DEEPSEEK_API_KEY."
        ),
    )
    parser.add_argument(
        "--output-token-limit",
        type=int,
        default=None,
        help=(
            "API max_tokens / completion-token limit per request "
            f"(default: {DEFAULT_OUTPUT_TOKEN_LIMIT})."
        ),
    )
    parser.add_argument(
        "--compression-factor",
        type=float,
        default=None,
        help=(
            "Chunk-size divisor. Chunk token target is "
            "output_token_limit / compression_factor "
            f"(default: {DEFAULT_COMPRESSION_FACTOR})."
        ),
    )
    parser.add_argument("--workers", type=int, default=MAX_WORKERS)
    parser.add_argument("--delay", type=float, default=STAGGER_DELAY)
    args = parser.parse_args()

    model = args.model or os.environ.get("MODEL", DEFAULT_MODEL)
    api_key = resolve_api_key(args.api_key_env)
    api_base_url = resolve_api_base_url(model, args.api_base_url)
    api_url = normalize_chat_completions_url(api_base_url)
    output_token_limit = (
        args.output_token_limit
        or env_int("TRANSLATION_OUTPUT_TOKEN_LIMIT")
        or DEFAULT_OUTPUT_TOKEN_LIMIT
    )
    compression_factor = (
        args.compression_factor
        or env_float("TRANSLATION_COMPRESSION_FACTOR")
        or DEFAULT_COMPRESSION_FACTOR
    )
    chunk_token_limit = derive_chunk_token_limit(
        output_token_limit, compression_factor
    )
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
    print(f"Using API endpoint: {api_url}")

    chunks = chunk_rows(rows, chunk_token_limit)
    sizes = [len(c) for c in chunks]
    token_sizes = chunk_token_counts(chunks)

    print(f"Split into {len(chunks)} chunks (lines per chunk: {min(sizes)}-{max(sizes)})")
    print(
        f"Output token limit: {output_token_limit:,} | "
        f"compression factor: {compression_factor:g} | "
        f"soft chunk target: {chunk_token_limit:,} tokens"
    )
    print(
        f"Actual chunk token range: {min(token_sizes):,}-"
        f"{max(token_sizes):,}"
    )
    print(f"Parallel mode: {args.workers} workers, {args.delay}s stagger delay")

    # Translate in parallel and checkpoint as chunks finish.
    all_translations = {}
    total_expected = 0
    failed = []
    completed_chunks = 0

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {}
        next_chunk = 0
        next_submit_at = 0.0

        while next_chunk < len(chunks) or futures:
            now = time.monotonic()
            while (
                next_chunk < len(chunks)
                and len(futures) < args.workers
                and now >= next_submit_at
            ):
                fut = pool.submit(
                    process_chunk, next_chunk, chunks[next_chunk], len(chunks),
                    args.lang, args.content_type, api_key, model,
                    api_url, output_token_limit
                )
                futures[fut] = next_chunk
                next_chunk += 1
                next_submit_at = time.monotonic() + max(0.0, args.delay)
                now = time.monotonic()

            if not futures:
                sleep_for = max(0.0, next_submit_at - time.monotonic())
                if sleep_for:
                    time.sleep(sleep_for)
                continue

            timeout = None
            if next_chunk < len(chunks) and len(futures) < args.workers:
                timeout = max(0.0, next_submit_at - time.monotonic())

            done, _ = wait(
                futures, timeout=timeout, return_when=FIRST_COMPLETED
            )
            if not done:
                continue

            for fut in done:
                futures.pop(fut, None)
                idx, translations, size = fut.result()
                completed_chunks += 1
                all_translations.update(translations)
                total_expected += size
                if translations:
                    write_translations(args.input_file, translations)
                    _tprint(
                        f"  Checkpointed chunk {idx+1}/{len(chunks)} "
                        f"({completed_chunks}/{len(chunks)} complete)"
                    )
                else:
                    failed.append(idx + 1)

    print(f"\nTotal translations: {len(all_translations)} / {total_expected}")
    if failed:
        print(f"Failed chunks: {sorted(failed)}")

    # Final write is still useful if multiple translations arrived together.
    write_translations(args.input_file, all_translations)

    print(f"Updated {args.input_file}")


if __name__ == "__main__":
    main()
