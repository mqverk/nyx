"""Productivity cog: last-seen tracking, todos, status rotation, timezones.

Pure helpers live at module level so they can be tested without a gateway.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import Any

import discord
from discord.ext import commands

MIN_CYCLE_INTERVAL = 60  # Discord presence rate limit floor


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------
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


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Productivity(bot))
