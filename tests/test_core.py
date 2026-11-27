"""Tests for nyx.bot.cogs.core — safe math, duration parsing, cog registration."""

from __future__ import annotations

import pytest
from discord.ext import commands

from nyx.bot.cogs.core import (
    MAX_SPOILER_INPUT,
    Core,
    human_duration,
    parse_duration,
    safe_eval,
    to_spoiler,
)


# --------------------------------------------------------------------------
# safe_eval
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "expr,expected",
    [
        ("2+2", 4),
        ("10-3", 7),
        ("6*7", 42),
        ("10/4", 2.5),
        ("7//2", 3),
        ("7%3", 1),
        ("2**10", 1024),
        ("(3+4)*2", 14),
        ("-5+2", -3),
        ("1.5*2", 3),
    ],
)
def test_safe_eval_arithmetic(expr, expected):
    assert safe_eval(expr) == expected


@pytest.mark.parametrize(
    "expr",
    [
        "__import__('os').system('id')",
        "open('/etc/passwd')",
        "a + 1",
        "lambda: 1",
        "[1,2,3]",
        "'a'*3",
        "2**10000",
        "",
        "   ",
        "1+",
    ],
)
def test_safe_eval_rejects_dangerous_or_invalid(expr):
    with pytest.raises(ValueError):
        safe_eval(expr)


def test_safe_eval_division_by_zero_propagates():
    with pytest.raises(ZeroDivisionError):
        safe_eval("1/0")


def test_safe_eval_length_cap():
    with pytest.raises(ValueError):
        safe_eval("1+" * 200 + "1")


# --------------------------------------------------------------------------
# parse_duration
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "text,seconds",
    [
        ("10s", 10),
        ("5m", 300),
        ("2h", 7200),
        ("1d", 86400),
        ("1w", 604800),
        ("1h30m", 5400),
        ("90", 90),
        ("2H", 7200),
        (" 15m ", 900),
    ],
)
def test_parse_duration(text, seconds):
    assert parse_duration(text) == seconds


@pytest.mark.parametrize("text", ["", "abc", "5x", "h"])
def test_parse_duration_rejects_garbage(text):
    with pytest.raises(ValueError):
        parse_duration(text)


def test_human_duration_round_trip():
    assert human_duration(5400) == "1h 30m"
    assert human_duration(0) == "0s"
    assert human_duration(86400) == "1d"


# --------------------------------------------------------------------------
# .spoiler transform
# --------------------------------------------------------------------------
def test_to_spoiler_wraps_every_character():
    assert to_spoiler("hi") == "||h||||i||"


def test_to_spoiler_empty_string():
    assert to_spoiler("") == ""


def test_to_spoiler_quintuples_length():
    # "||x||" is 5 characters per input character
    assert len(to_spoiler("abc")) == 5 * 3


def test_to_spoiler_preserves_spaces():
    assert to_spoiler("a b") == "||a|||| ||||b||"


def test_spoiler_input_cap_is_safe_for_discord():
    # 5 chars per spoiler must stay under Discord's 2000 char message limit
    assert MAX_SPOILER_INPUT * 5 <= 2000
    assert len(to_spoiler("x" * MAX_SPOILER_INPUT)) <= 2000


# --------------------------------------------------------------------------
# Cog registration
# --------------------------------------------------------------------------
async def test_cog_registers_all_commands():
    bot = commands.Bot(command_prefix=".")
    await bot.add_cog(Core(bot))

    cog = bot.get_cog("Core")
    assert cog is not None
    names = {cmd.name for cmd in cog.get_commands()}
    assert {
        "ping", "userinfo", "serverinfo", "avatar",
        "purge", "snipe", "remind", "calc", "spoiler",
    } <= names
    await bot.close()


async def test_extension_setup_loads_cog():
    bot = commands.Bot(command_prefix=".")
    await bot.load_extension("nyx.bot.cogs.core")
    assert bot.get_cog("Core") is not None
    await bot.unload_extension("nyx.bot.cogs.core")
    assert bot.get_cog("Core") is None
    await bot.close()


async def test_snipe_cache_starts_empty():
    bot = commands.Bot(command_prefix=".")
    cog = Core(bot)
    assert cog._snipes == {}
    await bot.close()
