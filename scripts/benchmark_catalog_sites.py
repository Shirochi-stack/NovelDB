"""Rank actual catalogue search responses. Run with --write-profile to ship measured results."""

import argparse
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from noveldb.core.catalog.generic_search import default_fetch, top_sites
from noveldb.core.catalog.search_ranking import PROFILE, profile
from noveldb.core.catalog.site_search import SiteSearch
from noveldb.core.engines.generic import decode, extract_metadata, site_of
from scripts.source_names import dumps


def probe(host, query, rounds=2, timeout=6, browser=False):
    scraper = None
    if browser:
        from external_scraper import ExternalScraper
        scraper = ExternalScraper(logger=lambda _message: None)
    samples = []
    for _ in range(rounds):
        statuses = []

        def fetch(url, **kwargs):
            if scraper:
                options = {key: value for key, value in kwargs.items() if key in ("method", "data", "headers", "binary", "referer")}
                result = scraper.engine_fetch(url, **options)
            else:
                result = default_fetch(url, **{**kwargs, "timeout": timeout})
            statuses.append(result.get("status", 0) if result else 0)
            return result

        started = time.monotonic()
        search = SiteSearch(fetch)
        if scraper:
            from scripts.check_generic_sites import _rendered
            search.render = lambda url: _rendered(scraper, url)
        try:
            if host.startswith(("http://", "https://")):
                result = fetch(host)
                metadata = extract_metadata(decode(result), host) if result and result["status"] == 200 else {}
                hits = [metadata] if metadata.get("title") and metadata.get("cover") else []
                reachable = bool(hits)
            else:
                hits = search.search("https://" + host + "/", query, budget=timeout)
                reachable = search.plan("https://" + host + "/").get("reachable", False)
            ok = reachable and bool(hits) and not any(s in (0, 403, 429, 503) for s in statuses)
        except Exception as error:
            hits, ok = [], False
            samples.append({"ms": round((time.monotonic() - started) * 1000), "ok": False, "results": 0, "statuses": statuses, "error": str(error)})
            continue
        samples.append({"ms": round((time.monotonic() - started) * 1000), "ok": ok, "results": len(hits), "statuses": statuses})
    if scraper:
        scraper.cleanup()
    passed = [s["ms"] for s in samples if s["ok"]]
    return {"site": site_of(urlsplit(host).hostname or host), "ms": round(statistics.median(passed or [s["ms"] for s in samples])),
            "ok": len(passed) == rounds, "at": time.time(), "samples": samples}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sites", nargs="*")
    parser.add_argument("--book-urls", nargs="*", help="measure real book metadata responses instead of site search guesses")
    parser.add_argument("--query", default="Shadow Slave")
    parser.add_argument("--rounds", type=int, default=2)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=6)
    parser.add_argument("--output", type=Path, default=Path("artifacts/catalog-responsiveness.json"))
    parser.add_argument("--write-profile", action="store_true")
    parser.add_argument("--browser", action="store_true", help="measure through the app's browser transport")
    args = parser.parse_args()
    if args.rounds < 1 or args.workers < 1 or args.timeout <= 0:
        parser.error("rounds, workers and timeout must be positive")
    preferred = profile().get("preferred", [])
    sites = args.book_urls or args.sites or preferred
    known = {s["site"]: s["host"] for s in top_sites()}
    with ThreadPoolExecutor(1 if args.browser else min(args.workers, 6)) as pool:
        results = list(pool.map(lambda name: probe(known.get(name, name), args.query, args.rounds, args.timeout, args.browser), sites))
    results.sort(key=lambda result: (not result["ok"], result["ms"], result["site"]))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(dumps({"query": args.query, "results": results}, indent=2), encoding="utf-8")
    for result in results:
        print(f"{'PASS' if result['ok'] else 'FAIL'} {result['ms']:6} ms  {result['site']}")
    if args.write_profile:
        data = profile()
        for result in results:
            if not result["ok"]:
                continue
            data["measurements"][result["site"]] = {key: result[key] for key in ("ms", "ok", "at")}
        PROFILE.write_text(dumps(data, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
