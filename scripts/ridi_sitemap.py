"""Anonymous sitemap enumeration and public metadata batches for Ridibooks."""
import gzip
import re
from xml.etree import ElementTree

INDEX = "https://ridibooks.com/sitemap.xml"
GRAPHQL = "https://api.ridibooks.com/graphql"
BATCH = 1000
BOOK_MAP = re.compile(r"https://ridibooks\.com/sitemap-books-[1-9][0-9]*\.xml\.gz\Z")
IDS_QUERY = "query($bookIds:[String!]!){books(bookIds:$bookIds){id categories{id parentId} series{id}}}"
DETAIL_QUERY = """query($bookIds:[String!]!){books(bookIds:$bookIds){id categories{id parentId name}
 title{main} authors{name role} introduction{description} isAdultOnly publicationInfo{name}
 ratings{count rating} series{id title totalEpisodeCount isCompleted thumbnail{large}}}}"""


def xml_locations(raw):
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    if len(raw) > 32 * 1024 * 1024 or b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        raise ValueError("Unsafe or oversized Ridibooks sitemap")
    tree = ElementTree.fromstring(raw)
    return [node.text.strip() for node in tree.iter() if node.tag.rsplit("}", 1)[-1] == "loc" and node.text]


def download_locations(client, url):
    response = client.get(url)
    try:
        return xml_locations(response.content)
    finally:
        response.close()


def sitemap_ids(client, url):
    if not BOOK_MAP.fullmatch(url):
        raise ValueError("Unexpected Ridibooks book sitemap")
    ids = []
    for location in download_locations(client, url):
        match = re.fullmatch(r"https://ridibooks\.com/books/([1-9][0-9]*)", location)
        if not match:
            raise ValueError("Unexpected URL in Ridibooks book sitemap")
        ids.append(match[1])
    if not ids or len(set(ids)) != len(ids):
        raise ValueError("Empty or duplicate Ridibooks sitemap IDs")
    return ids


def books(client, ids, *, details=False):
    if not ids:
        return []
    if len(ids) > BATCH or any(not re.fullmatch(r"[1-9][0-9]*", i) for i in ids):
        raise ValueError("Invalid public Ridibooks metadata batch")
    response = client.get_json(GRAPHQL, json_body={"query": DETAIL_QUERY if details else IDS_QUERY,
                                                 "variables": {"bookIds": ids}})
    if not isinstance(response, dict) or response.get("errors"):
        raise ValueError("Ridibooks metadata batch returned GraphQL errors")
    rows = response.get("data", {}).get("books")
    if not isinstance(rows, list):
        raise ValueError("Malformed Ridibooks metadata batch")
    present = [str(row.get("id", "")) for row in rows if isinstance(row, dict)]
    if len(set(present)) != len(present) or not set(present).issubset(ids):
        raise ValueError("Unexpected IDs in Ridibooks metadata batch")
    return [row for row in rows if isinstance(row, dict)]


def categories(book):
    return {str(value) for cat in book.get("categories", []) for value in (cat.get("id"), cat.get("parentId")) if value}


def as_catalog_item(book):
    series = book.get("series") or {}
    return {"book": {"bookId": series.get("id") or book["id"], "title": (book.get("title") or {}).get("main"),
            "authors": book.get("authors"), "categories": book.get("categories"),
            "introduction": book.get("introduction"), "adultsOnly": book.get("isAdultOnly"),
            "publisher": book.get("publicationInfo"), "ratings": book.get("ratings"),
            "serial": {"serialId": series.get("id"), "title": series.get("title"),
                       "total": series.get("totalEpisodeCount"), "completion": series.get("isCompleted"),
                       "cover": series.get("thumbnail")}}}


class SitemapCatalog:
    def restore_catalog(self, state):
        progress = state["progress"]
        snapshot = progress.get("ridi_sitemaps", {})
        self._snapshot_urls = snapshot.get("urls") if snapshot.get("scan_id") == progress.get("scan_id") else None

    def partitions(self, client):
        self._client = client
        self._scan_id = ""
        self._sitemap_ids = {}
        parts = [{"key": f"{category}:sitemap-v1", "tier": "webnovel", "category": category,
                  "start_page": 1} for category in self.categories]
        if client is not None:
            urls = getattr(self, "_snapshot_urls", None)
            if urls is None:
                urls = [url for url in download_locations(client, INDEX) if BOOK_MAP.fullmatch(url)]
            if any(not BOOK_MAP.fullmatch(url) for url in urls):
                raise ValueError("Unexpected Ridibooks sitemap snapshot URL")
            if not urls or len(set(urls)) != len(urls):
                raise ValueError("Missing or duplicate Ridibooks book sitemaps")
            parts.extend({"key": url.rsplit("/", 1)[-1], "tier": "webnovel", "sitemap": url,
                          "start_page": 1} for url in sorted(urls))
        return parts

    def prepare_catalog(self, state, partitions):
        progress = state["progress"]
        self._scan_id = progress.get("scan_id", "")
        snapshots = progress.setdefault("ridi_sitemaps", {})
        if snapshots.get("scan_id") != self._scan_id:
            snapshots.clear()
            snapshots.update(scan_id=self._scan_id, maps={})
        snapshots["urls"] = [part["sitemap"] for part in partitions if "sitemap" in part]
        # Freeze ordering for the scan so a regenerated map cannot skip IDs on resume.
        for part in partitions:
            if "sitemap" in part:
                url = part["sitemap"]
                if url not in snapshots["maps"]:
                    snapshots["maps"][url] = sitemap_ids(self._client, url)
                self._sitemap_ids[url] = snapshots["maps"][url]
        cursors = progress.setdefault("partitions", {})
        keys = {part["key"] for part in partitions}
        for key in list(cursors):
            if key not in keys:
                progress.setdefault("retired_partitions", {})[key] = cursors.pop(key)
        expected = progress.setdefault("ridi_expected", {})
        for category in self.categories:
            response = self._client.get_json("https://api.ridibooks.com/v2/category/books/total-count",
                                            params={"category_id": category, "tab": "books", "platform": "web"})
            total = self._data(response).get("totalCount")
            if not isinstance(total, int) or isinstance(total, bool) or total < 0:
                raise ValueError("Invalid Ridibooks catalog total")
            expected[category] = total
            self._totals[(category, None)] = total
            cursors.setdefault(f"{category}:sitemap-v1", {"next_page": 1, "complete": False})["coverage_complete"] = False
        self._parts = partitions

    def finalize_catalog(self, state):
        progress = state["progress"]
        cursors = progress["partitions"]
        expected = progress.get("ridi_expected", {})
        counts = {category: sum(record.get("ridi_seen_scan") == self._scan_id
                               and category in record.get("ridi_categories", [])
                               for record in state["records"].values()) for category in self.categories}
        audit = {category: {"expected": expected.get(category), "observed": counts[category]}
                 for category in self.categories}
        verified = all(counts[c] == expected.get(c) for c in self.categories)
        state["coverage"].setdefault("catalog", {})["verification"] = audit
        for category in self.categories:
            cursors.setdefault(f"{category}:sitemap-v1", {})["coverage_complete"] = verified
        if not verified and all(cursors.get(p["key"], {}).get("complete") for p in self._parts):
            state["coverage"]["errors"].append({"error": "Ridibooks sitemap/category counts differ; coverage remains partial", "counts": audit})

    def _sitemap_page(self, client, partition, page):
        try:
            from .metadata_common import CatalogPage
        except ImportError:
            from metadata_common import CatalogPage
        ids = self._sitemap_ids[partition["sitemap"]]
        start = (page - 1) * BATCH
        batch = ids[start:start + BATCH]
        minimal = books(client, batch)
        canonical = sorted({str((book.get("series") or {}).get("id") or book["id"])
                            for book in minimal if categories(book) & self.categories.keys()})
        records = []
        for book in books(client, canonical, details=True):
            if not categories(book) & self.categories.keys():
                raise ValueError("Ridibooks canonical metadata changed category during collection")
            record = self._normalize(as_catalog_item(book), "webnovel")
            record["ridi_seen_scan"] = self._scan_id
            records.append(record)
        return CatalogPage(records, page + 1 if start + BATCH < len(ids) else None,
                           observed_total=len(ids), scanned_items=len(batch))
