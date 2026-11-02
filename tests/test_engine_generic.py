"""The generic fallback engine on fixture pages: metadata, chapter lists, chapter text."""
from noveldb.core.engines.generic import (
    GenericEngine,
    decode,
    extract_content,
    extract_metadata,
    find_chapters,
    shape,
    soup_of,
)

BOOK_PAGE = """<html><head>
<title>The Wandering Sword Novel - Read Online Free - Example Novels</title>
<meta property="og:title" content="The Wandering Sword">
<meta property="og:image" content="/covers/sword.jpg">
<meta property="og:site_name" content="Example Novels">
<script type="application/ld+json">{"@type": "Book", "name": "The Wandering Sword",
 "author": {"@type": "Person", "name": "Lan Yu"}, "genre": ["Wuxia", "Action"]}</script>
</head><body>
<nav><a href="/">Home</a><a href="/genre/romance">Romance</a><a href="/latest">Latest</a></nav>
<div class="desc">A swordsman walks the world. He looks for the man who burned his village.</div>
<a href="/book/wandering-sword/chapter-1">Read first</a>
<ul class="chapter-list">
  <li><a href="/book/wandering-sword/chapter-3-the-gate">Chapter 3: The Gate</a></li>
  <li><a href="/book/wandering-sword/chapter-2-rain">Chapter 2: Rain</a></li>
  <li><a href="/book/wandering-sword/chapter-1">Chapter 1: Ashes</a></li>
</ul>
<div class="pagination"><a href="/book/wandering-sword?page=2">2</a></div>
<footer><a href="/about">About</a><a href="/contact">Contact</a></footer>
</body></html>"""

PAGE_TWO = """<html><body><ul>
<li><a href="/book/wandering-sword/chapter-5">Chapter 5</a></li>
<li><a href="/book/wandering-sword/chapter-4">Chapter 4</a></li>
</ul></body></html>"""

CHAPTER = """<html><body>
<header><a href="/">Example Novels</a></header>
<div class="sidebar"><a href="/x">Popular</a> <a href="/y">More</a></div>
<div id="chapter-content">
  <h2>Chapter 1: Ashes</h2>
  <p>The village was still burning when he woke.</p>
  <p>He picked up the sword his father left behind and walked into the smoke.</p>
  <p>Nobody followed him. Nobody was left to follow.</p>
  <script>ads()</script>
  <div class="ads">Buy now</div>
  <a href="/book/wandering-sword/chapter-2-rain">Next Chapter</a>
</div>
<div class="comments"><p>Great chapter!!! I love it so much, first comment here, see everyone tomorrow.</p></div>
</body></html>"""


def page(html, url, status=200):
    return {"status": status, "url": url, "content": html.encode("utf-8"), "text": html,
            "headers": "Content-Type: text/html; charset=utf-8"}


def test_metadata_prefers_structured_data_and_drops_the_site_name():
    meta = extract_metadata(BOOK_PAGE, "https://novels.example/book/wandering-sword")
    assert meta["title"] == "The Wandering Sword"
    assert meta["author"] == "Lan Yu"
    assert meta["cover"] == "https://novels.example/covers/sword.jpg"
    assert "Wuxia" in meta["tags"] and "Romance" in meta["tags"]
    assert meta["description"].startswith("A swordsman walks")


def test_chapter_links_are_found_by_their_shape_and_put_in_reading_order():
    chapters = find_chapters(soup_of(BOOK_PAGE), "https://novels.example/book/wandering-sword")
    assert [c["url"].rsplit("/", 1)[-1] for c in chapters] == ["chapter-1", "chapter-2-rain", "chapter-3-the-gate"]
    # The same chapter linked as "Read first" keeps its chapter-like name.
    assert chapters[0]["name"] == "Chapter 1: Ashes"
    assert shape("https://a.example/b/chapter-12-x") == shape("https://a.example/b/chapter-9-y")


def test_chapter_text_is_the_main_block_without_navigation_ads_or_comments():
    html, text = extract_content(CHAPTER, "https://novels.example/book/wandering-sword/chapter-1", "Chapter 1: Ashes")
    assert text.splitlines()[0] == "The village was still burning when he woke."
    assert "Buy now" not in text and "Next Chapter" not in text and "Great chapter" not in text
    assert "<script" not in html and "Chapter 1: Ashes" not in text


def test_the_engine_follows_list_pages_and_reads_chapters():
    pages = {
        "https://novels.example/book/wandering-sword": BOOK_PAGE,
        "https://novels.example/book/wandering-sword?page=2": PAGE_TWO,
        "https://novels.example/book/wandering-sword/chapter-1": CHAPTER,
    }
    asked = []

    def fetch(url, **_options):
        asked.append(url)
        return page(pages[url], url) if url in pages else page("", url, 404)

    engine = GenericEngine(fetch=fetch)
    book = engine.parse_book("https://novels.example/book/wandering-sword")
    assert book["bookname"] == "The Wandering Sword" and book["author"] == "Lan Yu"
    assert [c["name"] for c in book["chapters"]][-1] == "Chapter 5"
    assert len(book["chapters"]) == 5
    chapter = engine.parse_chapter(book["chapters"][0], book)
    assert chapter["contentText"].startswith("The village was still burning")


def test_a_chapter_without_a_list_is_followed_through_next_links():
    chain = {
        "https://n.example/s/1": '<html><body><h1>Part 1</h1><div class="content"><p>' + "word " * 50 +
                                 '</p></div><a rel="next" href="/s/2">Next</a></body></html>',
        "https://n.example/s/2": '<html><body><h1>Part 2</h1><div class="content"><p>' + "word " * 50 +
                                 '</p></div><a href="/s/3">Next chapter</a></body></html>',
        "https://n.example/s/3": '<html><body><h1>Part 3</h1><div class="content"><p>end</p></div></body></html>',
    }
    engine = GenericEngine(fetch=lambda url, **_o: page(chain[url], url))
    book = engine.parse_book("https://n.example/s/1")
    assert [c["name"] for c in book["chapters"]] == ["Part 1", "Part 2", "Part 3"]


def test_pages_are_decoded_in_their_declared_charset():
    gbk = "<html><head><meta charset=gbk></head><body>第一章 开始</body></html>".encode("gbk")
    assert "第一章" in decode({"content": gbk, "headers": ""})


def test_list_names_lose_their_age_and_repeated_number():
    from noveldb.core.engines.generic import clean_name

    assert clean_name("14 14 Nano Tech 1281 days ago") == "14 Nano Tech"
    assert clean_name("Read Chapter 1") == "Chapter 1"
    assert clean_name("Novel Chapters Chapter 1281: Epilogue Updated 3 years ago") == "Chapter 1281: Epilogue"


def test_list_pages_named_in_a_query_are_followed_and_numbers_come_from_the_addresses():
    book = "https://n.example/novel/my-book.html"
    first = ('<html><body><div class="latest"><a href="/novel/my-book_9.html">Chapter 9</a></div><ul>'
             + "".join(f'<li><a href="/novel/my-book_{n}.html">{n} {n} Tech talk {n} days ago</a></li>' for n in (1, 2, 3))
             + '</ul><div class="pagination"><a href="/e/fy.php?page=1&amp;wjm=my-book">2</a></div></body></html>')
    second = ('<html><body><ul>' + "".join(f'<li><a href="/novel/my-book_{n}.html">{n} {n} Part {n}</a></li>' for n in range(4, 10))
              + '</ul></body></html>')
    pages = {book: first, "https://n.example/e/fy.php?page=1&wjm=my-book": second}
    engine = GenericEngine(fetch=lambda url, **_o: page(pages[url], url) if url in pages else page("", url, 404))
    names = [c["name"] for c in engine.parse_book(book)["chapters"]]
    assert len(names) == 9 and names[0] == "1 Tech talk" and names[-1] == "9 Part 9"
