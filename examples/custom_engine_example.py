"""An example custom site engine for NovelDB.

Add it with Settings > Site engines > Add custom engine (desktop or Android). It then appears in the
engine list, before the Generic fallback, and can be reordered or switched off like the others.

The class must be called ``Engine``. ``label`` and ``sites`` are read from the file without running
it, so they must be plain values. The same file runs on Android in the app's Python (Pyodide): use
``self.fetch`` for requests (it goes through the app: saved logins, retries), and the standard
library, BeautifulSoup, lxml or regex for the rest.
"""

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup


class Engine:
    label = "Example Novels"
    sites = ["novels.example"]  # a link on one of these (or a subdomain) is offered to this engine first

    def __init__(self, fetch, log, render=None):
        self.fetch = fetch      # fetch(url, method="GET", data=None, headers=None, binary=False, referer="")
        self.log = log          # log("text") writes to the download log
        self.render = render    # render(url) -> the page as a browser draws it (desktop only; may be None)

    def knows(self, url):
        """Optional: which links this engine reads (by default, the ones on `sites`)."""
        return "/book/" in url

    def _page(self, url, referer=""):
        result = self.fetch(url, referer=referer)
        if not result or result["status"] >= 400:
            raise RuntimeError(f"could not load {url} ({result['status'] if result else 'no answer'})")
        return BeautifulSoup(result["text"], "html.parser")

    def parse_book(self, url):
        soup = self._page(url)
        title = soup.select_one("h1")
        author = soup.select_one(".author")
        cover = soup.select_one(".cover img")
        chapters = [{"url": urljoin(url, a["href"]), "name": a.get_text(strip=True)}
                    for a in soup.select(".chapter-list a[href]")]
        return {
            "bookname": title.get_text(strip=True) if title else "",
            "author": author.get_text(strip=True) if author else "",
            "coverUrl": urljoin(url, cover["src"]) if cover and cover.get("src") else "",
            "introduction": (soup.select_one(".synopsis") or soup.new_tag("p")).get_text("\n", strip=True),
            "tags": [t.get_text(strip=True) for t in soup.select(".tags a")],
            "chapters": chapters,
        }

    def parse_chapter(self, chapter, book):
        soup = self._page(chapter["url"], referer=book.get("bookUrl", ""))
        body = soup.select_one("#chapter-content")
        if body is None:
            raise RuntimeError("no chapter text on the page")
        paragraphs = [p.get_text(" ", strip=True) for p in body.select("p")] or \
            [line.strip() for line in body.get_text("\n").splitlines()]
        text = "\n".join(p for p in paragraphs if p and not re.match(r"^(advertisement|ad)$", p, re.I))
        # contentHtml may be left out: it is made from contentText.
        return {"chapterName": chapter.get("name", ""), "contentText": text}
