"""Core utility cog: ping, info commands, purge, snipe, remind, and safe calc.

All non-trivial logic lives in module-level pure helpers (``safe_eval``,
``parse_duration``) so it can be unit-tested without a live Discord gateway.
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import operator
import re
import time
from typing import Any

import discord
from discord.ext import commands

MAX_EXPR_LEN = 200
MAX_POW_EXPONENT = 512
MAX_ABS_RESULT = 1e100

_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARYOPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}

_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
_DURATION_RE = re.compile(r"(\d+)\s*([smhdw])", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------
def _eval_node(node: ast.AST) -> float:
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise ValueError("only numeric literals are allowed")
        return node.value
    if isinstance(node, ast.BinOp):
        op = _BINOPS.get(type(node.op))
        if op is None:
            raise ValueError(f"operator {type(node.op).__name__} is not allowed")
        left, right = _eval_node(node.left), _eval_node(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > MAX_POW_EXPONENT:
            raise ValueError(f"exponent too large (max {MAX_POW_EXPONENT})")
        result = op(left, right)
        if isinstance(result, complex) or abs(result) > MAX_ABS_RESULT:
            raise ValueError("result out of range")
        return result
    if isinstance(node, ast.UnaryOp):
        op = _UNARYOPS.get(type(node.op))
        if op is None:
            raise ValueError("unary operator not allowed")
        return op(_eval_node(node.operand))
    raise ValueError(f"disallowed expression element: {type(node).__name__}")


def safe_eval(expr: str) -> int | float:
    """Evaluate a purely arithmetic expression with no names or calls.

    Raises ``ValueError`` for anything that is not numbers + arithmetic.
    """
    expr = (expr or "").strip()
    if not expr:
        raise ValueError("empty expression")
    if len(expr) > MAX_EXPR_LEN:
        raise ValueError(f"expression too long (max {MAX_EXPR_LEN} chars)")
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise ValueError("invalid syntax") from exc
    result = _eval_node(tree)
    if isinstance(result, float) and result.is_integer():
        return int(result)
    return result


def parse_duration(text: str) -> int:
    """Parse ``10s``/``5m``/``1h30m``/``2d``/``90`` into seconds."""
    text = (text or "").strip().lower()
    if not text:
        raise ValueError("empty duration")
    if text.isdigit():
        return int(text)
    total = 0
    matched = False
    for amount, unit in _DURATION_RE.findall(text):
        total += int(amount) * _UNITS[unit]
        matched = True
    if not matched:
        raise ValueError(f"could not parse duration {text!r}")
    return total


def human_duration(seconds: int) -> str:
    parts: list[str] = []
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60), ("s", 1)):
        if seconds >= size:
            count, seconds = divmod(seconds, size)
            parts.append(f"{count}{unit}")
    return " ".join(parts) or "0s"


MAX_SPOILER_INPUT = 400  # 5 chars per spoiler ("||x||") stays under 2000


def to_spoiler(text: str) -> str:
    """Wrap every character in its own ``||spoiler||`` tag."""
    return "".join(f"||{ch}||" for ch in (text or ""))


_SMALLCAPS_MAP = {
    "a": "ᴀ", "b": "ʙ", "c": "ᴄ", "d": "ᴅ", "e": "ᴇ", "f": "ꜰ", "g": "ɢ",
    "h": "ʜ", "i": "ɪ", "j": "ᴊ", "k": "ᴋ", "l": "ʟ", "m": "ᴍ", "n": "ɴ",
    "o": "ᴏ", "p": "ᴘ", "q": "ǫ", "r": "ʀ", "s": "ꜱ", "t": "ᴛ", "u": "ᴜ",
    "v": "ᴠ", "w": "ᴡ", "x": "x", "y": "ʏ", "z": "ᴢ",
}
_SMALLCAPS_TABLE = str.maketrans(_SMALLCAPS_MAP)


def to_smallcaps(text: str) -> str:
    """Transliterate text into Unicode small-caps glyphs."""
    return (text or "").lower().translate(_SMALLCAPS_TABLE)


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------
class Core(commands.Cog, name="Core"):
    """Everyday utilities plus the message sniper."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._snipes: dict[int, dict[str, Any]] = {}
        self._reminders: set[asyncio.Task[None]] = set()

    # -- helpers -----------------------------------------------------------
    @property
    def db(self) -> Any:
        return getattr(self.bot, "db", None)

    def _prefix(self) -> str:
        cfg = getattr(self.bot, "config_manager", None)
        return cfg.get().prefix if cfg is not None else "."

    # -- listeners ---------------------------------------------------------
    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message) -> None:
        if message.author is None or message.author.id == getattr(self.bot.user, "id", None):
            return
        record = {
            "content": message.content or "",
            "author_name": str(message.author),
            "author_id": message.author.id,
            "channel_id": message.channel.id,
            "channel_name": getattr(message.channel, "name", str(message.channel.id)),
            "guild_id": getattr(message.guild, "id", None),
            "guild_name": getattr(message.guild, "name", None),
            "jump_url": getattr(message, "jump_url", None),
            "ts": time.time(),
        }
        self._snipes[message.channel.id] = record
        if self.db is not None:
            with contextlib.suppress(Exception):
                await self.db.log_snipe(**record)

    # -- commands ----------------------------------------------------------
    @commands.command(name="ping")
    async def ping(self, ctx: commands.Context) -> None:
        latency = round(float(getattr(self.bot, "latency", 0.0)) * 1000, 1)
        await ctx.send(f"pong — {latency}ms")

    @commands.command(name="userinfo")
    async def userinfo(self, ctx: commands.Context, member: discord.Member | None = None) -> None:
        target = member or ctx.author
        created = getattr(target, "created_at", None)
        await ctx.send(
            f"**{target}**\nid: `{target.id}`\ncreated: {created:%Y-%m-%d}"
            if created
            else f"**{target}**\nid: `{target.id}`"
        )

    @commands.command(name="serverinfo")
    async def serverinfo(self, ctx: commands.Context) -> None:
        guild = ctx.guild
        if guild is None:
            await ctx.send("not in a server")
            return
        await ctx.send(
            f"**{guild.name}**\nid: `{guild.id}`\nmembers: {getattr(guild, 'member_count', '?')}"
        )

    @commands.command(name="avatar")
    async def avatar(self, ctx: commands.Context, member: discord.Member | None = None) -> None:
        target = member or ctx.author
        url = getattr(getattr(target, "display_avatar", None), "url", None)
        await ctx.send(url or "no avatar")

    @commands.command(name="purge")
    async def purge(self, ctx: commands.Context, count: int = 10) -> None:
        """Delete up to ``count`` of *our own* recent messages, rate-limit safe."""
        count = max(1, min(int(count), 100))
        deleted = 0
        async for msg in ctx.channel.history(limit=count * 4):
            if msg.author.id != getattr(self.bot.user, "id", None):
                continue
            with contextlib.suppress(discord.HTTPException):
                await msg.delete()
                deleted += 1
                await asyncio.sleep(1.0)  # stay well under the delete rate limit
            if deleted >= count:
                break
        with contextlib.suppress(discord.HTTPException):
            await ctx.send(f"purged {deleted} message(s)", delete_after=5)

    @commands.command(name="snipe")
    async def snipe(self, ctx: commands.Context) -> None:
        record = self._snipes.get(ctx.channel.id)
        if record is None and self.db is not None:
            with contextlib.suppress(Exception):
                record = await self.db.last_snipe(ctx.channel.id)
        if not record:
            await ctx.send("nothing to snipe")
            return
        await ctx.send(
            f"**{record.get('author_name', 'unknown')}** deleted:\n{record.get('content') or '*[no text]*'}"
        )

    @commands.command(name="remind")
    async def remind(self, ctx: commands.Context, duration: str, *, text: str = "reminder") -> None:
        try:
            seconds = parse_duration(duration)
        except ValueError as exc:
            await ctx.send(f"bad duration: {exc}")
            return
        await ctx.send(f"ok, reminding you in {human_duration(seconds)}")

        async def _fire() -> None:
            await asyncio.sleep(seconds)
            with contextlib.suppress(discord.HTTPException):
                await ctx.send(f"{ctx.author.mention} reminder: {text}")

        task = asyncio.create_task(_fire())
        self._reminders.add(task)
        task.add_done_callback(self._reminders.discard)

    @commands.command(name="calc")
    async def calc(self, ctx: commands.Context, *, expr: str) -> None:
        try:
            result = safe_eval(expr)
        except (ValueError, ZeroDivisionError) as exc:
            await ctx.send(f"error: {exc}")
            return
        await ctx.send(f"`{expr}` = **{result}**")

    @commands.command(name="spoiler")
    async def spoiler(self, ctx: commands.Context, *, text: str) -> None:
        """Rewrite your own command message with per-character spoilers."""
        cleaned = (text or "").strip()
        if not cleaned:
            await ctx.send("usage: `.spoiler <text>`")
            return
        if len(cleaned) > MAX_SPOILER_INPUT:
            await ctx.send(f"too long — max {MAX_SPOILER_INPUT} characters")
            return
        with contextlib.suppress(discord.HTTPException):
            await ctx.message.edit(content=to_spoiler(cleaned))

    @commands.command(name="smallcaps")
    async def smallcaps(self, ctx: commands.Context, *, text: str) -> None:
        """Rewrite your own command message in Unicode small caps."""
        cleaned = (text or "").strip()
        if not cleaned:
            await ctx.send("usage: `.smallcaps <text>`")
            return
        with contextlib.suppress(discord.HTTPException):
            await ctx.message.edit(content=to_smallcaps(cleaned))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Core(bot))
