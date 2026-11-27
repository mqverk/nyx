"""Tests for nyx.bot.cogs.productivity — last-seen tracking and .seen."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from discord.ext import commands

from nyx.bot.cogs.productivity import Productivity, humanize_ago
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
# Cog registration
# --------------------------------------------------------------------------
async def test_cog_registers_seen():
    bot = commands.Bot(command_prefix=".")
    await bot.add_cog(Productivity(bot))
    cog = bot.get_cog("Productivity")
    assert cog is not None
    assert "seen" in {c.name for c in cog.get_commands()}
    await bot.close()


async def test_extension_loads():
    bot = commands.Bot(command_prefix=".")
    await bot.load_extension("nyx.bot.cogs.productivity")
    assert bot.get_cog("Productivity") is not None
    await bot.unload_extension("nyx.bot.cogs.productivity")
    await bot.close()
