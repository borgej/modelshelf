"""SQLite library catalogue.

One row per file. Scan results and the user's own data (status, favourite,
tags, notes) live in the same row; when a file disappears and a file with the
same content hash turns up elsewhere in the same scan, the user data moves
with it — so reorganising folders doesn't lose your tags.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from dataclasses import dataclass, field

from core.settings import data_dir

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY,
    path TEXT UNIQUE NOT NULL,
    root TEXT, name TEXT, ext TEXT, kind TEXT,
    size INTEGER, mtime REAL, sha256 TEXT,
    dim_x REAL, dim_y REAL, dim_z REAL, volume REAL, area REAL,
    triangles INTEGER, parts INTEGER,
    colors TEXT, designer TEXT, title TEXT, photos TEXT, note TEXT,
    sliced_weight REAL, print_time REAL,
    thumb TEXT, thumb_source TEXT, error TEXT,
    analyzer INTEGER DEFAULT 0, added_at REAL, analyzed_at REAL,
    status TEXT DEFAULT '', favorite INTEGER DEFAULT 0, tags TEXT DEFAULT '[]',
    notes TEXT DEFAULT '',
    hidden INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_files_sha ON files(sha256);
CREATE TABLE IF NOT EXISTS dup_ignore (sha256 TEXT PRIMARY KEY);
"""

USER_FIELDS = ("status", "favorite", "tags", "notes")


@dataclass
class Item:
    id: int
    path: str
    root: str
    name: str
    ext: str
    kind: str
    size: int
    mtime: float
    sha256: str = ""
    dim_x: float | None = None
    dim_y: float | None = None
    dim_z: float | None = None
    volume: float | None = None
    area: float | None = None
    triangles: int | None = None
    parts: int | None = None
    colors: list[str] = field(default_factory=list)
    designer: str = ""
    title: str = ""
    photos: list[str] = field(default_factory=list)
    note: str = ""
    sliced_weight: float | None = None
    print_time: float | None = None
    thumb: str = ""
    thumb_source: str = ""
    error: str = ""
    analyzer: int = 0
    added_at: float = 0.0
    analyzed_at: float = 0.0
    status: str = ""
    favorite: bool = False
    tags: list[str] = field(default_factory=list)
    notes: str = ""
    # Derived, not stored
    folder: str = ""
    search_blob: str = ""

    def finish(self) -> "Item":
        self.folder = os.path.dirname(self.path)
        self.search_blob = " ".join(
            [self.name, self.title or "", self.designer or "", self.folder, " ".join(self.tags),
             self.notes or "", self.kind]
        ).lower()
        return self

    @property
    def dims(self) -> tuple[float, float, float] | None:
        if self.dim_x is None:
            return None
        return self.dim_x, self.dim_y, self.dim_z


_JSON_COLS = {"colors", "photos", "tags"}


def _row_to_item(row: sqlite3.Row) -> Item:
    d = dict(row)
    for k in _JSON_COLS:
        try:
            d[k] = json.loads(d.get(k) or "[]")
        except ValueError:
            d[k] = []
    d["favorite"] = bool(d.get("favorite"))
    d.pop("hidden", None)
    for k in ("designer", "title", "note", "thumb", "thumb_source", "error", "status", "notes", "sha256"):
        d[k] = d.get(k) or ""
    return Item(**d).finish()


class Library:
    def __init__(self, path: str | None = None):
        self.path = path or str(data_dir() / "library.sqlite")
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.executescript(SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(files)")}
        if "hidden" not in cols:
            self.conn.execute("ALTER TABLE files ADD COLUMN hidden INTEGER DEFAULT 0")
            # Archives analysed by 0.1.x that turned out to hold no models.
            self.conn.execute("UPDATE files SET hidden=1 WHERE kind='ZIP' AND note LIKE '0 model file%'")
            self.conn.commit()

    def close(self) -> None:
        with self._lock:
            self.conn.close()

    # ---------- reading ----------
    def all_items(self) -> list[Item]:
        with self._lock:
            # Hidden rows (e.g. zips without models) stay in the table so rescans
            # know they were already checked, but are never shown.
            return [_row_to_item(r) for r in self.conn.execute("SELECT * FROM files WHERE hidden=0")]

    def get(self, item_id: int) -> Item | None:
        with self._lock:
            r = self.conn.execute("SELECT * FROM files WHERE id=? AND hidden=0", (item_id,)).fetchone()
        return _row_to_item(r) if r else None

    def by_path(self, path: str) -> Item | None:
        with self._lock:
            r = self.conn.execute("SELECT * FROM files WHERE path=?", (path,)).fetchone()
        return _row_to_item(r) if r else None

    def fingerprints(self) -> dict[str, tuple[int, float, int, int]]:
        """path -> (size, mtime, analyzer version, id)"""
        with self._lock:
            return {r[0]: (r[1], r[2], r[3], r[4]) for r in self.conn.execute(
                "SELECT path, size, mtime, analyzer, id FROM files")}

    def ignored_duplicates(self) -> set[str]:
        with self._lock:
            return {r[0] for r in self.conn.execute("SELECT sha256 FROM dup_ignore")}

    # ---------- writing scan results ----------
    def upsert_analysis(self, rec: dict, root: str, analyzer: int, thumb: str) -> int:
        path = rec["path"]
        name = os.path.basename(path)
        ext = os.path.splitext(name)[1].lower()
        from core.formats import KIND_BY_EXT
        vals = {
            "path": path, "root": root, "name": name, "ext": ext,
            "kind": KIND_BY_EXT.get(ext, ext.upper().lstrip(".")),
            "size": rec.get("size"), "mtime": rec.get("mtime"), "sha256": rec.get("sha256") or "",
            "dim_x": rec.get("dim_x"), "dim_y": rec.get("dim_y"), "dim_z": rec.get("dim_z"),
            "volume": rec.get("volume"), "area": rec.get("area"),
            "triangles": rec.get("triangles"), "parts": rec.get("parts"),
            "colors": json.dumps(rec.get("colors") or []),
            "designer": rec.get("designer") or "", "title": rec.get("title") or "",
            "photos": json.dumps(rec.get("photos") or []), "note": rec.get("note") or "",
            "sliced_weight": rec.get("sliced_weight"), "print_time": rec.get("print_time"),
            "thumb": thumb, "thumb_source": rec.get("thumb_source") or "",
            "error": rec.get("error") or "", "analyzer": analyzer, "analyzed_at": time.time(),
            "hidden": int(bool(rec.get("not_model"))),
        }
        cols = ", ".join(vals)
        marks = ", ".join("?" for _ in vals)
        updates = ", ".join(f"{k}=excluded.{k}" for k in vals if k != "path")
        with self._lock:
            cur = self.conn.execute(
                f"INSERT INTO files ({cols}, added_at) VALUES ({marks}, ?) "
                f"ON CONFLICT(path) DO UPDATE SET {updates} RETURNING id",
                (*vals.values(), time.time()),
            )
            item_id = cur.fetchone()[0]
            self.conn.commit()
        return item_id

    def remove_paths(self, paths: list[str]) -> list[tuple[int, str]]:
        """Delete rows; returns (id, thumb) of what was removed."""
        out = []
        with self._lock:
            for p in paths:
                r = self.conn.execute("SELECT id, thumb FROM files WHERE path=?", (p,)).fetchone()
                if r:
                    out.append((r[0], r[1] or ""))
                    self.conn.execute("DELETE FROM files WHERE id=?", (r[0],))
            self.conn.commit()
        return out

    def user_data_for(self, paths: list[str]) -> dict[str, dict]:
        with self._lock:
            out = {}
            for p in paths:
                r = self.conn.execute(
                    "SELECT sha256, status, favorite, tags, notes FROM files WHERE path=?", (p,)
                ).fetchone()
                if r and (r["status"] or r["favorite"] or (r["tags"] or "[]") != "[]" or r["notes"]):
                    out[p] = dict(r)
            return out

    def restore_user_data(self, item_id: int, data: dict) -> None:
        with self._lock:
            self.conn.execute(
                "UPDATE files SET status=?, favorite=?, tags=?, notes=? WHERE id=?",
                (data["status"], data["favorite"], data["tags"], data["notes"], item_id),
            )
            self.conn.commit()

    # ---------- user data ----------
    def set_user(self, ids: list[int], **fields) -> None:
        sets, vals = [], []
        for k, v in fields.items():
            if k not in USER_FIELDS:
                raise KeyError(k)
            if k == "tags":
                v = json.dumps(sorted(set(v), key=str.lower))
            if k == "favorite":
                v = int(bool(v))
            sets.append(f"{k}=?")
            vals.append(v)
        with self._lock:
            self.conn.executemany(
                f"UPDATE files SET {', '.join(sets)} WHERE id=?", [(*vals, i) for i in ids]
            )
            self.conn.commit()

    def ignore_duplicate(self, sha: str, ignore: bool = True) -> None:
        with self._lock:
            if ignore:
                self.conn.execute("INSERT OR IGNORE INTO dup_ignore VALUES (?)", (sha,))
            else:
                self.conn.execute("DELETE FROM dup_ignore WHERE sha256=?", (sha,))
            self.conn.commit()

    def clear_thumb_versions(self) -> None:
        """Force re-analysis of everything (e.g. after changing the default colour)."""
        with self._lock:
            self.conn.execute("UPDATE files SET analyzer=0")
            self.conn.commit()
