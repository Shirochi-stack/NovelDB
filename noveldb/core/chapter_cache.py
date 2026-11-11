"""Incremental download checkpoints stored beside the legacy book cache."""

import json
import sqlite3
from contextlib import closing
from pathlib import Path


class ChapterCache:
    def __init__(self, legacy_path):
        self.path = Path(legacy_path).with_suffix(".sqlite3")

    def entries(self):
        if not self.path.is_file():
            return {}
        with closing(sqlite3.connect(self.path)) as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name = 'entries'").fetchone():
                return {}
            return {
                key: json.loads(value)
                for key, value in db.execute("SELECT key, value FROM entries")
            }

    def put(self, key, value):
        payload = json.dumps(value, ensure_ascii=False)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("PRAGMA synchronous=FULL")
            db.execute("CREATE TABLE IF NOT EXISTS entries (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            db.execute("INSERT OR REPLACE INTO entries VALUES (?, ?)", (str(key), payload))
