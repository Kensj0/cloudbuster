"""SQLite state: which Immich asset corresponds to which Nextcloud file."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .planner import Record

SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
    nc_path  TEXT PRIMARY KEY,
    asset_id TEXT NOT NULL,
    sha1     TEXT NOT NULL,
    etag     TEXT
);
CREATE INDEX IF NOT EXISTS records_asset ON records(asset_id);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class State:
    def __init__(self, path: str | Path):
        self.db = sqlite3.connect(str(path))
        self.db.executescript(SCHEMA)
        self.db.commit()

    def records(self) -> list[Record]:
        rows = self.db.execute("SELECT asset_id, nc_path, sha1, etag FROM records").fetchall()
        return [Record(*row) for row in rows]

    def upsert(self, rec: Record) -> None:
        self.db.execute(
            "INSERT INTO records (nc_path, asset_id, sha1, etag) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(nc_path) DO UPDATE SET asset_id=excluded.asset_id, sha1=excluded.sha1, etag=excluded.etag",
            (rec.nc_path, rec.asset_id, rec.sha1, rec.etag),
        )
        self.db.commit()

    def delete(self, nc_path: str) -> None:
        self.db.execute("DELETE FROM records WHERE nc_path = ?", (nc_path,))
        self.db.commit()

    def get_meta(self, key: str) -> str | None:
        row = self.db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self.db.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        self.db.commit()

    def close(self) -> None:
        self.db.close()
