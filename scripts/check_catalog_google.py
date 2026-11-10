"""Exercise catalogue Google dorks and record accepted book links or verification failures."""

import argparse
import time
from pathlib import Path

from noveldb.core.catalog.generic_search import (GenericSearch, GoogleUnavailable, default_fetch,
    google_dorks, google_results, looks_like_book, top_sites)
from noveldb.core.catalog.title_search import similar_title
from scripts.source_names import dumps


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", default="Shadow Slave")
    parser.add_argument("--browser", action="store_true", help="use the app's browser for JavaScript-only Google pages")
    parser.add_argument("--output", type=Path, default=Path("artifacts/catalog-google-check.json"))
    args = parser.parse_args()
    scraper = None
    if args.browser:
        from external_scraper import ExternalScraper
        scraper = ExternalScraper(logger=lambda _message: None)

    def fetch(url, **kwargs):
        result = default_fetch(url, **kwargs)
        if scraper is not None and result and "/httpservice/retry/enablejs" in result.get("text", ""):
            html = scraper.render_html(url)
            if html:
                return {"status": 200, "text": html, "content": html.encode("utf-8"), "url": url, "headers": ""}
        return result

    results = []
    engine = GenericSearch(fetch=fetch)
    try:
        for query in google_dorks(args.query, "name", top_sites()):
            started = time.monotonic()
            try:
                hits = google_results(query, fetch)
                accepted = [hit for hit in hits if engine.site_for(hit["url"]) and looks_like_book(hit["url"], hit["title"])
                            and similar_title(args.query, hit["title"]) >= 0.75]
                results.append({"query": query, "state": "ok", "hits": len(hits), "accepted": accepted})
            except GoogleUnavailable as error:
                results.append({"query": query, "state": "verification-required", "error": str(error)})
            results[-1]["ms"] = round((time.monotonic() - started) * 1000)
    finally:
        if scraper is not None:
            scraper.cleanup()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(dumps({"query": args.query, "results": results}, indent=2), encoding="utf-8")
    for result in results:
        print(f"{result['state']} {result['ms']} ms, {len(result.get('accepted', []))} accepted book links")
    return 2 if any(result["state"] != "ok" for result in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
