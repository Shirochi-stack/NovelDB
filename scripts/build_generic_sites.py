"""Pick the Catalog's generic-search sites: the 100 most visited sites any site engine knows.

Every site an engine lists (the built-in scrapers, novel-downloader, WebToEpub, LNReader,
lightnovel-crawler, FanFicFare, HaruNeko) is ranked by the Tranco list of the most visited
domains (https://tranco-list.eu, a research ranking that averages several traffic sources). The top
100 go to noveldb/core/catalog/generic_sites.json with what each can do: its own search (an LNReader
plugin or a lightnovel-crawler crawler that searches) or a web search limited to the site. Adult-only
sites are left out of this default list.

    python scripts/build_generic_sites.py                 # downloads the current Tranco list
    python scripts/build_generic_sites.py --tranco top-1m.csv.zip --count 100
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from noveldb.core import engine_updates  # noqa: E402
from noveldb.core.catalog import sites as catalog_sites  # noqa: E402
from noveldb.core.engines.generic import site_of  # noqa: E402

OUT = ROOT / "noveldb" / "core" / "catalog" / "generic_sites.json"
TRANCO = "https://tranco-list.eu/top-1m.csv.zip"
ADULT = re.compile(r"(hentai|porn|xxx|nsfw|sex|erotic|literotica|adult-fanfiction|doujin|nhentai|hitomi|tsumino|"
                   r"pururin|luscious|rule34|gelbooru|danbooru|manytoon|toonily|18comic|wnacg|myreadingmanga|manga18|"
                   r"allhen|yaoi|toongod|toptoon|cool18|semprot|hanime|ecchi|chochox|xlecx|inkbunny|honeytoon|cgbum)", re.I)
# Hosts that are not catalogs of books (APIs, image hosts) and platforms where an engine reads a few
# pages (blogs, documents, galleries, video sites) but whose search would not find books.
NOT_CATALOGS = re.compile(r"^(api|cdn|img|static|assets)\.", re.I)
PLATFORMS = {"google.com", "reddit.com", "github.io", "wordpress.com", "blogspot.com", "vercel.app", "imgur.com",
             "spiegel.de", "fandom.com", "wikia.com", "gamespot.com", "patreon.com", "deviantart.com",
             "crunchyroll.com", "iqiyi.com", "nicovideo.jp", "myanimelist.net", "animepahe.pw", "anime-sama.to",
             "home.blog", "fc2.com", "medium.com", "tumblr.com", "twitter.com", "x.com", "facebook.com",
             "discord.com", "pastebin.com", "netlify.app", "pages.dev", "weebly.com", "wixsite.com",
             "substack.com", "livejournal.com", "dropbox.com", "mega.nz", "telegra.ph", "notion.site",
             "wizards.com", "cmpunjabrashancard.pk"}


# Sites no engine lists that the generic engine reads well, added after the ranked ones. Bookkor
# moves between numbered addresses (each older one sends you to the newest): all of them are its.
EXTRA_SITES = [
    {"site": "bookkor.com", "host": "010.bookkor.com",
     "hosts": ["%03d.bookkor.com" % n for n in range(10, 0, -1)], "comic": False},
]


def _host(url: str) -> str:
    text = str(url or "").strip()
    try:
        host = urlsplit(text if "//" in text else "//" + text).hostname or ""
    except ValueError:
        return ""
    return host.lower().removeprefix("www.")


def engine_hosts() -> dict:
    """{host: {engine: [details]}} for every site an engine lists."""
    found: dict = defaultdict(lambda: defaultdict(list))
    for code, hosts in catalog_sites.BUILTIN_SITES.items():
        for host in hosts:
            found[host.removeprefix("www.")]["native"].append(code)
    for engine in (engine_updates.WEBTOEPUB, engine_updates.NOVEL_DOWNLOADER):
        bundle = engine_updates.current(engine)
        if bundle is None:
            continue
        hosts, _patterns = engine_updates._site_index(engine.name, str(bundle.path), bundle.path.stat().st_mtime)
        for host in hosts:
            found[host.removeprefix("www.")][engine.name].append(host)
    lnr = engine_updates.current(engine_updates.LNREADER)
    if lnr is not None:
        text = lnr.read()
        for match in re.finditer(r'__lnr_define\((\{.*?\}), function', text):
            info = json.loads(match.group(1))
            if _host(info.get("site")):
                found[_host(info["site"])]["lnreader"].append(info["id"])
    lnc = engine_updates.current(engine_updates.LNCRAWL)
    if lnc is not None:
        index = json.loads(zipfile.ZipFile(lnc.path).read("index.json"))
        for crawler in index["crawlers"]:
            if crawler.get("disabled"):
                continue
            for base in crawler["base_urls"]:
                found[_host(base)]["lncrawl"].append(
                    {"file": crawler["file"], "name": crawler["name"], "search": crawler["search"]})
    hn = engine_updates.current(engine_updates.HARUNEKO)
    if hn is not None:
        for site in hn.manifest.get("sites") or []:
            found[_host(site)]["haruneko"].append(site)
    try:
        from fanficfare import adapters

        for section in adapters.getSiteSections():
            if "." in section:
                found[section.removeprefix("www.")]["fanficfare"].append(section)
    except Exception as e:
        print(f"FanFicFare not available: {e}", file=sys.stderr)
    found.pop("", None)
    return found


def tranco_ranks(source: str) -> dict:
    if source.startswith("http"):
        import requests

        data = requests.get(source, timeout=300).content
    else:
        data = Path(source).read_bytes()
    archive = zipfile.ZipFile(io.BytesIO(data))
    ranks = {}
    with archive.open(archive.namelist()[0]) as handle:
        for rank, domain in csv.reader(io.TextIOWrapper(handle, "utf-8")):
            ranks.setdefault(domain.lower(), int(rank))
    return ranks


def build(hosts: dict, ranks: dict, count: int) -> list:
    by_site: dict = defaultdict(lambda: {"hosts": set(), "engines": defaultdict(list)})
    for host, engines in hosts.items():
        site = site_of(host)
        if ADULT.search(host) or NOT_CATALOGS.search(host) or site in PLATFORMS:
            continue
        entry = by_site[site]
        entry["hosts"].add(host)
        for engine, details in engines.items():
            entry["engines"][engine].extend(details)
    ranked = []
    for site, entry in by_site.items():
        rank = min([ranks[h] for h in entry["hosts"] if h in ranks] + [ranks.get(site, 10 ** 9)])
        if rank < 10 ** 9:
            ranked.append((rank, site, entry))
    ranked.sort(key=lambda item: item[0])
    out = []
    for rank, site, entry in ranked[:count]:
        engines = entry["engines"]
        lnr = sorted(set(engines.get("lnreader", [])))
        lnc = [c for c in engines.get("lncrawl", []) if c["search"]]
        hosts_sorted = sorted(entry["hosts"], key=lambda h: (h.count("."), len(h)))
        out.append({
            "site": site,
            "rank": rank,
            "host": hosts_sorted[0],
            "hosts": hosts_sorted,
            "engines": sorted(engines),
            "lnreader": lnr,
            "lncrawl": lnc[:1],
            "search": "lnreader" if lnr else ("lncrawl" if lnc else "web"),
            "comic": bool(engines.get("haruneko")) and not (lnr or engines.get("lncrawl") or engines.get("webtoepub")),
        })
    return out


def extra_sites(listed: set) -> list:
    """EXTRA_SITES not already among the ranked ones, as entries like theirs (ranked after all of them, a web search)."""
    return [dict({"rank": 10 ** 6, "engines": ["generic"], "lnreader": [], "lncrawl": [], "search": "web"}, **extra)
            for extra in EXTRA_SITES if extra["site"] not in listed]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tranco", default=TRANCO, help="the Tranco list (zip) as a file or an address")
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args(argv)
    sites = build(engine_hosts(), tranco_ranks(args.tranco), args.count)
    sites += extra_sites({site["site"] for site in sites})
    data = {"built": datetime.now(timezone.utc).strftime("%Y-%m-%d"), "ranking": "Tranco top 1M", "sites": sites}
    args.out.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    kinds = defaultdict(int)
    for site in sites:
        kinds[site["search"]] += 1
    print(f"{len(sites)} sites -> {args.out} ({dict(kinds)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
