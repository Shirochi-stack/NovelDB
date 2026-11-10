"""Disk-backed chapter snapshots shared by a Library download and its open reader."""

import base64
import mimetypes
import tempfile
import threading
import uuid
from pathlib import Path


def image_html(html, images):
    for name, data in images:
        if not isinstance(data, bytes):
            continue
        mime = mimetypes.guess_type(name)[0] or "image/jpeg"
        url = "data:" + mime + ";base64," + base64.b64encode(data).decode()
        html = html.replace('"' + name + '"', '"' + url + '"').replace("'" + name + "'", "'" + url + "'")
    return html


class LiveBook:
    def __init__(self, referer=""):
        self.id = uuid.uuid4().hex
        self.folder = tempfile.TemporaryDirectory(prefix="noveldb-live-")
        self.lock = threading.RLock()
        self.chapters = []
        self.states = []
        self.version = 0
        self.active = True
        self.saved = False
        self.saved_offset = None
        self.referer = referer

    def book(self, chapters):
        with self.lock:
            self.version += 1
            self.chapters = [{"key": str(i), "name": str(c.get("title") or c.get("name") or f"Chapter {i + 1}"),
                              "identity": str(c.get("id") or c.get("url") or i)} for i, c in enumerate(chapters)]
            self.states = [{"key": str(i), "state": "pending", "revision": self.version} for i in range(len(chapters))]

    def chapter(self, index, html, images=(), error=""):
        with self.lock:
            if not 0 <= index < len(self.chapters):
                return
            state = "unavailable"
            if html:
                try:
                    Path(self.folder.name, f"{index}.html").write_text(image_html(html, images), encoding="utf-8")
                    state = "ready"
                except OSError:
                    error = "The temporary chapter could not be saved"
            self.version += 1
            self.states[index] = {"key": str(index), "state": state, "error": str(error), "revision": self.version}

    def finish(self, saved=False, spine_count=None):
        with self.lock:
            self.active = False
            self.saved = self.saved or saved
            if spine_count is not None:
                self.saved_offset = max(0, spine_count - len(self.chapters))
            self.version += 1
            for state in self.states:
                if state["state"] == "pending":
                    state.update(state="unavailable", revision=self.version)

    def snapshot(self, session="", after=0, key=None):
        with self.lock:
            if key is not None:
                try:
                    index = int(key)
                    if index < 0 or self.states[index]["state"] != "ready":
                        return {"error": "Chapter is not available"}
                    return {"html": Path(self.folder.name, f"{index}.html").read_text(encoding="utf-8"),
                            "name": self.chapters[index]["name"], "imageReferer": self.referer}
                except (ValueError, IndexError, OSError):
                    return {"error": "Chapter is not available"}
            full = session != self.id or after < 1
            return {"session": self.id, "version": self.version, "active": self.active, "saved": self.saved,
                    "savedOffset": self.saved_offset,
                    "chapters": self.chapters if full and self.chapters else None,
                    "states": [dict(s) for s in self.states if full or s["revision"] > after]}


class LibraryLive:
    def __init__(self):
        self.lock = threading.RLock()
        self.books = {}

    def begin(self, path, referer=""):
        book = LiveBook(referer)
        with self.lock:
            history = self.books.setdefault(Path(path).resolve().as_uri(), [])
            history.append(book)
            if len(history) > 2:
                history.pop(0).folder.cleanup()
        return book

    def query(self, url, session="", after=0, key=None):
        with self.lock:
            books = self.books.get(url, [])
            book = next((b for b in books if b.id == session), None) if key is not None else next(iter(books[-1:]), None)
            return book.snapshot(session, after, key) if book else {}

    def close(self):
        with self.lock:
            for books in self.books.values():
                for book in books:
                    book.folder.cleanup()
            self.books.clear()
