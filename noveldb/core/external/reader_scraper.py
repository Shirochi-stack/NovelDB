"""The Catalog reader's own scraper: the downloader's engines on a browser of their own.

The downloader's browser worker runs one job at a time, and a download is one long job, so a book the
reader handed to it waited until the download finished ("Read now" seemed to do nothing). The reader
gets a second worker with this scraper: the same engines and the logins saved by Enter Browser (read
from the downloader's saved sessions), in a browser profile of its own (Chromium lets one browser open
a profile at a time). It never writes those saved sessions back.
"""

from __future__ import annotations

import os

_reader_class = None


def reader_scraper(logger=None):
    """A new reader scraper (the class is made on first use: external_scraper is a large import)."""
    global _reader_class
    if _reader_class is None:
        from external_scraper import ExternalScraper

        class ReaderScraper(ExternalScraper):
            @staticmethod
            def _get_user_data_dir():
                path = os.path.join(ExternalScraper._get_user_data_dir(), "reader_profile")
                os.makedirs(path, exist_ok=True)
                return path

            @classmethod
            def _get_storage_state_path(cls):
                return ExternalScraper._get_storage_state_path()  # the downloader's saved logins

            @classmethod
            def _get_session_cookie_path(cls):
                return ExternalScraper._get_session_cookie_path()

            def _backup_storage_state(self):
                pass  # the downloader's saved logins are only read here

        _reader_class = ReaderScraper
    return _reader_class(logger=logger)
