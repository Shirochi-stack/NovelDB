"""A stand-in for lncrawl-scraper, the HTTP layer lightnovel-crawler's sources are written against.

NovelDB makes every request itself: `Scraper.fetcher` is set by the app to its own transport (the
app's HTTP client with the browser's cookies, and the browser itself when a site refuses that). Only
the parts of the scraper's interface the sources use are here; `PageSoup` is the original wrapper.
"""

from .exceptions import Aborted, Blocked, Poisoned, ScraperError
from .session import Diagnosis, Scraper, extract_base
from .soup import PageSoup

__all__ = ["Aborted", "Blocked", "Diagnosis", "PageSoup", "Poisoned", "Scraper", "ScraperError", "extract_base"]
