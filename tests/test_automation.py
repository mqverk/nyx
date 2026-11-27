"""Tests for nyx.bot.cogs.automation — cooldown lock, keyword matcher, snippets."""

from __future__ import annotations

import pytest
from discord.ext import commands

from nyx.bot.cogs.automation import (
    AFK_COOLDOWN_SECONDS,
    AfkCooldown,
    Automation,
    expand_snippet,
    match_keywords,
)


# --------------------------------------------------------------------------
# AFK cooldown
# --------------------------------------------------------------------------
def test_cooldown_allows_first_reply():
    cd = AfkCooldown()
    assert cd.should_reply(1, 2, now=1000.0) is True


def test_cooldown_blocks_within_60_seconds():
    cd = AfkCooldown()
    assert cd.should_reply(1, 2, now=1000.0) is True
    assert cd.should_reply(1, 2, now=1000.0 + 59.9) is False
    assert cd.should_reply(1, 2, now=1000.0 + 60.0) is True


def test_cooldown_is_per_user_and_channel():
    cd = AfkCooldown()
    assert cd.should_reply(1, 2, now=0.0) is True
    assert cd.should_reply(1, 3, now=1.0) is True   # different channel
    assert cd.should_reply(9, 2, now=2.0) is True   # different user
    assert cd.should_reply(1, 2, now=3.0) is False  # same pair, still locked


def test_cooldown_window_constant_is_60():
    assert AFK_COOLDOWN_SECONDS == 60.0
    assert AfkCooldown().window == 60.0


def test_cooldown_reset_clears_locks():
    cd = AfkCooldown()
    cd.should_reply(1, 2, now=0.0)
    cd.reset()
    assert cd.tracked == 0
    assert cd.should_reply(1, 2, now=1.0) is True


def test_cooldown_prevents_ping_pong_loop():
    """Two AFK accounts must not reply to each other more than once a minute."""
    a, b = AfkCooldown(), AfkCooldown()
    replies = 0
    for tick in range(0, 120):  # 2 minutes of 1s ticks
        if a.should_reply(1, 10, now=float(tick)):
            replies += 1
        if b.should_reply(2, 10, now=float(tick)):
            replies += 1
    assert replies == 4  # 2 accounts x 2 windows


# --------------------------------------------------------------------------
# Keyword matching
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "content,keywords,expected",
    [
        ("deploy is broken", ["deploy"], ["deploy"]),
        ("DEPLOY is broken", ["deploy"], ["deploy"]),
        ("concatenate strings", ["cat"], []),
        ("the cat sat", ["cat"], ["cat"]),
        ("please deploy the build", ["deploy", "build"], ["deploy", "build"]),
        ("nothing here", ["deploy"], []),
        ("", ["deploy"], []),
        ("anything", [], []),
        ("multi word phrase here", ["multi word"], ["multi word"]),
        ("deploying", ["deploy"], []),
    ],
)
def test_match_keywords(content, keywords, expected):
    assert match_keywords(content, keywords) == expected


def test_match_keywords_ignores_blank_entries():
    assert match_keywords("hello", ["", "  ", "hello"]) == ["hello"]


# --------------------------------------------------------------------------
# Snippet expansion
# --------------------------------------------------------------------------
def test_expand_snippet_exact():
    assert expand_snippet("brb", {"brb": "be right back"}) == "be right back"


def test_expand_snippet_case_insensitive():
    assert expand_snippet("BRB", {"brb": "be right back"}) == "be right back"


def test_expand_snippet_preserves_trailing_args():
    assert expand_snippet("brb 5 min", {"brb": "be right back"}) == "be right back 5 min"


def test_expand_snippet_unknown_returns_none():
    assert expand_snippet("hello", {"brb": "be right back"}) is None
    assert expand_snippet("", {"brb": "x"}) is None
    assert expand_snippet("brb", {}) is None


def test_expand_snippet_does_not_match_mid_sentence():
    assert expand_snippet("say brb now", {"brb": "be right back"}) is None


# --------------------------------------------------------------------------
# Cog registration
# --------------------------------------------------------------------------
async def test_cog_registers_commands():
    bot = commands.Bot(command_prefix=".")
    await bot.add_cog(Automation(bot))
    cog = bot.get_cog("Automation")
    assert cog is not None
    names = {cmd.name for cmd in cog.get_commands()}
    assert {"afk", "presence", "snippet", "keyword"} <= names
    await bot.close()


async def test_extension_setup_loads_automation():
    bot = commands.Bot(command_prefix=".")
    await bot.load_extension("nyx.bot.cogs.automation")
    assert bot.get_cog("Automation") is not None
    await bot.unload_extension("nyx.bot.cogs.automation")
    await bot.close()


async def test_both_cogs_coexist():
    bot = commands.Bot(command_prefix=".")
    await bot.load_extension("nyx.bot.cogs.core")
    await bot.load_extension("nyx.bot.cogs.automation")
    assert {c.qualified_name for c in bot.cogs.values()} == {"Core", "Automation"}
    await bot.close()
