"""The sites' own pages read as book cards (site_search)."""


def test_an_app_pages_listed_books_beat_the_few_it_draws():
    import json as _json

    from noveldb.core.catalog.site_search import extract_cards

    books = [{"id": n, "slug": f"book-{n}", "title": f"Book {n}", "thumbnail": f"cover{n}.jpg", "tags_string": "판타지, #현대"}
             for n in range(10)]
    episodes = [{"id": n, "series_id": 1, "slug": f"1-ep-{n}", "title": f"{n}화"} for n in range(30)]
    data = _json.dumps({"component": "contents/Home", "props": {"comics": {"data": books, "per_page": 40},
                                                                "episodes": {"data": episodes}}})
    page = (f'<html><body><script data-page="app" type="application/json">{data}</script>'
            '<div><a href="/book-0"><img src="/storage/cover0.jpg">Book 0</a>'
            '<a href="/book-1"><img src="/storage/cover1.jpg">Book 1</a></div></body></html>')
    cards = extract_cards(page, "https://novels.example/")
    assert [c["title"] for c in cards] == [f"Book {n}" for n in range(10)]
    assert cards[3] == {"url": "https://novels.example/book-3", "title": "Book 3",
                        "cover": "https://novels.example/storage/cover3.jpg", "snippet": "", "tags": ["판타지", "현대"]}
