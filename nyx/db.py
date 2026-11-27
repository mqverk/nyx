"""Async SQLite storage for nyx.

Three append-only event tables, all written through a single serialized
aiosqlite connection in WAL mode:

* ``keyword_hits``  — keyword / mention captures
* ``snipes``        — deleted messages captured by ``.snipe``
* ``command_audit`` — every command invocation, success or failure

All timestamps are UTC epoch seconds (REAL) so they sort correctly and survive
timezone changes.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, Iterable, Sequence

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS keyword_hits (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           REAL    NOT NULL,
    kind         TEXT    NOT NULL DEFAULT 'keyword',
    guild_id     INTEGER,
    guild_name   TEXT,
    channel_id   INTEGER,
    channel_name TEXT,
    author_id    INTEGER,
    author_name  TEXT,
    content      TEXT    NOT NULL,
    matched      TEXT,
    jump_url     TEXT
);
CREATE INDEX IF NOT EXISTS idx_kh_ts      ON keyword_hits(ts DESC);
CREATE INDEX IF NOT EXISTS idx_kh_channel ON keyword_hits(channel_id);
CREATE INDEX IF NOT EXISTS idx_kh_kind    ON keyword_hits(kind);

CREATE TABLE IF NOT EXISTS snipes (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           REAL    NOT NULL,
    guild_id     INTEGER,
    guild_name   TEXT,
    channel_id   INTEGER NOT NULL,
    channel_name TEXT,
    author_id    INTEGER,
    author_name  TEXT,
    content      TEXT,
    attachments  TEXT,
    jump_url     TEXT
);
CREATE INDEX IF NOT EXISTS idx_snipes_channel_ts ON snipes(channel_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_snipes_ts         ON snipes(ts DESC);

CREATE TABLE IF NOT EXISTS command_audit (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           REAL    NOT NULL,
    guild_id     INTEGER,
    guild_name   TEXT,
    channel_id   INTEGER,
    channel_name TEXT,
    author_id    INTEGER,
    author_name  TEXT,
    command      TEXT    NOT NULL,
    args         TEXT,
    success      INTEGER NOT NULL DEFAULT 1,
    error        TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON command_audit(ts DESC);
"""

TABLES = ("keyword_hits", "snipes", "command_audit")


class Database:
    """Serialized async wrapper around a single SQLite file."""

    def __init__(self, path: str | Path = "data/nyx.db") -> None:
        self.path = Path(path)
        self._conn: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    # -- lifecycle ---------------------------------------------------------
    async def connect(self) -> "Database":
        if self._conn is not None:
            return self
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(str(self.path))
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA synchronous=NORMAL")
        await self._conn.execute("PRAGMA busy_timeout=5000")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._conn.executescript(SCHEMA)
        await self._conn.commit()
        return self

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def __aenter__(self) -> "Database":
        return await self.connect()

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database.connect() must be awaited first")
        return self._conn

    # -- writers -----------------------------------------------------------
    async def log_keyword_hit(
        self,
        *,
        content: str,
        matched: str = "",
        kind: str = "keyword",
        guild_id: int | None = None,
        guild_name: str | None = None,
        channel_id: int | None = None,
        channel_name: str | None = None,
        author_id: int | None = None,
        author_name: str | None = None,
        jump_url: str | None = None,
        ts: float | None = None,
    ) -> int:
        async with self._lock:
            cur = await self.conn.execute(
                """INSERT INTO keyword_hits
                   (ts, kind, guild_id, guild_name, channel_id, channel_name,
                    author_id, author_name, content, matched, jump_url)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (ts or time.time(), kind, guild_id, guild_name, channel_id,
                 channel_name, author_id, author_name, content, matched, jump_url),
            )
            await self.conn.commit()
            return int(cur.lastrowid or 0)

    async def log_snipe(
        self,
        *,
        channel_id: int,
        content: str = "",
        guild_id: int | None = None,
        guild_name: str | None = None,
        channel_name: str | None = None,
        author_id: int | None = None,
        author_name: str | None = None,
        attachments: Sequence[str] | None = None,
        jump_url: str | None = None,
        ts: float | None = None,
    ) -> int:
        async with self._lock:
            cur = await self.conn.execute(
                """INSERT INTO snipes
                   (ts, guild_id, guild_name, channel_id, channel_name,
                    author_id, author_name, content, attachments, jump_url)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (ts or time.time(), guild_id, guild_name, channel_id, channel_name,
                 author_id, author_name, content,
                 json.dumps(list(attachments or [])), jump_url),
            )
            await self.conn.commit()
            return int(cur.lastrowid or 0)

    async def log_command(
        self,
        *,
        command: str,
        args: str = "",
        success: bool = True,
        error: str | None = None,
        guild_id: int | None = None,
        guild_name: str | None = None,
        channel_id: int | None = None,
        channel_name: str | None = None,
        author_id: int | None = None,
        author_name: str | None = None,
        ts: float | None = None,
    ) -> int:
        async with self._lock:
            cur = await self.conn.execute(
                """INSERT INTO command_audit
                   (ts, guild_id, guild_name, channel_id, channel_name,
                    author_id, author_name, command, args, success, error)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (ts or time.time(), guild_id, guild_name, channel_id, channel_name,
                 author_id, author_name, command, args, int(success), error),
            )
            await self.conn.commit()
            return int(cur.lastrowid or 0)

    # -- readers -----------------------------------------------------------
    async def _rows(self, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
        async with self._lock:
            cur = await self.conn.execute(sql, tuple(params))
            rows = await cur.fetchall()
            await cur.close()
        return [dict(row) for row in rows]

    async def search_keyword_hits(
        self, query: str | None = None, limit: int = 50, offset: int = 0
    ) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        sql = "SELECT * FROM keyword_hits"
        params: list[Any] = []
        if query:
            sql += " WHERE content LIKE ? OR matched LIKE ? OR author_name LIKE ?"
            like = f"%{query}%"
            params += [like, like, like]
        sql += " ORDER BY ts DESC LIMIT ? OFFSET ?"
        params += [limit, max(0, int(offset))]
        return await self._rows(sql, params)

    async def search_snipes(
        self, query: str | None = None, limit: int = 50, offset: int = 0
    ) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        sql = "SELECT * FROM snipes"
        params: list[Any] = []
        if query:
            sql += " WHERE content LIKE ? OR author_name LIKE ? OR channel_name LIKE ?"
            like = f"%{query}%"
            params += [like, like, like]
        sql += " ORDER BY ts DESC LIMIT ? OFFSET ?"
        params += [limit, max(0, int(offset))]
        return await self._rows(sql, params)

    async def search_audit(
        self, query: str | None = None, limit: int = 50, offset: int = 0
    ) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        sql = "SELECT * FROM command_audit"
        params: list[Any] = []
        if query:
            sql += " WHERE command LIKE ? OR args LIKE ? OR author_name LIKE ?"
            like = f"%{query}%"
            params += [like, like, like]
        sql += " ORDER BY ts DESC LIMIT ? OFFSET ?"
        params += [limit, max(0, int(offset))]
        return await self._rows(sql, params)

    async def search_events(
        self, query: str | None = None, limit: int = 50
    ) -> dict[str, list[dict[str, Any]]]:
        """Unified search used by the dashboard table."""
        return {
            "keyword_hits": await self.search_keyword_hits(query, limit),
            "snipes": await self.search_snipes(query, limit),
        }

    async def last_snipe(self, channel_id: int) -> dict[str, Any] | None:
        rows = await self._rows(
            "SELECT * FROM snipes WHERE channel_id = ? ORDER BY ts DESC LIMIT 1",
            (channel_id,),
        )
        return rows[0] if rows else None

    async def count(self, table: str) -> int:
        if table not in TABLES:
            raise ValueError(f"unknown table {table!r}")
        rows = await self._rows(f"SELECT COUNT(*) AS n FROM {table}")
        return int(rows[0]["n"])

    # -- maintenance -------------------------------------------------------
    async def cleanup(self, days: int = 30) -> dict[str, int]:
        """Delete rows older than ``days``; returns per-table deletion counts."""
        cutoff = time.time() - max(0, days) * 86400
        removed: dict[str, int] = {}
        async with self._lock:
            for table in TABLES:
                cur = await self.conn.execute(
                    f"DELETE FROM {table} WHERE ts < ?", (cutoff,)
                )
                removed[table] = cur.rowcount or 0
            await self.conn.commit()
        return removed
