import concurrent.futures

from noveldb.core.library_live import LibraryLive
from noveldb.core.events import CallbackEvents
from noveldb.core.external.pipeline import ExternalPipelineMixin


def test_live_metadata_deltas_retries_and_private_paths(tmp_path):
    live = LibraryLive()
    target = tmp_path / "Book.epub"
    book = live.begin(target, "https://example.test/book")
    url = target.resolve().as_uri()
    try:
        preparing = live.query(url)
        assert preparing["version"] == 0 and preparing["chapters"] is None
        book.book([{"id": "one", "title": "One"}, {"id": "two", "title": "Two"}])
        first = live.query(url, book.id, 0)
        assert [c["name"] for c in first["chapters"]] == ["One", "Two"]
        assert all(s["state"] == "pending" for s in first["states"])
        book.chapter(1, "", error="Retrying")
        book.chapter(1, '<div style="white-space:pre-wrap">Two\n\nlines</div>')
        delta = live.query(url, book.id, first["version"])
        assert delta["chapters"] is None
        assert len(delta["states"]) == 1 and delta["states"][0]["state"] == "ready"
        assert "Two\n\nlines" in live.query(url, book.id, key="1")["html"]
        assert live.query(url, book.id, key="1")["imageReferer"] == "https://example.test/book"
        assert live.query(url, book.id, key="../../file").get("error")
        assert live.query((tmp_path / "Other.epub").as_uri()) == {}
        book.finish(saved=True, spine_count=4)
        done = live.query(url, book.id, delta["version"])
        assert done["saved"] and done["savedOffset"] == 2 and not done["active"]
        assert done["states"][0]["state"] == "unavailable"
    finally:
        live.close()


def test_live_chapters_arrive_out_of_order_and_images_are_embedded(tmp_path):
    live = LibraryLive()
    target = tmp_path / "Book.epub"
    book = live.begin(target)
    try:
        book.book([{"title": str(i)} for i in range(20)])
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda i: book.chapter(i, f'<p>{i}</p><img src="images/a.png">', [("images/a.png", b"PNG")]), reversed(range(20))))
        snapshot = live.query(target.as_uri())
        assert all(s["state"] == "ready" for s in snapshot["states"])
        assert 'data:image/png;base64,UE5H' in live.query(target.as_uri(), book.id, key="7")["html"]
        assert live.query(target.as_uri(), book.id, key="7")["html"].startswith("<p>7</p>")
    finally:
        live.close()


def test_external_live_result_uses_the_finished_chapter_without_new_requests():
    from types import SimpleNamespace
    seen = []
    owner = SimpleNamespace(events=CallbackEvents(live_chapter=lambda *args: seen.append(args)))
    ExternalPipelineMixin._publish_live_chapter(owner, 3, {"contentHtml": '<p>A\n\nB</p><img src="https://images.test/a">',
        "images": [{"url": "https://images.test/a", "data": "data:image/png;base64,UE5H"}]})
    assert seen[0][0] == 3 and "A\n\nB" in seen[0][1] and "data:image/png;base64,UE5H" in seen[0][1]
    ExternalPipelineMixin._publish_live_chapter(owner, 4, {"_locked": True})
    assert seen[1][1] == ""
