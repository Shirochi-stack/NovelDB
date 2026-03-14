"""Fetch missing SFACG descriptions from the API.

Identifies novels in sfacg_novels.json that are NOT in sfacg_descriptions.txt,
fetches their synopsis from the SFACG API, and appends them to the descriptions file.

Usage:
    python scripts/fetch_missing_sfacg_descriptions.py
"""

import sys, os, json, time, re, requests

sys.stdout.reconfigure(encoding="utf-8")

API_URL = "https://api.sfacg.com/novels"
HEADERS = {
    "User-Agent": "boluobao/5.0.36(android;34)/H5/{}/H5",
    "Accept": "application/json",
    "Authorization": "Basic YW5kcm9pZHVzZXI6MWEjJDUxLXl0Njk7KkFjdkBxeHE=",
}

DATA = os.path.join("docs", "data", "sfacg_novels.json")
DESC = os.path.join("docs", "data", "sfacg_descriptions.txt")


def main():
    # Load all novel IDs from JSON
    with open(DATA, "r", encoding="utf-8") as f:
        novels = json.load(f)
    all_ids = {str(n[0]) for n in novels if n}
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

    missing = sorted(all_ids - existing_ids, key=int)
    print(f"Missing descriptions: {len(missing)}", flush=True)

    if not missing:
        print("Nothing to fetch!")
        return

    session = requests.Session()
    session.headers.update(HEADERS)

    # Fetch in batches using the novel list API with expand=intro
    # The API supports fetching up to 50 novels at once, but for individual
    # synopsis we need to hit the single-novel endpoint
    fetched = 0
    failed = 0
    new_rows = []

    for i, nid in enumerate(missing):
        try:
            r = session.get(f"{API_URL}/{nid}", params={"expand": "intro"}, timeout=10)
            if r.status_code == 404:
                # Novel doesn't exist on SFACG anymore
                failed += 1
                continue
            r.raise_for_status()
            data = r.json()
            novel = data.get("data", {})
            intro = novel.get("expand", {}).get("intro", "")

            if intro:
                intro = intro.replace("\r\n", "\n").replace("\r", "\n")
                intro = re.sub(r"\n{2,}", "\n", intro).strip()
                flat = intro.replace("\n", "\\n")
                new_rows.append(f"{nid}|||{flat}|||\n")
                fetched += 1
            else:
                # Novel exists but has no description
                new_rows.append(f"{nid}|||N/A|||\n")
                fetched += 1

        except Exception as e:
            print(f"  Error fetching {nid}: {e}")
            failed += 1

        if (i + 1) % 100 == 0:
            print(f"  Progress: {i+1}/{len(missing)} ({fetched} fetched, {failed} failed)", flush=True)

        time.sleep(0.2)

    # Append to descriptions file
    if new_rows:
        with open(DESC, "a", encoding="utf-8") as f:
            f.writelines(new_rows)
        print(f"\nAppended {len(new_rows)} rows to {DESC}")

    print(f"Done: {fetched} fetched, {failed} failed out of {len(missing)} missing")


if __name__ == "__main__":
    main()
