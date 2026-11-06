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


def build(books, page=500, aliases=None, tag_list=None):
    """An index in the builder's format (2): per-source lists of each token in pages, then card blocks;
    `aliases`: {alias key: [tag token, ...]} as the builder writes them into the buckets."""
    lists = [dict() for _ in range(BUCKETS)]
    for number, book in enumerate(books):
        own = set(tokens(book["titleEn"])) | set(tokens(book["title"])) | {tag_token(t) for t in book["tags"]}
        own.add("!" + book["source"])
        for token in own:
            lists[bucket_of(token, BUCKETS)].setdefault(token, {}).setdefault(book["source"], []).append(number)
        for other in book.get("also", []):  # a merged book is in its other sources' Top lists too
            lists[bucket_of("!" + other[0], BUCKETS)].setdefault("!" + other[0], {}).setdefault(other[0], []).append(number)
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
    # The builder's rows: 13 fields, then the age rating, the original tags and the publication date.
    rows = [[b["source"], b["id"], b["title"], b["titleEn"], "", "", b["tags"], 10, 0, 3, 1, b["url"], "", None, b["tags"],
             b.get("published", ""), b.get("also", [])] + ([b["rating"]] if "rating" in b else []) for b in books]
    block_at.append(write(rows))
    manifest = {"format": 2, "built": "test", "books": len(books), "buckets": BUCKETS, "block": 256, "page": page,
                "bin": "x.bin", "bucketAt": bucket_at, "blockAt": block_at,
                "sources": {"npia": {"descriptions": {"prefix": "descriptions_shard_", "count": 128}}}}
    if tag_list is not None:
        manifest["tagsAt"] = write(tag_list)
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


def test_a_long_title_reads_only_its_shortest_word_lists():
    """A long Chinese title: its common pieces' long lists (here "生活" in 60 titles, on 15 pages) are
    not read page by page; the four shortest lists find the book."""
    books = [{"source": "sfc", "id": f"s{i}", "title": f"某人{i}的生活", "titleEn": "", "tags": [],
              "url": f"https://example.test/s/{i}"} for i in range(60)]
    books.append({"source": "sfc", "id": "elf", "title": "萌妹精灵与她的幸福生活", "titleEn": "Cute Elf and Her Happy Life",
                  "tags": [], "url": "https://example.test/s/elf"})
    manifest, data = build(books, page=4)
    reads = []
    index = NovelDBIndex(fetcher(manifest, data, reads))
    cards, _more = index.search_page("萌妹精灵与她的幸福生活", "name", 0, 40)
    assert [c["url"] for c in cards] == ["https://example.test/s/elf"]
    pages = sum(1 for url in reads if url.endswith(".bin")) - len(set(tokens("萌妹精灵与她的幸福生活"))) - 1
    assert pages == 0  # buckets and the card block only: no list page of "生活" (or of any piece)


def test_a_card_has_its_publication_date_and_a_tag_its_names_on_each_site():
    books = [dict(BOOKS[0], published="2021-01-18")] + BOOKS[1:]
    tag_list = [["Gender Bender", 40, {"npia": ["TS"], "sfc": ["性转", "娘化"]}], ["Romance", 9, {}], ["Old entry", 3]]
    manifest, data = build(books, tag_list=tag_list)
    index = NovelDBIndex(fetcher(manifest, data, []))
    assert index.search("omniscient")[0]["published"] == "2021-01-18"
    assert index.search("another reader")[0]["published"] == ""
    assert index.native_tags("gender bender") == {"npia": ["TS"], "sfc": ["性转", "娘化"]}
    # Another spelling of the same tag (its alias key) finds it too; a tag it doesn't list has none.
    assert index.native_tags("Gender-Bender")["npia"] == ["TS"]
    assert index.native_tags("Isekai") == {}
    assert index.tag_names() == ["Gender Bender", "Romance", "Old entry"]


def test_the_builder_merges_one_book_on_two_sources():
    """The same original title and author (after marks like ™) on two sources: one book, the fuller entry
    leading with the other's chapters, tags and English title; a short title by another author stays apart."""
    script = r"""
const {mergeSame} = require('./scripts/noveldb_index/build.cjs');
const book = (o) => Object.assign({tags: [], originalTags: [], views: 0, likes: 0, chapters: 0, titleEn: '', cover: '',
    complete: null, age: null, updated: '', published: '', url: 'https://example.test/' + o.source + '/' + o.id}, o);
const out = mergeSame([
    book({source: 'mpia', id: '573666', title: '군필 여대생의 게임방송', author: '안현™', views: 655161, chapters: 123,
          tags: ['Modern Fantasy', 'Game'], originalTags: ['현대판타지', '게임'], titleEn: 'A Military-Trained College Girl', updated: '2026-10-05'}),
    book({source: 'nseries', id: '14672427', title: '군필 여대생의 게임방송', author: '안현', chapters: 134,
          tags: ['Modern Fantasy', 'Web Novel'], originalTags: ['현판', '웹소설'], titleEn: 'A Military-Veteran College Girl', updated: '2026-10-23'}),
    book({source: 'jara', id: '1', title: '꿈', author: '박정훈'}),
    book({source: 'nweb', id: '2', title: '꿈', author: '아무'}),
]);
console.log(JSON.stringify(out));
"""
    out = json.loads(subprocess.run(["node", "-e", script], cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                                    check=True).stdout)
    assert out["merged"] == 1
    lead = out["books"][0]
    assert [b["source"] for b in out["books"]] == ["mpia", "jara", "nweb"]
    assert lead["also"] == [["nseries", "14672427", "https://example.test/nseries/14672427"]]
    assert lead["titleEn"] == "A Military-Trained College Girl" and lead["otherTitles"] == ["A Military-Veteran College Girl"]
    assert lead["views"] == 655161 and lead["chapters"] == 134 and lead["updated"] == "2026-10-23"
    assert lead["tags"] == ["Modern Fantasy", "Game", "Web Novel"] and lead["originalTags"][-1] == "웹소설"


def test_a_merged_card_lists_its_other_sources_and_shows_once():
    other = ["nseries", "14672427", "https://example.test/nseries/14672427"]
    books = [dict(BOOKS[0], also=[other]), BOOKS[1]]
    manifest, data = build(books)
    index = NovelDBIndex(fetcher(manifest, data, []))
    card = index.search("omniscient")[0]
    assert card["also"] == [{"url": other[2], "noveldb": {"source": "nseries", "id": "14672427"}}]
    assert index.search("another reader")[0]["also"] == []
    # Both sources' Top lists have it: asked for both, it shows once.
    cards, _more = index.search_page("npia,nseries", "source", 0)
    assert [c["url"] for c in cards] == ["https://example.test/novel/7", "https://example.test/novel/8"]


def test_cards_carry_the_age_rating_and_r19_is_a_tag():
    # The builder gives an adults-only book the "R19" tag and its rating (19) as the card's last field.
    books = [dict(BOOKS[0], tags=["Romance", "R19"], rating=19), dict(BOOKS[1], tags=["Romance", "R15"], rating=15),
             {"source": "kpage", "id": "9", "title": "옛 책", "titleEn": "Old Book", "tags": ["Romance"],
              "url": "https://example.test/novel/9"}]
    manifest, data = build(books)
    index = NovelDBIndex(fetcher(manifest, data, []))
    assert {c["title"]: c["ageRating"] for c in index.search("romance", "tag")} == {
        "Omniscient Reader's Viewpoint": 19, "Another Reader": 15, "Old Book": 0}  # an older card: no rating
    assert [c["title"] for c in index.search("R19", "tag")] == ["Omniscient Reader's Viewpoint"]
    # A book its site tagged "R19" but the site's own rule doesn't rate so: not an adults-only result.
    manifest, data = build(books + [{"source": "jara", "id": "5", "title": "태그만", "titleEn": "Tagged Only",
                                      "tags": ["R19"], "url": "https://example.test/novel/5", "rating": 0}])
    index = NovelDBIndex(fetcher(manifest, data, []))
    assert [c["title"] for c in index.search("R19", "tag")] == ["Omniscient Reader's Viewpoint"]
    assert [c["title"] for c in index.search("R15", "tag")] == ["Another Reader"]


def test_the_builder_rates_books_by_the_sites_own_rule():
    script = ("const b=require('./scripts/noveldb_index/build.cjs');"
              "console.log(JSON.stringify([{source:'npia',age:19},{source:'npia',age:15},{source:'sfc',age:19},"
              "{source:'kpage',age:19},{source:'nweb',age:19,metadataV1:true},{source:'jara',age:15,metadataV1:true},"
              "{source:'npia',age:0},{source:'mpia',age:null,metadataV1:true}].map(b.ratingOf)))")
    out = subprocess.run(["node", "-e", script], cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True,
                         check=True).stdout
    # sfc's old "19" is a teen rating on the site, as metadata-core reads it.
    assert json.loads(out) == [19, 15, 15, 19, 19, 15, 0, 0]


def test_a_sorted_r19_search_reads_the_indexs_list_in_that_order():
    # The builder keeps R19's books in the catalogue's other orders too ("#r19|new": the newest first).
    older = dict(BOOKS[0], tags=["R19"], rating=19)
    newer = dict(BOOKS[1], tags=["R19", "R19|new"], rating=19)
    first = dict(older, tags=["R19", "R19|new"])
    manifest, data = build([newer, first])  # "#r19|new" lists the newer book first
    index = NovelDBIndex(fetcher(manifest, data, []))
    assert [c["title"] for c in index.search("R19", "tag", sort="new")][0] == "Another Reader"
    # A sort the index has no list for, or none: the index's own order.
    assert {c["title"] for c in index.search("R19", "tag", sort="nonsense")} == {"Another Reader", "Omniscient Reader's Viewpoint"}
