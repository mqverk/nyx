"""Media cog: edit sniper, ghost messages, bookmarks, and quote cards.

Non-trivial logic lives in module-level helpers/classes so it can be tested
without a live Discord gateway.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import textwrap
import time
from typing import Any

import discord
from discord.ext import commands
from PIL import Image, ImageDraw, ImageFont

from nyx.bot.cogs.core import human_duration, parse_duration

MAX_GHOST_SECONDS = 86400  # 24h hard cap
QUOTE_WIDTH = 820
QUOTE_PAD = 44
QUOTE_MAX_LINES = 24

_FONT_REGULAR = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
)
_FONT_BOLD = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------
def ghost_delay(duration: str) -> int:
    """Parse a duration and clamp it to a sane self-destruct window."""
    seconds = parse_duration(duration)
    if seconds <= 0:
        raise ValueError("duration must be positive")
    return max(1, min(seconds, MAX_GHOST_SECONDS))


def _font(size: int, bold: bool = False) -> Any:
    for path in (_FONT_BOLD if bold else _FONT_REGULAR):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def render_quote_png(
    display_name: str,
    timestamp: str,
    text: str,
    width: int = QUOTE_WIDTH,
) -> bytes:
    """Render a dark-mode quote card and return PNG bytes."""
    body = (text or "").strip() or "[no text]"
    name_font = _font(26, bold=True)
    meta_font = _font(17)
    body_font = _font(24)

    lines: list[str] = []
    for paragraph in body.splitlines() or [""]:
        lines.extend(textwrap.wrap(paragraph, width=52) or [""])
    lines = lines[:QUOTE_MAX_LINES]
    if len(lines) == QUOTE_MAX_LINES:
        lines[-1] = lines[-1][:49] + "…"

    line_height = 36
    body_height = len(lines) * line_height
    height = QUOTE_PAD * 2 + 74 + body_height

    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    draw.rounded_rectangle(
        [0, 0, width - 1, height - 1], radius=26,
        fill=(11, 13, 18, 255), outline=(35, 42, 58, 255), width=2,
    )
    draw.rounded_rectangle(
        [QUOTE_PAD, QUOTE_PAD + 4, QUOTE_PAD + 6, height - QUOTE_PAD - 4],
        radius=3, fill=(124, 92, 255, 255),
    )

    x = QUOTE_PAD + 26
    draw.text((x, QUOTE_PAD), display_name or "unknown", font=name_font, fill=(232, 236, 246, 255))
    draw.text((x, QUOTE_PAD + 34), timestamp or "", font=meta_font, fill=(123, 133, 156, 255))

    y = QUOTE_PAD + 74
    for line in lines:
        draw.text((x, y), line, font=body_font, fill=(214, 220, 235, 255))
        y += line_height

    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


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
        self._ghosts: set[asyncio.Task[None]] = set()

    @property
    def db(self) -> Any:
        return getattr(self.bot, "db", None)

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

    @commands.command(name="ghost")
    async def ghost(self, ctx: commands.Context, duration: str, *, text: str) -> None:
        """Post ``text`` and delete it after ``duration`` (e.g. ``.ghost 30s hi``)."""
        try:
            seconds = ghost_delay(duration)
        except ValueError as exc:
            await ctx.send(f"bad duration: {exc}")
            return
        sent = await ctx.send(text)

        async def _burn() -> None:
            await asyncio.sleep(seconds)
            with contextlib.suppress(discord.HTTPException):
                await sent.delete()

        task = asyncio.create_task(_burn())
        self._ghosts.add(task)
        task.add_done_callback(self._ghosts.discard)
        with contextlib.suppress(discord.HTTPException):
            await ctx.send(f"👻 self-destructs in {human_duration(seconds)}", delete_after=5)

    @commands.command(name="bookmark")
    async def bookmark(
        self, ctx: commands.Context, message: discord.Message | None = None
    ) -> None:
        """Save a replied-to message to the bookmarks table."""
        target = message
        if target is None:
            reference = getattr(ctx.message, "reference", None)
            target = getattr(reference, "resolved", None) if reference else None
        if not isinstance(target, discord.Message):
            await ctx.send("reply to a message to bookmark it")
            return

        database = self.db
        if database is None:
            await ctx.send("storage unavailable")
            return

        attachments = [a.url for a in getattr(target, "attachments", [])]
        bookmark_id = await database.add_bookmark(
            content=target.content or "",
            message_id=getattr(target, "id", None),
            guild_id=getattr(getattr(target, "guild", None), "id", None),
            guild_name=getattr(getattr(target, "guild", None), "name", None),
            channel_id=getattr(getattr(target, "channel", None), "id", None),
            channel_name=getattr(getattr(target, "channel", None), "name", None),
            author_id=getattr(getattr(target, "author", None), "id", None),
            author_name=str(getattr(target, "author", "")),
            attachments=attachments,
            jump_url=getattr(target, "jump_url", None),
            saved_by=getattr(ctx.author, "id", None),
            saved_by_name=str(ctx.author),
        )
        await ctx.send(f"🔖 bookmarked as #{bookmark_id}")

    @commands.command(name="quote")
    async def quote(
        self, ctx: commands.Context, message: discord.Message | None = None
    ) -> None:
        """Render a replied-to message as a dark-mode PNG quote card."""
        target = message
        if target is None:
            reference = getattr(ctx.message, "reference", None)
            target = getattr(reference, "resolved", None) if reference else None
        if not isinstance(target, discord.Message):
            await ctx.send("reply to a message to quote it")
            return

        created = getattr(target, "created_at", None)
        stamp = created.strftime("%Y-%m-%d %H:%M UTC") if created else ""
        png = render_quote_png(
            str(getattr(target, "author", "unknown")),
            stamp,
            target.content or "",
        )
        await ctx.send(file=discord.File(io.BytesIO(png), filename="quote.png"))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Media(bot))
