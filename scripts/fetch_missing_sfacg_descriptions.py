"""Fetch missing SFACG descriptions from the API.

Identifies novels in sfacg_novels.json that are NOT in sfacg_descriptions.txt,
fetches their synopsis from SFACG's broad type/char-count API buckets, and
appends them to the descriptions file.

Usage:
    python scripts/fetch_missing_sfacg_descriptions.py
"""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import gzip
import json
import os
import re
import sys
import time

import requests

import scrape_sfacg as sfacg

sys.stdout.reconfigure(encoding="utf-8")

HEADERS = {
    "User-Agent": "boluobao/5.0.36(android;34)/H5/{}/H5",
    "Accept": "application/json",
    "Authorization": "Basic YW5kcm9pZHVzZXI6MWEjJDUxLXl0Njk7KkFjdkBxeHE=",
}

DATA = os.path.join("docs", "data", "sfacg_novels.json")
DESC = os.path.join("docs", "data", "sfacg_descriptions.txt")
TYPE_NAME_TO_ID = {
    item["typeName"]: int(item["typeId"])
    for item in sfacg.FALLBACK_NOVEL_TYPES
}


def ensure_descriptions_txt():
    """Decompress the committed descriptions file when only .gz exists."""
    if os.path.exists(DESC) or not os.path.exists(DESC + ".gz"):
        return
    print(f"Decompressing {DESC}.gz -> {DESC}", flush=True)
    with gzip.open(DESC + ".gz", "rb") as gz_in:
        with open(DESC, "wb") as f_out:
            f_out.write(gz_in.read())


def normalize_intro(intro):
    intro = (intro or "").replace("\r\n", "\n").replace("\r", "\n")
    intro = re.sub(r"\n{2,}", "\n", intro).strip()
    return intro.replace("\n", "\\n")


def broad_context_from_row(row):
    tags = row[4] if len(row) > 4 and isinstance(row[4], list) else []
    type_name = tags[0] if tags else ""
    type_id = TYPE_NAME_TO_ID.get(type_name)
    char_count = row[7] if len(row) > 7 else None
    if not type_id or char_count is None:
        return None
    return {
        "id": str(row[0]),
        "type_id": type_id,
        "chapters": char_count,
    }


def fetch_one(nid, context):
    session = requests.Session()
    session.headers.update(HEADERS)
    try:
        if not context:
            return nid, None, "missing broad context"
        novel = sfacg.fetch_broad_item_for_novel(session, context)
        if not novel:
            return nid, None, "missing from broad bucket"
        intro = novel.get("synopsis", "")
        return nid, normalize_intro(intro) if intro else "N/A", None
    except Exception as exc:
        return nid, None, str(exc)


def main():
    parser = argparse.ArgumentParser(description="Fetch missing SFACG descriptions")
    parser.add_argument("--workers", type=int, default=16, help="Parallel fetch workers")
    parser.add_argument("--delay", type=float, default=0.0, help="Delay after scheduling each request")
    parser.add_argument("--limit", type=int, default=0, help="Fetch at most N missing rows (for testing)")
    parser.add_argument("--batch-size", type=int, default=500, help="Rows to fetch before appending progress")
    parser.add_argument("--ids", default="", help="Comma-separated novel IDs to fetch, limited to missing rows")
    args = parser.parse_args()

    ensure_descriptions_txt()

    # Load all novel IDs from JSON
    with open(DATA, "r", encoding="utf-8") as f:
        novels = json.load(f)
    novel_rows = {str(n[0]): n for n in novels if n}
    all_ids = set(novel_rows)
    print(f"Total novels in JSON: {len(all_ids)}", flush=True)

    # Load existing description IDs
    existing_ids = set()
    if os.path.exists(DESC):
        with open(DESC, "r", encoding="utf-8") as f:
            for line in f:
                line = line.rstrip("\r\n")
                if not line:
                    continue
                nid = line.split("|||")[0].strip()
                if nid:
                    existing_ids.add(nid)
    print(f"Existing descriptions: {len(existing_ids)}", flush=True)

    missing_set = all_ids - existing_ids
    if args.ids.strip():
        requested_ids = {nid.strip() for nid in args.ids.split(",") if nid.strip()}
        missing_set &= requested_ids
    missing = sorted(missing_set, key=int)
    if args.limit > 0:
        missing = missing[:args.limit]
    print(f"Missing descriptions: {len(missing)}", flush=True)

    if not missing:
        print("Nothing to fetch!")
        return

    fetched = 0
    failed = 0
    max_workers = max(1, args.workers)
    batch_size = max(1, args.batch_size)
    appended = 0

    with open(DESC, "a", encoding="utf-8") as out:
        for batch_start in range(0, len(missing), batch_size):
            batch = missing[batch_start:batch_start + batch_size]
            new_rows_by_id = {}

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = []
                for nid in batch:
                    context = broad_context_from_row(novel_rows.get(nid, []))
                    futures.append(executor.submit(fetch_one, nid, context))
                    if args.delay > 0:
                        time.sleep(args.delay)

                for future in as_completed(futures):
                    nid, intro, error = future.result()
                    if error:
                        failed += 1
                    else:
                        new_rows_by_id[nid] = f"{nid}|||{intro}|||\n"
                        fetched += 1

            if new_rows_by_id:
                new_rows = [new_rows_by_id[nid] for nid in sorted(new_rows_by_id, key=int)]
                out.writelines(new_rows)
                out.flush()
                appended += len(new_rows)

            done = min(batch_start + len(batch), len(missing))
            print(
                f"  Progress: {done}/{len(missing)} "
                f"({fetched} fetched, {failed} failed, {appended} appended)",
                flush=True,
            )

    if appended:
        print(f"\nAppended {appended} rows to {DESC}")

    print(f"Done: {fetched} fetched, {failed} failed out of {len(missing)} missing")


if __name__ == "__main__":
    main()
