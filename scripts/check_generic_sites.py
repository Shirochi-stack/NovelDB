"""Check the Catalog's generic search and the generic engine on the generic-search sites.

For each site in noveldb/core/catalog/generic_sites.json:
1. find a book on its home page (the generic result-card reader),
2. search the site for that book's title with its own search (the generic site search),
3. read the book page's metadata and cover,
4. read the book's chapter list and first chapter with the generic engine.

Pages come through plain HTTP first. With --browser, sites that refuse that are checked again with the
app's transport (the External Downloader's browser as the fallback), one at a time.

    python scripts/check_generic_sites.py --out report.json
    python scripts/check_generic_sites.py --browser --sites royalroad.com,wattpad.com
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from noveldb.core.catalog.generic_search import GenericSearch, default_fetch, top_sites  # noqa: E402
from noveldb.core.catalog.site_search import SiteSearch, extract_cards  # noqa: E402
from noveldb.core.engines.generic import GenericEngine, decode  # noqa: E402

REFUSED = (401, 403, 418, 429, 503)


def query_for(title: str) -> str:
    words = re.findall(r"[\w']+", title)
    if words and re.match(r"[^\x00-\x7f]", words[0]):
        return words[0][:6]  # CJK titles have no spaces: the first characters
    return " ".join(words[:3])


def check_site(site: dict, fetch, chapter: bool = True, scraper=None) -> dict:
    """One site's checks. With `scraper` (an ExternalScraper), pages the plain request cannot read are
    rendered in its browser, and the book is read by the whole engine chain, as a download would."""
    out = {"site": site["site"], "rank": site["rank"], "host": site["host"], "comic": site["comic"],
           "engines": site["engines"]}
    started = time.monotonic()
    home = f"https://{site['host']}/"
    result = fetch(home)
    out["home"] = result["status"] if result else 0
    if not result or result["status"] >= 400:
        out["note"] = "home page refused" if result and result["status"] in REFUSED else "home page did not load"
        return out
    final = result.get("url") or home
    cards = [c for c in extract_cards(decode(result), final) if len(c["title"]) >= 3]
    searcher = SiteSearch(fetch)
    if scraper is not None:
        searcher.render = lambda url: _rendered(scraper, url)
        if not cards:
            rendered = _rendered(scraper, home)
            if rendered:
                cards = [c for c in extract_cards(decode(rendered), home) if len(c["title"]) >= 3]
    if not cards:
        plan = searcher.plan(home)
        for _name, url in list(plan["tags"].items())[:3]:
            page = fetch(url)
            if page and page["status"] < 400:
                cards = [c for c in extract_cards(decode(page), page.get("url") or url) if len(c["title"]) >= 3]
            if cards:
                break
    if not cards:
        out["note"] = "no books found on the home page"
        return out
    book = cards[0]
    out["book"] = {"title": book["title"], "url": book["url"]}
    query = query_for(book["title"])
    found = searcher.search(home, query)
    out["search"] = {"query": query, "results": len(found), "covers": sum(bool(c["cover"]) for c in found),
                     "found_book": any(c["url"].rstrip("/") == book["url"].rstrip("/") for c in found)}
    details = GenericSearch(fetch=fetch, sites=[site], site_search=searcher).details(
        {"url": book["url"], "title": book["title"], "cover": book["cover"], "description": "", "tags": [],
         "author": "", "site": site["site"], "comic": site["comic"], "source": "check", "complete": False})
    out["metadata"] = {"title": details["title"][:80], "author": details.get("author", "")[:40],
                       "cover": bool(details["cover"]), "description": len(details.get("description") or ""),
                       "tags": len(details.get("tags") or [])}
    if chapter and scraper is not None:
        # The whole engine chain, as a download runs it (built-in scrapers, the rule sets, then generic).
        try:
            parsed = scraper.parse_book(book["url"])
        except Exception as e:
            parsed = None
            out["engine_error"] = str(e)[:120]
        out["chapters"] = len(parsed["chapters"]) if parsed else 0
        out["engine"] = (parsed or {}).get("ruleName") or (parsed or {}).get("engine", "")
        if parsed and parsed["chapters"]:
            text = scraper.parse_chapter(0, parsed["chapters"][0], interval=0) or {}
            out["chapter_text"] = len(text.get("contentText") or "")
            out["chapter_images"] = (text.get("contentHtml") or "").count("<img") + len(text.get("images") or [])
    elif chapter:
        engine = GenericEngine(fetch=fetch)
        engine.MAX_LIST_PAGES = 3
        try:
            parsed = engine.parse_book(book["url"])
        except Exception as e:
            parsed = None
            out["engine_error"] = str(e)[:120]
        out["chapters"] = len(parsed["chapters"]) if parsed else 0
        out["engine"] = "Generic"
        if parsed and parsed["chapters"]:
            try:
                text = engine.parse_chapter(parsed["chapters"][0], parsed)
                out["chapter_text"] = len(text["contentText"])
                out["chapter_images"] = text["contentHtml"].count("<img")
            except Exception as e:
                out["chapter_error"] = str(e)[:120]
    out["seconds"] = round(time.monotonic() - started, 1)
    return out


def _rendered(scraper, url: str):
    html = scraper.render_html(url)
    return {"status": 200, "url": url, "text": html, "content": html.encode("utf-8"), "headers": ""} if html else None


def passed(row: dict) -> dict:
    search_ok = bool(row.get("search", {}).get("results"))
    meta = row.get("metadata", {})
    metadata_ok = bool(meta.get("title")) and bool(meta.get("cover"))
    chapter_ok = bool(row.get("chapters")) and (row.get("chapter_text", 0) >= 200 or row.get("chapter_images", 0) > 0)
    return {"search": search_ok, "metadata": metadata_ok, "chapter": chapter_ok}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, help="write the full report (JSON) here")
    parser.add_argument("--sites", help="comma-separated sites to check (default: all)")
    parser.add_argument("--browser", action="store_true", help="recheck refused sites through the app's transport")
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--no-chapters", action="store_true")
    args = parser.parse_args(argv)
    logging.disable(logging.CRITICAL)
    sites = top_sites()
    if args.sites:
        wanted = set(args.sites.split(","))
        sites = [site for site in sites if site["site"] in wanted]
    with ThreadPoolExecutor(args.workers) as pool:
        rows = list(pool.map(lambda site: _safe(check_site, site, default_fetch, not args.no_chapters), sites))
    if args.browser:
        from external_scraper import ExternalScraper

        scraper = ExternalScraper(logger=lambda _m: None)
        try:
            for index, row in enumerate(rows):
                if all(passed(row).values()):
                    continue
                again = _safe(check_site, sites[index], scraper.engine_fetch, not args.no_chapters, scraper)
                print(f"  rechecked {sites[index]['site']}: {passed(again)}", flush=True)
                if sum(passed(again).values()) > sum(passed(row).values()):
                    again["via"] = "browser"
                    rows[index] = again
        finally:
            scraper.cleanup()
    totals = {"search": 0, "metadata": 0, "chapter": 0, "all": 0}
    for row in rows:
        row["passed"] = passed(row)
        for key, value in row["passed"].items():
            totals[key] += value
        totals["all"] += all(row["passed"].values())
        marks = "".join("✓" if row["passed"][k] else "✗" for k in ("search", "metadata", "chapter"))
        print(f"{row['rank']:>6} {row['site']:<28} {marks}  {row.get('note') or row.get('book', {}).get('title', '')[:50]}")
    print(f"\n{len(rows)} sites: search {totals['search']}, metadata+cover {totals['metadata']}, "
          f"chapters {totals['chapter']}, everything {totals['all']}")
    if args.out:
        args.out.write_text(json.dumps({"totals": totals, "sites": rows}, indent=1, ensure_ascii=False), encoding="utf-8")
    return 0


def _safe(function, *args):
    try:
        return function(*args)
    except Exception as e:
        return {"site": args[0]["site"], "rank": args[0]["rank"], "host": args[0]["host"], "note": f"error: {e}"[:200]}


if __name__ == "__main__":
    sys.exit(main())
