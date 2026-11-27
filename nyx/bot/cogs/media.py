"""Media cog: edit sniper, ghost messages, bookmarks, and quote cards.

Non-trivial logic lives in module-level helpers/classes so it can be tested
without a live Discord gateway.
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
class EditTracker:
    """Bounded in-memory history of message edits, keyed by channel."""

    def __init__(self, max_per_channel: int = 10) -> None:
        self.max_per_channel = max(1, int(max_per_channel))
        self._history: dict[int, list[dict[str, Any]]] = {}

    def record(
        self,
        channel_id: int,
        *,
        before: str,
        after: str,
        author_name: str = "",
        author_id: int | None = None,
        jump_url: str | None = None,
        ts: float | None = None,
    ) -> dict[str, Any]:
        entry = {
            "channel_id": int(channel_id),
            "before": before or "",
            "after": after or "",
            "author_name": author_name,
            "author_id": author_id,
            "jump_url": jump_url,
            "ts": time.time() if ts is None else float(ts),
        }
        bucket = self._history.setdefault(int(channel_id), [])
        bucket.append(entry)
        del bucket[: -self.max_per_channel]
        return entry

    def last(self, channel_id: int) -> dict[str, Any] | None:
        bucket = self._history.get(int(channel_id))
        return bucket[-1] if bucket else None

    def clear(self, channel_id: int | None = None) -> None:
        if channel_id is None:
            self._history.clear()
        else:
            self._history.pop(int(channel_id), None)

    @property
    def tracked_channels(self) -> int:
        return len(self._history)


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------
class Media(commands.Cog, name="Media"):
    """Message media utilities."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.edits = EditTracker()

    # -- listeners ---------------------------------------------------------
    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message) -> None:
        if before.content == after.content:
            return
        author = getattr(after, "author", None)
        self.edits.record(
            after.channel.id,
            before=before.content or "",
            after=after.content or "",
            author_name=str(author) if author else "",
            author_id=getattr(author, "id", None),
            jump_url=getattr(after, "jump_url", None),
        )

    # -- commands ----------------------------------------------------------
    @commands.command(name="editsnipe", aliases=["es"])
    async def editsnipe(self, ctx: commands.Context) -> None:
        """Show the original text of the most recently edited message."""
        entry = self.edits.last(ctx.channel.id)
        if entry is None:
            await ctx.send("nothing to snipe — no edits seen in this channel")
            return
        before = entry["before"] or "*[empty]*"
        after = entry["after"] or "*[empty]*"
        await ctx.send(
            f"**{entry['author_name'] or 'unknown'}** edited a message:\n"
            f"**before:** {before}\n**after:** {after}"
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Media(bot))
