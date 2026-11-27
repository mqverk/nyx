"""Tests for nyx.bot.cogs.media — edit tracking and the .editsnipe command."""

from __future__ import annotations

import pytest
from discord.ext import commands

from nyx.bot.cogs.media import MAX_GHOST_SECONDS, EditTracker, Media, ghost_delay


# --------------------------------------------------------------------------
# EditTracker
# --------------------------------------------------------------------------
def test_record_and_last():
    tracker = EditTracker()
    tracker.record(1, before="helo", after="hello", author_name="alice")
    entry = tracker.last(1)
    assert entry is not None
    assert entry["before"] == "helo"
    assert entry["after"] == "hello"
    assert entry["author_name"] == "alice"


def test_last_returns_newest():
    tracker = EditTracker()
    tracker.record(1, before="a", after="b")
    tracker.record(1, before="c", after="d")
    assert tracker.last(1)["before"] == "c"


def test_channels_are_isolated():
    tracker = EditTracker()
    tracker.record(1, before="one", after="1")
    tracker.record(2, before="two", after="2")
    assert tracker.last(1)["before"] == "one"
    assert tracker.last(2)["before"] == "two"
    assert tracker.tracked_channels == 2


def test_history_is_bounded():
    tracker = EditTracker(max_per_channel=3)
    for i in range(10):
        tracker.record(1, before=f"b{i}", after=f"a{i}")
    assert tracker.last(1)["before"] == "b9"
    assert len(tracker._history[1]) == 3


def test_last_on_unknown_channel_is_none():
    assert EditTracker().last(999) is None


def test_clear_single_and_all():
    tracker = EditTracker()
    tracker.record(1, before="a", after="b")
    tracker.record(2, before="c", after="d")
    tracker.clear(1)
    assert tracker.last(1) is None
    assert tracker.last(2) is not None
    tracker.clear()
    assert tracker.tracked_channels == 0


def test_empty_content_is_preserved_as_empty_string():
    tracker = EditTracker()
    tracker.record(1, before=None, after="now has text")  # type: ignore[arg-type]
    assert tracker.last(1)["before"] == ""


# --------------------------------------------------------------------------
# .ghost duration handling
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "text,expected",
    [("30s", 30), ("5m", 300), ("1h", 3600), ("1h30m", 5400), ("90", 90)],
)
def test_ghost_delay_parses(text, expected):
    assert ghost_delay(text) == expected


def test_ghost_delay_clamps_to_24h():
    assert ghost_delay("5d") == MAX_GHOST_SECONDS
    assert ghost_delay("999999") == MAX_GHOST_SECONDS


def test_ghost_delay_rejects_garbage_and_zero():
    with pytest.raises(ValueError):
        ghost_delay("abc")
    with pytest.raises(ValueError):
        ghost_delay("0s")


def test_ghost_delay_never_below_one_second():
    assert ghost_delay("1s") == 1


# --------------------------------------------------------------------------
# Cog registration
# --------------------------------------------------------------------------
async def test_cog_registers_editsnipe_with_alias():
    bot = commands.Bot(command_prefix=".")
    await bot.add_cog(Media(bot))
    cog = bot.get_cog("Media")
    assert cog is not None
    names = {c.name for c in cog.get_commands()}
    assert {"editsnipe", "ghost"} <= names
    assert bot.get_command("es") is not None
    assert bot.get_command("es").name == "editsnipe"
    await bot.close()


async def test_extension_loads():
    bot = commands.Bot(command_prefix=".")
    await bot.load_extension("nyx.bot.cogs.media")
    assert bot.get_cog("Media") is not None
    await bot.unload_extension("nyx.bot.cogs.media")
    await bot.close()
