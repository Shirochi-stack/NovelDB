"""NovelDB's search index: the client reads only the pieces a search needs (noveldb_index.py)."""

import gzip
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from noveldb.core.catalog.noveldb_index import NovelDBIndex, bucket_of, tag_concept, tag_token, tokens

ROOT = Path(__file__).resolve().parents[1]
BUCKETS = 4096


def deltas(numbers):
    return [n - numbers[i - 1] if i else n for i, n in enumerate(numbers)]


def build(books, page=500, aliases=None):
    """An index in the builder's format (2): per-source lists of each token in pages, then card blocks;
    `aliases`: {alias key: [tag token, ...]} as the builder writes them into the buckets."""
    lists = [dict() for _ in range(BUCKETS)]
    for number, book in enumerate(books):
        own = set(tokens(book["titleEn"])) | set(tokens(book["title"])) | {tag_token(t) for t in book["tags"]}
        for token in own:
            lists[bucket_of(token, BUCKETS)].setdefault(token, {}).setdefault(book["source"], []).append(number)
    data, bucket_at, block_at, pages = b"", [], [], 0
    keys = {}
    for key, tags in (aliases or {}).items():
        keys.setdefault(bucket_of(key, BUCKETS), {})[key] = tags

    def write(value):
        nonlocal data
        piece = gzip.compress(json.dumps(value).encode())
        at = [len(data), len(piece)]
        data += piece
        return at

    for number, bucket in enumerate(lists):
        value = dict(keys.get(number, {}))
        for token, by_source in bucket.items():
            entry = value[token] = {}
            for source, numbers in by_source.items():
                entry[source] = deltas(numbers[:page])
                if len(numbers) > page:
                    first, at = pages, []
                    for start in range(page, len(numbers), page):
                        at.append(write(deltas(numbers[start:start + page])))
                        pages += 1
                    entry["+" + source] = [len(numbers), first, at]
        bucket_at.append(write(value))
    # The builder's rows: 13 fields, then the age rating and the original tags.
    rows = [[b["source"], b["id"], b["title"], b["titleEn"], "", "", b["tags"], 10, 0, 3, 1, b["url"], "", None, b["tags"]]
            for b in books]
    block_at.append(write(rows))
    manifest = {"format": 2, "built": "test", "books": len(books), "buckets": BUCKETS, "block": 256, "page": page,
                "bin": "x.bin", "bucketAt": bucket_at, "blockAt": block_at,
                "sources": {"npia": {"descriptions": {"prefix": "descriptions_shard_", "count": 128}}}}
    return manifest, data


def fetcher(manifest, data, reads):
    def fetch(url, binary=False, headers=None, **_kwargs):
        reads.append(url)
        if url.endswith(".json"):
            return {"status": 200, "content": json.dumps(manifest).encode()}
        if url.endswith(".bin"):
            start, end = (int(x) for x in headers["Range"][len("bytes="):].split("-"))
            return {"status": 206, "content": data[start:end + 1]}
        return {"status": 200, "content": gzip.compress(json.dumps({"7": "A synopsis."}).encode())}
    return fetch


BOOKS = [
    {"source": "npia", "id": "7", "title": "전지적 독자 시점", "titleEn": "Omniscient Reader's Viewpoint",
     "tags": ["Regression", "판타지"], "url": "https://example.test/novel/7"},
    {"source": "npia", "id": "8", "title": "다른 책", "titleEn": "Another Reader", "tags": ["Romance"],
     "url": "https://example.test/novel/8"},
]


def test_a_search_reads_only_its_buckets_and_one_card_block():
    manifest, data = build(BOOKS)
    reads = []
    index = NovelDBIndex(fetcher(manifest, data, reads))
    found = index.search("omniscient reader")
    assert [card["title"] for card in found] == ["Omniscient Reader's Viewpoint"]
    assert found[0]["url"] == "https://example.test/novel/7" and found[0]["source"] == "noveldb"
    # The table of contents, one bucket per word, one card block.
    assert len(reads) == 4
    assert [c["title"] for c in index.search("전지적 독자")] == ["Omniscient Reader's Viewpoint"]
    assert [c["title"] for c in index.search("regression", "tag")] == ["Omniscient Reader's Viewpoint"]
    assert index.search("nothing like this") == []


def test_a_card_gets_its_synopsis_from_the_sites_shard():
    manifest, data = build(BOOKS)
    reads = []
    index = NovelDBIndex(fetcher(manifest, data, reads))
    card = index.search("omniscient")[0]
    assert index.description(card) == "A synopsis."
    assert reads[-1].endswith("descriptions_shard_007.json.gz")


def test_a_server_that_ignores_the_range_gives_nothing():
    manifest, data = build(BOOKS)

    def whole(url, binary=False, headers=None, **_kwargs):
        if url.endswith(".json"):
            return {"status": 200, "content": json.dumps(manifest).encode()}
        return {"status": 200, "content": data}

    assert NovelDBIndex(whole).search("omniscient") == []


@pytest.mark.skipif(shutil.which("node") is None, reason="needs Node")
def test_the_client_tokenizes_like_the_builder():
    titles = ["Solo Leveling: The Return", "전지적 독자 시점", "転生したらスライムだった件", "斗破苍穹", "Ｆｕｌｌ－width ＡＢＣ"]
    script = ("const b=require('./scripts/noveldb_index/build.cjs');"
              f"const t={json.dumps(titles)};"
              "console.log(JSON.stringify(t.map(x=>[b.tokens(x),b.tokens(x).map(b.bucketOf)])))")
    out = subprocess.run(["node", "-e", script], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", check=True)
    expected = json.loads(out.stdout)
    assert [[tokens(t), [bucket_of(k, BUCKETS) for k in tokens(t)]] for t in titles] == expected


@pytest.mark.skipif(shutil.which("node") is None, reason="needs Node")
def test_tag_alias_keys_are_the_builders():
    tags = ["Gender Bender", "Genderbend (TS)", "Genderbending", "[Gender Bender]", "TS", "Harems", "性转", "회귀", "(TS)"]
    script = ("const b=require('./scripts/noveldb_index/build.cjs');"
              f"console.log(JSON.stringify({json.dumps(tags)}.map(b.tagConcept)))")
    out = subprocess.run(["node", "-e", script], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", check=True)
    assert [tag_concept(t) for t in tags] == json.loads(out.stdout)
    assert len({tag_concept(t) for t in tags[:4]}) == 1  # one key for the spellings of one tag


def test_a_tag_search_merges_the_tags_sharing_its_alias_key():
    books = [{"source": "npia", "id": str(n), "title": f"책 {n}", "titleEn": f"Book {n}", "url": f"https://example.test/novel/{n}",
              "tags": [["Genderbend (TS)"], ["Gender Bender"], ["Romance"], ["Genderbend (TS)", "Gender Bender"]][n % 4]}
             for n in range(12)]
    manifest, data = build(books, aliases={"=genderbend": ["#genderbend (ts)", "#gender bender"]})
    found = NovelDBIndex(fetcher(manifest, data, [])).search("Gender Bender", "tag", limit=50)
    assert sorted(int(card["noveldb"]["id"]) for card in found) == [n for n in range(12) if n % 4 != 2]


def test_a_kept_table_of_contents_whose_data_is_gone_is_read_again(tmp_path):
    manifest, data = build(BOOKS)
    old = dict(manifest, bin="old.bin")
    (tmp_path / "noveldb-search.json").write_text(json.dumps(old), encoding="utf-8")  # kept from yesterday
    reads = []

    def fetch(url, binary=False, headers=None, **kwargs):
        if url.endswith("old.bin"):
            reads.append("old")
            return {"status": 404, "content": b""}
        return fetcher(manifest, data, reads)(url, binary=binary, headers=headers, **kwargs)

    index = NovelDBIndex(fetch, cache_dir=tmp_path)
    assert [card["title"] for card in index.search("omniscient")] == ["Omniscient Reader's Viewpoint"]
    assert reads[0] == "old" and json.loads((tmp_path / "noveldb-search.json").read_text())["bin"] == "x.bin"


def many_books():
    """Romance books of three sources, the big one first in number order (as the most read)."""
    books = []
    for i in range(30):
        books.append({"source": "nweb", "id": f"w{i}", "title": "", "titleEn": f"Web romance {i}", "tags": ["Romance"],
                      "url": f"https://example.test/w/{i}"})
    for i in range(7):
        books.append({"source": "npia", "id": f"n{i}", "title": "", "titleEn": f"Npia romance {i}", "tags": ["Romance"],
                      "url": f"https://example.test/n/{i}"})
    for i in range(5):
        books.append({"source": "jara", "id": f"j{i}", "title": "", "titleEn": f"Jara romance {i}", "tags": ["Romance"],
                      "url": f"https://example.test/j/{i}"})
    return books


def test_pages_take_each_source_in_turn_npia_first():
    manifest, data = build(many_books(), page=4)  # small pages: most books are on further pages
    index = NovelDBIndex(fetcher(manifest, data, []))
    first, more = index.search_page("romance", "tag", 0, 6)
    assert [c["noveldb"]["source"] for c in first] == ["npia", "nweb", "jara", "npia", "nweb", "jara"]
    assert more
    seen, page = [], 0
    while True:
        cards, more = index.search_page("romance", "tag", page, 6)
        seen += [c["url"] for c in cards]
        if not more:
            break
        page += 1
    assert len(seen) == len(set(seen)) == 42  # every book once, through the further pages
    # Once the smaller sources run out, the big one fills the pages.
    assert all(url.startswith("https://example.test/w/") for url in seen[-10:])


def test_a_search_of_several_words_matches_within_each_source():
    manifest, data = build(many_books(), page=4)
    index = NovelDBIndex(fetcher(manifest, data, []))
    cards, more = index.search_page("npia romance", "name", 0, 40)
    assert {c["noveldb"]["source"] for c in cards} == {"npia"} and len(cards) == 7 and not more


def test_an_index_of_the_old_format_is_not_read(tmp_path):
    manifest, data = build(BOOKS)
    old = dict(manifest, format=1)
    (tmp_path / "noveldb-search.json").write_text(json.dumps(old), encoding="utf-8")
    reads = []
    index = NovelDBIndex(fetcher(manifest, data, reads), cache_dir=tmp_path)
    assert [c["title"] for c in index.search("omniscient")] == ["Omniscient Reader's Viewpoint"]
    assert reads[0].endswith(".json")  # the kept old table was read again from the server
