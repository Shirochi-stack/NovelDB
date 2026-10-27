"""Links collected from the Catalog page, like a shopping cart; the Download page queues them.

Stored as `link-cart.json`, a list of {url, title, source} - the mobile `LinkCart` format.
"""

from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass
from typing import Callable, Iterable, Optional


@dataclass(frozen=True)
class CartItem:
    url: str
    title: str = ""
    source: str = ""


class LinkCart:
    def __init__(self, path: Optional[str] = None, on_change: Optional[Callable[[], None]] = None) -> None:
        self._path = path
        self._lock = threading.Lock()
        self.on_change = on_change
        self._items: list[CartItem] = self._load()

    @property
    def items(self) -> list[CartItem]:
        with self._lock:
            return list(self._items)

    @property
    def urls(self) -> list[str]:
        return [item.url for item in self.items]

    def __len__(self) -> int:
        return len(self._items)

    def __contains__(self, url: str) -> bool:
        return any(item.url == url for item in self.items)

    def add(self, item: CartItem) -> bool:
        """False when the link is already in the cart."""
        with self._lock:
            if any(existing.url == item.url for existing in self._items):
                return False
            self._items.append(item)
        self._changed()
        return True

    def remove(self, url: str) -> None:
        self.remove_all([url])

    def remove_all(self, urls: Iterable[str]) -> None:
        drop = set(urls)
        with self._lock:
            before = len(self._items)
            self._items = [item for item in self._items if item.url not in drop]
            changed = len(self._items) != before
        if changed:
            self._changed()

    def clear(self) -> None:
        with self._lock:
            had = bool(self._items)
            self._items = []
        if had:
            self._changed()

    def _load(self) -> list[CartItem]:
        if not self._path:
            return []
        try:
            with open(self._path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            return []
        items = []
        for entry in data if isinstance(data, list) else []:
            if isinstance(entry, dict) and isinstance(entry.get("url"), str) and entry["url"]:
                items.append(CartItem(entry["url"], str(entry.get("title") or ""), str(entry.get("source") or "")))
        return items

    def _changed(self) -> None:
        if self._path:
            from noveldb.core.settings.store import write_raw

            try:
                write_raw([asdict(item) for item in self.items], self._path)
            except OSError:
                pass
        if self.on_change is not None:
            self.on_change()
