"""NovelDB's search index: the client reads only the pieces a search needs (noveldb_index.py)."""

import gzip
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from noveldb.core.catalog.noveldb_index import NovelDBIndex, bucket_of, tag_token, tokens

ROOT = Path(__file__).resolve().parents[1]
BUCKETS = 4096


def build(books):
    """An index in the builder's format: buckets of postings, then card blocks."""
    buckets = [dict() for _ in range(BUCKETS)]
    for number, book in enumerate(books):
        own = set(tokens(book["titleEn"])) | set(tokens(book["title"])) | {tag_token(t) for t in book["tags"]}
        for token in own:
            buckets[bucket_of(token, BUCKETS)].setdefault(token, []).append(number)
    data, bucket_at, block_at = b"", [], []
    for bucket in buckets:
        piece = gzip.compress(json.dumps(bucket).encode())
        bucket_at.append([len(data), len(piece)])
        data += piece
    rows = [[b["source"], b["id"], b["title"], b["titleEn"], "", "", b["tags"], 10, 0, 3, 1, b["url"], ""] for b in books]
    piece = gzip.compress(json.dumps(rows).encode())
    block_at.append([len(data), len(piece)])
    data += piece
    manifest = {"format": 1, "built": "test", "books": len(books), "buckets": BUCKETS, "block": 256, "bin": "x.bin",
                "bucketAt": bucket_at, "blockAt": block_at,
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
