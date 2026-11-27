"""Tests for nyx.bot.cogs.productivity — last-seen tracking and .seen."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from discord.ext import commands

from nyx.bot.cogs.productivity import Productivity, format_todo_list, humanize_ago
from nyx.db import Database


@pytest.fixture
async def db(tmp_path: Path):
    database = Database(tmp_path / "p.db")
    await database.connect()
    yield database
    await database.close()


# --------------------------------------------------------------------------
# humanize_ago
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "delta,expected",
    [
        (0, "just now"),
        (59, "just now"),
        (60, "1m ago"),
        (3599, "59m ago"),
        (3600, "1h ago"),
        (86399, "23h ago"),
        (86400, "1d ago"),
        (604800, "1w ago"),
        (1209600, "2w ago"),
    ],
)
def test_humanize_ago(delta, expected):
    now = 1_000_000.0
    assert humanize_ago(now - delta, now=now) == expected


def test_humanize_ago_clamps_future_timestamps():
    now = 1_000_000.0
    assert humanize_ago(now + 500, now=now) == "just now"


# --------------------------------------------------------------------------
# Persistence
# --------------------------------------------------------------------------
async def test_record_and_get_last_seen(db: Database):
    await db.record_seen(
        42,
        username="alice",
        guild_id=1,
        guild_name="Guild",
        channel_id=2,
        channel_name="general",
    )
    record = await db.get_last_seen(42)
    assert record is not None
    assert record["username"] == "alice"
    assert record["channel_name"] == "general"
    assert record["guild_name"] == "Guild"


async def test_last_seen_upserts_newest(db: Database):
    await db.record_seen(42, username="alice", channel_name="old", ts=1000.0)
    await db.record_seen(42, username="alice", channel_name="new", ts=2000.0)

    record = await db.get_last_seen(42)
    assert record["channel_name"] == "new"
    assert record["ts"] == 2000.0
    assert await db.count("last_seen") == 1  # upsert, not append


async def test_last_seen_preserves_fields_on_partial_update(db: Database):
    await db.record_seen(7, username="bob", guild_name="Guild", channel_name="general")
    await db.record_seen(7)  # presence ping with no context

    record = await db.get_last_seen(7)
    assert record["guild_name"] == "Guild"      # COALESCE kept it
    assert record["channel_name"] == "general"


async def test_get_last_seen_unknown_user(db: Database):
    assert await db.get_last_seen(999) is None


async def test_last_seen_survives_cleanup(db: Database):
    old = time.time() - 90 * 86400
    await db.record_seen(5, username="carol", ts=old)
    await db.cleanup(days=30)
    assert await db.count("last_seen") == 1


# --------------------------------------------------------------------------
# Todos CRUD
# --------------------------------------------------------------------------
async def test_todo_add_and_list(db: Database):
    first = await db.add_todo(1, "buy milk")
    second = await db.add_todo(1, "ship nyx")

    rows = await db.list_todos(1)
    assert [r["id"] for r in rows] == [first, second]
    assert rows[0]["text"] == "buy milk"
    assert rows[0]["done"] == 0


async def test_todo_complete(db: Database):
    todo_id = await db.add_todo(1, "finish task")
    assert await db.complete_todo(1, todo_id) is True

    row = (await db.list_todos(1))[0]
    assert row["done"] == 1
    assert row["done_ts"] is not None


async def test_todo_remove(db: Database):
    todo_id = await db.add_todo(1, "temporary")
    assert await db.remove_todo(1, todo_id) is True
    assert await db.list_todos(1) == []
    assert await db.remove_todo(1, todo_id) is False


async def test_todo_ownership_is_enforced(db: Database):
    mine = await db.add_todo(1, "my task")
    assert await db.complete_todo(2, mine) is False   # other user cannot complete
    assert await db.remove_todo(2, mine) is False     # nor delete
    assert len(await db.list_todos(2)) == 0           # nor even see
    assert (await db.list_todos(1))[0]["done"] == 0


async def test_todo_list_filters_done(db: Database):
    a = await db.add_todo(1, "open")
    b = await db.add_todo(1, "closed")
    await db.complete_todo(1, b)

    assert len(await db.list_todos(1, include_done=False)) == 1
    assert len(await db.list_todos(1, include_done=True)) == 2
    assert (await db.list_todos(1, include_done=False))[0]["id"] == a


async def test_todos_survive_cleanup(db: Database):
    old = time.time() - 90 * 86400
    await db.add_todo(1, "ancient todo", ts=old)
    await db.cleanup(days=30)
    assert await db.count("todos") == 1


# --------------------------------------------------------------------------
# format_todo_list
# --------------------------------------------------------------------------
def test_format_todo_list_empty():
    assert "no todos" in format_todo_list([])


def test_format_todo_list_marks_done():
    text = format_todo_list([
        {"id": 1, "text": "open item", "done": 0},
        {"id": 2, "text": "closed item", "done": 1},
    ])
    assert "⬜ `#1` open item" in text
    assert "✅ `#2` closed item" in text


def test_format_todo_list_truncates():
    rows = [{"id": i, "text": "x" * 100, "done": 0} for i in range(100)]
    assert len(format_todo_list(rows)) <= 1900


# --------------------------------------------------------------------------
# Cog registration
# --------------------------------------------------------------------------
async def test_cog_registers_seen_and_todo():
    bot = commands.Bot(command_prefix=".")
    await bot.add_cog(Productivity(bot))
    cog = bot.get_cog("Productivity")
    assert cog is not None
    names = {c.name for c in cog.get_commands()}
    assert {"seen", "todo"} <= names

    todo_cmd = bot.get_command("todo")
    assert isinstance(todo_cmd, commands.Group)
    assert {"add", "list", "done", "remove"} <= {c.name for c in todo_cmd.commands}
    await bot.close()


async def test_extension_loads():
    bot = commands.Bot(command_prefix=".")
    await bot.load_extension("nyx.bot.cogs.productivity")
    assert bot.get_cog("Productivity") is not None
    await bot.unload_extension("nyx.bot.cogs.productivity")
    await bot.close()
