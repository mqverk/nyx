"""Productivity cog: last-seen tracking, todos, status rotation, timezones.

Pure helpers live at module level so they can be tested without a gateway.
"""

from __future__ import annotations

import contextlib
import time
from typing import Any

import discord
from discord.ext import commands


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------
class Productivity(commands.Cog, name="Productivity"):
    """Passive activity tracking and personal productivity commands."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @property
    def db(self) -> Any:
        return getattr(self.bot, "db", None)

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


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Productivity(bot))
