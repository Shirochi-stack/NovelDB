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
        from noveldb.core.settings.model import ENGINE_GENERIC, ENGINE_HN

        class ReaderScraper(ExternalScraper):
            def parse_chapter(self, index, chapter_info, interval=0, *, retry_images=False, **kwargs):
                book = self._book_data or {}
                script = self._script_for_book()
                primary = script.engine if script else self._python_engine_for_book()
                alternate = ENGINE_GENERIC in self.engine_order and primary not in (None, ENGINE_GENERIC)
                haruneko = primary == ENGINE_HN
                if alternate and haruneko and not retry_images:
                    # A chapter with an explicit list of ordinary image URLs can use the reader's
                    # parallel loader. Other layouts go through the site's HaruNeko plugin.
                    result = self._reader_generic(chapter_info, book, comic=True, listed_only=True)
                    if result:
                        return result
                result = super().parse_chapter(index, chapter_info, interval=interval, **kwargs)
                if result or self._stop_requested or not alternate or retry_images:
                    return result
                note = "Chapter engine failed; trying Generic fallback for the same chapter…"
                self.log(note)
                if self.reader_progress:
                    self.reader_progress(note)
                return self._reader_generic(chapter_info, book, comic=haruneko) or {
                    "_readerError": "The chapter could not be read after trying the available engines.",
                }

            def _reader_generic(self, chapter, book, *, comic, listed_only=False):
                try:
                    result = self._python_engine(ENGINE_GENERIC).parse_chapter(chapter, book, listed_only=listed_only)
                except Exception as error:
                    self.log(f"  Generic fallback: {error}")
                    return None
                return result if result and (not comic or result.get("comic")) else None

            reader_progress = None

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
