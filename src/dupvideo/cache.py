"""
On-disk fingerprint cache.

Decoding a handful of frames from every video is the expensive part of a
scan, and a video library barely changes between runs.  Keying on
``(path, size, mtime)`` lets a rescan skip decoding anything untouched, which
turns a repeat scan of a large library from minutes into seconds.

The cache is strictly an optimisation: every operation degrades to a no-op if
the database cannot be opened or written, and a stale row is detected by the
size/mtime check rather than trusted.
"""

from __future__ import annotations

import contextlib
import os
import sqlite3
import sys
import time
from collections.abc import Iterable, Iterator

from .engine import CachedFingerprint

APP_DIR_NAME = "DuplicateVideoFinder"
DB_NAME = "fingerprints.sqlite3"
MAX_AGE_DAYS = 120
MAX_ROWS = 100_000

_SCHEMA = """
CREATE TABLE IF NOT EXISTS fingerprints (
    key       TEXT PRIMARY KEY,
    size      INTEGER NOT NULL,
    mtime_ns  INTEGER NOT NULL,
    width     INTEGER NOT NULL,
    height    INTEGER NOT NULL,
    duration  REAL NOT NULL,
    dhashes   TEXT NOT NULL,
    phashes   TEXT NOT NULL,
    md5       TEXT NOT NULL DEFAULT '',
    used_at   INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_used_at ON fingerprints(used_at);
"""


def default_dir() -> str:
    """Per-user application data directory."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    elif sys.platform == "darwin":
        base = os.path.join(os.path.expanduser("~"), "Library",
                            "Application Support")
    else:
        base = (os.environ.get("XDG_CACHE_HOME")
                or os.path.join(os.path.expanduser("~"), ".cache"))
    return os.path.join(base, APP_DIR_NAME)


def _pack(hashes: tuple[int, ...]) -> str:
    return ",".join(f"{h:016x}" for h in hashes)


def _unpack(text: str) -> tuple[int, ...]:
    if not text:
        return ()
    return tuple(int(part, 16) for part in text.split(","))


class FingerprintCache:
    """SQLite-backed store of previously computed video fingerprints."""

    def __init__(self, path: str | None = None) -> None:
        self.path = path or os.path.join(default_dir(), DB_NAME)
        self.available = False
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with self._session() as db:
                db.executescript(_SCHEMA)
            self.available = True
        except Exception:
            pass                     # run without a cache rather than fail

    def _connect(self) -> sqlite3.Connection:
        # A fresh connection per operation: scans run on a worker thread and
        # SQLite connections are not safely shared across threads.
        db = sqlite3.connect(self.path, timeout=5.0)
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=NORMAL")
        return db

    @contextlib.contextmanager
    def _session(self) -> Iterator[sqlite3.Connection]:
        """Transactional connection that is always closed again."""
        db = self._connect()
        try:
            with db:
                yield db
        finally:
            db.close()

    # ---- reads ------------------------------------------------------------ #
    def load(self, paths: Iterable[str]) -> dict[str, CachedFingerprint]:
        """Fetch cached rows for ``paths``.  Missing or unreadable → empty."""
        keys = [os.path.normcase(os.path.abspath(p)) for p in paths]
        if not keys or not self.available:
            return {}
        found: dict[str, CachedFingerprint] = {}
        try:
            with self._session() as db:
                # SQLite caps host parameters per statement, so batch the IN().
                for start in range(0, len(keys), 500):
                    batch = keys[start:start + 500]
                    placeholders = ",".join("?" * len(batch))
                    rows = db.execute(
                        "SELECT key, size, mtime_ns, width, height, duration,"
                        f" dhashes, phashes, md5 FROM fingerprints"
                        f" WHERE key IN ({placeholders})", batch)
                    for row in rows:
                        found[row[0]] = CachedFingerprint(
                            key=row[0], size=row[1], mtime_ns=row[2],
                            width=row[3], height=row[4], duration=row[5],
                            dhashes=_unpack(row[6]), phashes=_unpack(row[7]),
                            md5=row[8])
        except Exception:
            return {}
        return found

    def count(self) -> int:
        if not self.available:
            return 0
        try:
            with self._session() as db:
                return int(db.execute(
                    "SELECT COUNT(*) FROM fingerprints").fetchone()[0])
        except Exception:
            return 0

    # ---- writes ----------------------------------------------------------- #
    def store(self, entries: Iterable[CachedFingerprint]) -> None:
        rows = [(e.key, e.size, e.mtime_ns, e.width, e.height, e.duration,
                 _pack(e.dhashes), _pack(e.phashes), e.md5, int(time.time()))
                for e in entries]
        if not rows or not self.available:
            return
        try:
            with self._session() as db:
                db.executemany(
                    "INSERT INTO fingerprints (key, size, mtime_ns, width,"
                    " height, duration, dhashes, phashes, md5, used_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?)"
                    " ON CONFLICT(key) DO UPDATE SET"
                    "   size=excluded.size, mtime_ns=excluded.mtime_ns,"
                    "   width=excluded.width, height=excluded.height,"
                    "   duration=excluded.duration,"
                    "   dhashes=excluded.dhashes, phashes=excluded.phashes,"
                    "   md5=CASE WHEN excluded.md5 != '' THEN excluded.md5"
                    "            ELSE fingerprints.md5 END,"
                    "   used_at=excluded.used_at", rows)
        except Exception:
            pass

    def touch(self, keys: Iterable[str]) -> None:
        """Mark cache hits as recently used so pruning doesn't evict them."""
        batch = [(int(time.time()), k) for k in keys]
        if not batch or not self.available:
            return
        try:
            with self._session() as db:
                db.executemany(
                    "UPDATE fingerprints SET used_at=? WHERE key=?", batch)
        except Exception:
            pass

    def prune(self, max_age_days: int = MAX_AGE_DAYS,
              max_rows: int = MAX_ROWS) -> None:
        """Drop long-unused rows so the file cannot grow without bound."""
        if not self.available:
            return
        try:
            with self._session() as db:
                db.execute("DELETE FROM fingerprints WHERE used_at < ?",
                           (int(time.time()) - max_age_days * 86400,))
                db.execute(
                    "DELETE FROM fingerprints WHERE key IN ("
                    "  SELECT key FROM fingerprints"
                    "  ORDER BY used_at DESC LIMIT -1 OFFSET ?)", (max_rows,))
        except Exception:
            pass

    def clear(self) -> None:
        if not self.available:
            return
        try:
            with self._session() as db:
                db.execute("DELETE FROM fingerprints")
        except Exception:
            return
        # VACUUM cannot run inside a transaction, and letting it raise in the
        # block above would roll the DELETE straight back.
        try:
            db = self._connect()
            db.isolation_level = None
            try:
                db.execute("VACUUM")
            finally:
                db.close()
        except Exception:
            pass
