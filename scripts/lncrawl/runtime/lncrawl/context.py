"""A stand-in for lightnovel-crawler's application context: what its crawler core reads from `ctx`."""

import logging
from types import SimpleNamespace

from scraper import Scraper


class _Logger:
    is_debug = False
    progress_bar = False  # a flag: no terminal progress bars

    def __init__(self):
        self._log = logging.getLogger("lncrawl")

    def __getattr__(self, name):
        if name == "warn":
            name = "warning"
        return getattr(self._log, name)


class _Scrapers:
    @staticmethod
    def open(origin, parser=None, rate_limit=None, **kwargs):
        return Scraper(origin, parser=parser, rate_limit=rate_limit, **kwargs)


ctx = SimpleNamespace(
    config=SimpleNamespace(crawler=SimpleNamespace(runner_concurrency=2, max_sessions_per_exit=2,
                                                   ignore_images=False)),
    logger=_Logger(),
    scraper=_Scrapers(),
)
