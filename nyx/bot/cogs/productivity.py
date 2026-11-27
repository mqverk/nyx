"""Productivity cog: last-seen tracking, todos, status rotation, timezones.

Pure helpers live at module level so they can be tested without a gateway.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import time
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import discord
from discord.ext import commands

MIN_CYCLE_INTERVAL = 60  # Discord presence rate limit floor

_OFFSET_RE = re.compile(r"^(?:UTC|GMT)?\s*([+-])\s*(\d{1,2})(?::?(\d{2}))?$", re.IGNORECASE)

# Friendly city names -> IANA zones. IANA names always win when given directly.
CITY_ALIASES: dict[str, str] = {
    "kolkata": "Asia/Kolkata",
    "calcutta": "Asia/Kolkata",
    "mumbai": "Asia/Kolkata",
    "delhi": "Asia/Kolkata",
    "bangalore": "Asia/Kolkata",
    "london": "Europe/London",
    "paris": "Europe/Paris",
    "berlin": "Europe/Berlin",
    "moscow": "Europe/Moscow",
    "dubai": "Asia/Dubai",
    "tokyo": "Asia/Tokyo",
    "singapore": "Asia/Singapore",
    "sydney": "Australia/Sydney",
    "new york": "America/New_York",
    "nyc": "America/New_York",
    "los angeles": "America/Los_Angeles",
    "la": "America/Los_Angeles",
    "chicago": "America/Chicago",
    "toronto": "America/Toronto",
    "sao paulo": "America/Sao_Paulo",
    "utc": "UTC",
    "gmt": "UTC",
}


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------
def resolve_timezone(text: str) -> tzinfo:
    """Resolve an IANA name, a ``UTC+5:30`` offset, or a friendly city name."""
    raw = (text or "").strip()
    if not raw:
        raise ValueError("empty timezone")

    match = _OFFSET_RE.match(raw)
    if match:
        sign = 1 if match.group(1) == "+" else -1
        hours = int(match.group(2))
        minutes = int(match.group(3) or 0)
        if hours > 14 or minutes > 59:
            raise ValueError(f"offset out of range: {raw}")
        return timezone(sign * timedelta(hours=hours, minutes=minutes))

    try:
        return ZoneInfo(raw)
    except (ZoneInfoNotFoundError, ValueError):
        pass

    alias = CITY_ALIASES.get(raw.lower())
    if alias:
        try:
            return ZoneInfo(alias)
        except ZoneInfoNotFoundError:
            pass

    raise ValueError(f"unknown timezone {raw!r}")


def format_local_time(zone: tzinfo, now: datetime | None = None) -> str:
    """Render the current time in ``zone`` as ``YYYY-MM-DD HH:MM ABBR``."""
    moment = datetime.now(zone) if now is None else now.astimezone(zone)
    label = moment.tzname() or ""
    stamp = moment.strftime("%Y-%m-%d %H:%M")
    return f"{stamp} {label}".strip()


def clamp_interval(seconds: Any) -> int:
    """Coerce a rotation interval to an integer of at least 60 seconds."""
    try:
        value = int(seconds)
    except (TypeError, ValueError):
        value = MIN_CYCLE_INTERVAL
    return max(MIN_CYCLE_INTERVAL, value)


def humanize_ago(ts: float, now: float | None = None) -> str:
    """Render an epoch timestamp as a short relative string."""
    moment = time.time() if now is None else float(now)
    delta = max(0.0, moment - float(ts))
    if delta < 60:
        return "just now"
    if delta < 3600:
        return f"{int(delta // 60)}m ago"
    if delta < 86400:
        return f"{int(delta // 3600)}h ago"
    if delta < 604800:
        return f"{int(delta // 86400)}d ago"
    return f"{int(delta // 604800)}w ago"


def format_todo_list(rows: list[dict[str, Any]], limit: int = 1900) -> str:
    """Render todo rows as a compact checklist, truncated to a safe length."""
    if not rows:
        return "no todos yet — add one with `.todo add <text>`"
    lines = []
    for row in rows:
        mark = "✅" if row.get("done") else "⬜"
        lines.append(f"{mark} `#{row.get('id')}` {row.get('text', '')}")
    text = "\n".join(lines)
    return text if len(text) <= limit else text[: limit - 1] + "…"


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------
class Productivity(commands.Cog, name="Productivity"):
    """Passive activity tracking and personal productivity commands."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._cycle_task: asyncio.Task[None] | None = None
        self._cycle_index = 0

    @property
    def db(self) -> Any:
        return getattr(self.bot, "db", None)

    @property
    def cfg(self) -> Any:
        return getattr(self.bot, "config_manager", None)

    async def cog_unload(self) -> None:
        await self._stop_cycle()

    # -- status rotation ---------------------------------------------------
    async def _stop_cycle(self) -> None:
        if self._cycle_task is not None and not self._cycle_task.done():
            self._cycle_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._cycle_task
        self._cycle_task = None

    async def _cycle_loop(self, presets: list[str], interval: int) -> None:
        while True:
            text = presets[self._cycle_index % len(presets)]
            self._cycle_index += 1
            with contextlib.suppress(Exception):
                await self.bot.change_presence(
                    activity=discord.CustomActivity(name=text or None)
                )
            await asyncio.sleep(interval)

    # -- listeners ---------------------------------------------------------
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        author = getattr(message, "author", None)
        if author is None or getattr(author, "bot", False):
            return
        database = self.db
        if database is None:
            return
        with contextlib.suppress(Exception):
            await database.record_seen(
                author.id,
                username=str(author),
                guild_id=getattr(getattr(message, "guild", None), "id", None),
                guild_name=getattr(getattr(message, "guild", None), "name", None),
                channel_id=getattr(getattr(message, "channel", None), "id", None),
                channel_name=getattr(getattr(message, "channel", None), "name", None),
            )

    @commands.Cog.listener()
    async def on_presence_update(self, before: Any, after: Any) -> None:
        if after is None or getattr(after, "bot", False):
            return
        status = getattr(after, "status", None)
        if status is not None and str(status) == "offline":
            return
        database = self.db
        if database is None:
            return
        with contextlib.suppress(Exception):
            await database.record_seen(after.id, username=str(after))

    # -- commands ----------------------------------------------------------
    @commands.command(name="seen")
    async def seen(
        self, ctx: commands.Context, member: discord.Member | None = None
    ) -> None:
        """Show when a user was last active."""
        target = member or ctx.author
        database = self.db
        if database is None:
            await ctx.send("storage unavailable")
            return

        record = await database.get_last_seen(target.id)
        if record is None:
            await ctx.send(f"no activity recorded for {target.display_name} yet")
            return

        channel = record.get("channel_name") or record.get("channel_id") or "unknown"
        guild = record.get("guild_name") or "direct messages"
        await ctx.send(
            f"**{target.display_name}** was last active "
            f"{humanize_ago(record['ts'])} in #{channel} ({guild})"
        )

    @commands.group(name="todo", invoke_without_command=True)
    async def todo(self, ctx: commands.Context) -> None:
        await ctx.send(
            "usage: `.todo add <text>` · `.todo list` · "
            "`.todo done <id>` · `.todo remove <id>`"
        )

    @todo.command(name="add")
    async def todo_add(self, ctx: commands.Context, *, text: str) -> None:
        database = self.db
        if database is None:
            await ctx.send("storage unavailable")
            return
        cleaned = (text or "").strip()
        if not cleaned:
            await ctx.send("todo text cannot be empty")
            return
        todo_id = await database.add_todo(ctx.author.id, cleaned[:500])
        await ctx.send(f"added todo `#{todo_id}`")

    @todo.command(name="list")
    async def todo_list(self, ctx: commands.Context) -> None:
        database = self.db
        if database is None:
            await ctx.send("storage unavailable")
            return
        rows = await database.list_todos(ctx.author.id)
        await ctx.send(format_todo_list(rows))

    @todo.command(name="done")
    async def todo_done(self, ctx: commands.Context, todo_id: int) -> None:
        database = self.db
        if database is None:
            await ctx.send("storage unavailable")
            return
        if await database.complete_todo(ctx.author.id, todo_id):
            await ctx.send(f"✅ completed `#{todo_id}`")
        else:
            await ctx.send(f"no todo `#{todo_id}` of yours")

    @todo.command(name="remove")
    async def todo_remove(self, ctx: commands.Context, todo_id: int) -> None:
        database = self.db
        if database is None:
            await ctx.send("storage unavailable")
            return
        if await database.remove_todo(ctx.author.id, todo_id):
            await ctx.send(f"🗑️ removed `#{todo_id}`")
        else:
            await ctx.send(f"no todo `#{todo_id}` of yours")

    @commands.command(name="statuscycle")
    async def statuscycle(self, ctx: commands.Context, mode: str = "on") -> None:
        """Rotate the account status through ``status_cycle`` presets."""
        if mode.lower() in ("off", "stop", "disable", "false"):
            await self._stop_cycle()
            await ctx.send("status cycle off")
            return

        config = self.cfg.get() if self.cfg is not None else None
        presets = list(getattr(config, "status_cycle", []) or [])
        if not presets:
            await ctx.send(
                "no presets — add a `status_cycle` list to config.json first"
            )
            return

        interval = clamp_interval(getattr(config, "status_cycle_interval", 60))
        await self._stop_cycle()
        self._cycle_index = 0
        self._cycle_task = asyncio.create_task(self._cycle_loop(presets, interval))
        await ctx.send(
            f"status cycle on — {len(presets)} presets, rotating every {interval}s"
        )

    @commands.command(name="tz")
    async def tz(self, ctx: commands.Context, *, where: str = "") -> None:
        """Convert the current time to another timezone."""
        if not where.strip():
            await ctx.send("usage: `.tz Asia/Kolkata` · `.tz UTC+5:30` · `.tz london`")
            return
        try:
            zone = resolve_timezone(where)
        except ValueError as exc:
            await ctx.send(f"error: {exc}")
            return
        await ctx.send(f"**{where.strip()}** → {format_local_time(zone)}")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Productivity(bot))
