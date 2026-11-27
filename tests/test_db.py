"""Tests for nyx.db — schema, concurrent writes, search, and retention."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import aiosqlite
import pytest

from nyx.db import TABLES, Database


@pytest.fixture
async def db(tmp_path: Path):
    database = Database(tmp_path / "test.db")
    await database.connect()
    yield database
    await database.close()


async def test_schema_creates_all_tables(db: Database):
    async with aiosqlite.connect(db.path) as raw:
        cur = await raw.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
        names = {row[0] for row in await cur.fetchall()}
    assert set(TABLES) <= names


async def test_wal_mode_enabled(db: Database):
    async with aiosqlite.connect(db.path) as raw:
        cur = await raw.execute("PRAGMA journal_mode")
        mode = (await cur.fetchone())[0]
    assert mode.lower() == "wal"


async def test_keyword_hit_round_trip(db: Database):
    await db.log_keyword_hit(
        content="deploy is broken again",
        matched="deploy",
        guild_id=1,
        guild_name="Guild",
        channel_id=2,
        channel_name="general",
        author_id=3,
        author_name="alice",
        jump_url="https://discord.com/channels/1/2/3",
    )
    rows = await db.search_keyword_hits()
    assert len(rows) == 1
    row = rows[0]
    assert row["matched"] == "deploy"
    assert row["author_name"] == "alice"
    assert row["jump_url"].endswith("/1/2/3")
    assert row["kind"] == "keyword"


async def test_search_filters_by_query(db: Database):
    await db.log_keyword_hit(content="alpha one", matched="alpha")
    await db.log_keyword_hit(content="beta two", matched="beta")
    assert len(await db.search_keyword_hits("alpha")) == 1
    assert len(await db.search_keyword_hits("BETA")) == 1
    assert len(await db.search_keyword_hits("gamma")) == 0


async def test_concurrent_writes_are_serialized(db: Database):
    async def write(i: int) -> None:
        await db.log_keyword_hit(content=f"msg {i}", matched="msg")

    await asyncio.gather(*(write(i) for i in range(50)))
    assert await db.count("keyword_hits") == 50


async def test_snipe_last_returns_newest(db: Database):
    await db.log_snipe(channel_id=10, content="first", author_name="a")
    await db.log_snipe(channel_id=10, content="second", author_name="b")
    await db.log_snipe(channel_id=11, content="other channel")

    latest = await db.last_snipe(10)
    assert latest is not None
    assert latest["content"] == "second"
    assert await db.last_snipe(99) is None


async def test_snipe_attachments_are_json(db: Database):
    await db.log_snipe(channel_id=1, content="pic", attachments=["a.png", "b.png"])
    row = (await db.search_snipes())[0]
    assert "a.png" in row["attachments"]


async def test_command_audit_records_failure(db: Database):
    await db.log_command(command="purge", args="5", success=False, error="rate limited")
    rows = await db.search_audit("purge")
    assert rows[0]["success"] == 0
    assert rows[0]["error"] == "rate limited"


async def test_pagination(db: Database):
    for i in range(10):
        await db.log_keyword_hit(content=f"n{i}", ts=1000 + i)
    page1 = await db.search_keyword_hits(limit=4, offset=0)
    page2 = await db.search_keyword_hits(limit=4, offset=4)
    assert len(page1) == 4 and len(page2) == 4
    assert {r["id"] for r in page1}.isdisjoint({r["id"] for r in page2})
    # newest first
    assert page1[0]["content"] == "n9"


async def test_cleanup_removes_only_old_rows(db: Database):
    old = time.time() - 40 * 86400
    await db.log_keyword_hit(content="ancient", ts=old)
    await db.log_keyword_hit(content="fresh")
    await db.log_snipe(channel_id=1, content="ancient", ts=old)

    removed = await db.cleanup(days=30)
    assert removed["keyword_hits"] == 1
    assert removed["snipes"] == 1
    assert await db.count("keyword_hits") == 1


async def test_count_rejects_unknown_table(db: Database):
    with pytest.raises(ValueError):
        await db.count("users; DROP TABLE snipes")


async def test_unified_search_shape(db: Database):
    await db.log_keyword_hit(content="hello", matched="hello")
    await db.log_snipe(channel_id=1, content="hello")
    result = await db.search_events("hello")
    assert set(result) == {"keyword_hits", "snipes"}
    assert len(result["keyword_hits"]) == 1
    assert len(result["snipes"]) == 1


# --------------------------------------------------------------------------
# Bookmarks
# --------------------------------------------------------------------------
async def test_bookmark_persists_all_fields(db: Database):
    bookmark_id = await db.add_bookmark(
        content="remember this",
        message_id=42,
        guild_id=1,
        guild_name="Guild",
        channel_id=2,
        channel_name="general",
        author_id=3,
        author_name="alice",
        attachments=["https://cdn/a.png"],
        jump_url="https://discord.com/channels/1/2/42",
        saved_by=9,
        saved_by_name="me",
    )
    rows = await db.list_bookmarks()
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == bookmark_id
    assert row["content"] == "remember this"
    assert row["author_name"] == "alice"
    assert row["channel_name"] == "general"
    assert row["jump_url"].endswith("/1/2/42")
    assert "a.png" in row["attachments"]
    assert row["saved_by_name"] == "me"


async def test_bookmark_search_and_delete(db: Database):
    first = await db.add_bookmark(content="alpha note")
    await db.add_bookmark(content="beta note")

    assert len(await db.list_bookmarks("alpha")) == 1
    assert len(await db.list_bookmarks("note")) == 2

    assert await db.delete_bookmark(first) is True
    assert await db.delete_bookmark(999999) is False
    assert await db.count("bookmarks") == 1


async def test_bookmarks_are_not_pruned_by_cleanup(db: Database):
    old = time.time() - 90 * 86400
    await db.add_bookmark(content="ancient but mine", ts=old)
    await db.log_keyword_hit(content="ancient event", ts=old)

    await db.cleanup(days=30)

    assert await db.count("bookmarks") == 1      # user data survives
    assert await db.count("keyword_hits") == 0   # event data pruned


async def test_bookmark_table_exists(db: Database):
    async with aiosqlite.connect(db.path) as raw:
        cur = await raw.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='bookmarks'"
        )
        assert await cur.fetchone() is not None
