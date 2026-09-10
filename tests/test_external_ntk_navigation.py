import json
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

from external_scraper import ExternalScraper


def test_ntk_headless_index_routes_after_403_and_waits_for_hydration(monkeypatch):
    book_url = "https://sbxh9.com/novel/58410"
    hydrated_index = """
      <main>
        <section class="novel-detail"><h1>Hydrated Novel</h1></section>
        <div class="nd-meta"><span>Fixture Author</span></div>
        <div class="nd-desc">An asynchronously rendered book index.</div>
        <ul class="novel-eps">
          <li data-ep="3"><a href="/novel/58410/303">Episode 3</a></li>
          <li data-ep="2"><a href="/novel/58410/202">Episode 2</a></li>
          <li data-ep="1"><a href="/novel/58410/101">Episode 1</a></li>
        </ul>
      </main>
    """
    homepage = """<html><head><title>Site Home</title></head><body>
      <p>Home</p>
      <script>
      window.next = {router: {push(path) {
        history.pushState({}, '', path);
        document.body.innerHTML = '<p>Loading book...</p>';
        // Deliberately longer than refresh's three-second pause: the index
        // parser must actually wait for chapter rows using Playwright's arg=.
        setTimeout(() => { document.body.innerHTML = HYDRATED_INDEX; }, 4500);
      }}};
      </script>
    </body></html>""".replace("HYDRATED_INDEX", json.dumps(hydrated_index))
    requests = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            context = browser.new_context(service_workers="block")

            def serve(route):
                requests.append(route.request.url)
                parsed = urlsplit(route.request.url)
                if parsed.netloc != "sbxh9.com":
                    route.abort()
                elif parsed.path == "/novel/58410":
                    route.fulfill(
                        status=403, content_type="text/html",
                        body=("<html><body>Access denied. "
                              "This request was blocked by site security policy. "
                              "APP_OR_UNKNOWN_403</body></html>"),
                    )
                elif parsed.path == "/":
                    route.fulfill(
                        status=200, content_type="text/html", body=homepage,
                    )
                else:
                    route.fulfill(status=404, body="Fixture route not found")

            # Every browser request is intercepted, including unexpected URLs.
            context.route("**/*", serve)
            page = context.new_page()
            messages = []
            scraper = ExternalScraper(logger=messages.append)
            scraper._page = page
            scraper._context = context
            monkeypatch.setattr(scraper, "_start_ntk_browser", lambda _url: True)

            assert scraper._ntk_refresh_cloudflare_session(book_url)
            book = scraper._ntk_parse_index_browser(book_url)

            assert book is not None
            assert book["bookname"] == "Hydrated Novel"
            assert book["author"] == "Fixture Author"
            assert book["chapterCount"] == 3
            assert [chapter["name"] for chapter in book["chapters"]] == [
                "Episode 1", "Episode 2", "Episode 3",
            ]
            assert [chapter["number"] for chapter in book["chapters"]] == [1, 2, 3]
            assert page.url == book_url
            assert requests.count(book_url) == 1
            assert requests.count("https://sbxh9.com/") == 1
            assert any("HTTP 403" in message for message in messages)
        finally:
            browser.close()
