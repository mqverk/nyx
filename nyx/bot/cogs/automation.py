"""Automation cog: presence, AFK auto-reply, keyword capture, and snippets.

The three behaviours that are easy to get wrong — the AFK anti-loop cooldown,
keyword matching, and snippet expansion — are implemented as pure helpers so
they can be tested deterministically without a gateway connection.
"""

from __future__ import annotations

import contextlib
import re
import time
from typing import Any

import discord
from discord.ext import commands

AFK_COOLDOWN_SECONDS = 60.0
_STATUSES = {"online", "idle", "dnd", "invisible"}


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------
class AfkCooldown:
    """Strict per-(user, channel) rate limiter for AFK auto-replies.

    ``should_reply`` returns True at most once per ``window`` seconds for a
    given pair, which is what stops two AFK accounts from ping-ponging forever.
    """

    def __init__(self, window: float = AFK_COOLDOWN_SECONDS) -> None:
        self.window = float(window)
        self._last: dict[tuple[int, int], float] = {}

    def should_reply(
        self, user_id: int, channel_id: int, now: float | None = None
    ) -> bool:
        moment = time.monotonic() if now is None else float(now)
        key = (int(user_id), int(channel_id))
        previous = self._last.get(key)
        if previous is not None and moment - previous < self.window:
            return False
        self._last[key] = moment
        return True

    def reset(self) -> None:
        self._last.clear()

    @property
    def tracked(self) -> int:
        return len(self._last)


def match_keywords(content: str, keywords: list[str]) -> list[str]:
    """Return every keyword present in ``content`` as a whole word/phrase."""
    if not content or not keywords:
        return []
    lowered = content.lower()
    hits: list[str] = []
    for keyword in keywords:
        needle = (keyword or "").strip().lower()
        if not needle:
            continue
        if re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", lowered):
            hits.append(needle)
    return hits


def expand_snippet(content: str, snippets: dict[str, str]) -> str | None:
    """Expand a leading snippet token, preserving any trailing arguments."""
    text = (content or "").strip()
    if not text or not snippets:
        return None
    first, _, rest = text.partition(" ")
    expansion = snippets.get(first.lower())
    if expansion is None:
        return None
    return f"{expansion} {rest}".strip() if rest else expansion


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------
class Automation(commands.Cog, name="Automation"):
    """Presence, AFK responder, keyword watcher, and snippet expander."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.cooldown = AfkCooldown()

    # -- helpers -----------------------------------------------------------
    @property
    def db(self) -> Any:
        return getattr(self.bot, "db", None)

    @property
    def cfg(self) -> Any:
        return getattr(self.bot, "config_manager", None)

    def _config(self) -> Any:
        return self.cfg.get() if self.cfg is not None else None

    # -- listeners ---------------------------------------------------------
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author is None:
            return
        if message.author.id == getattr(self.bot.user, "id", None):
            return

        config = self._config()
        if config is None:
            return

        # 1. keyword / mention capture -> SQLite
        hits = match_keywords(message.content or "", config.keywords)
        mentioned = bool(getattr(self.bot.user, "mentioned_in", lambda _m: False)(message))
        if hits or mentioned:
            if self.db is not None:
                with contextlib.suppress(Exception):
                    await self.db.log_keyword_hit(
                        content=message.content or "",
                        matched=", ".join(hits) or "mention",
                        kind="mention" if mentioned and not hits else "keyword",
                        guild_id=getattr(message.guild, "id", None),
                        guild_name=getattr(message.guild, "name", None),
                        channel_id=message.channel.id,
                        channel_name=getattr(message.channel, "name", None),
                        author_id=message.author.id,
                        author_name=str(message.author),
                        jump_url=getattr(message, "jump_url", None),
                    )

        # 2. AFK auto-reply with strict cooldown
        if config.afk.enabled and (mentioned or message.guild is None):
            if self.cooldown.should_reply(message.author.id, message.channel.id):
                with contextlib.suppress(discord.HTTPException):
                    await message.channel.send(
                        f"{message.author.mention} {config.afk.message}"
                    )

        # 3. snippet expansion
        expanded = expand_snippet(message.content or "", config.snippets)
        if expanded:
            with contextlib.suppress(discord.HTTPException):
                await message.channel.send(expanded)

    # -- commands ----------------------------------------------------------
    @commands.command(name="afk")
    async def afk(self, ctx: commands.Context, *, arg: str = "") -> None:
        """``.afk on|off|<message>`` toggles the auto-responder."""
        if self.cfg is None:
            await ctx.send("config unavailable")
            return
        value = arg.strip()
        if value.lower() in ("off", "disable", "false"):
            self.cfg.update({"afk": {"enabled": False}})
            await ctx.send("AFK off")
        elif value.lower() in ("on", "enable", "true") or not value:
            self.cfg.update({"afk": {"enabled": True}})
            await ctx.send("AFK on")
        else:
            self.cfg.update({"afk": {"enabled": True, "message": value}})
            await ctx.send(f"AFK on — {value}")

    @commands.command(name="presence")
    async def presence(self, ctx: commands.Context, status: str = "online", *, text: str = "") -> None:
        if self.cfg is None:
            await ctx.send("config unavailable")
            return
        status = status.lower()
        if status not in _STATUSES:
            await ctx.send(f"status must be one of {sorted(_STATUSES)}")
            return
        self.cfg.update({"presence": {"status": status, "text": text}})
        with contextlib.suppress(Exception):
            await self.bot.change_presence(
                status=getattr(discord.Status, status),
                activity=discord.CustomActivity(name=text or None),
            )
        await ctx.send(f"presence → {status} {text}".strip())

    @commands.group(name="snippet", invoke_without_command=True)
    async def snippet(self, ctx: commands.Context) -> None:
        config = self._config()
        items = config.snippets if config else {}
        await ctx.send(
            "\n".join(f"`{k}` → {v}" for k, v in items.items()) or "no snippets"
        )

    @snippet.command(name="add")
    async def snippet_add(self, ctx: commands.Context, key: str, *, value: str) -> None:
        if self.cfg is None:
            return
        snippets = dict(self._config().snippets)
        snippets[key.lower()] = value
        self.cfg.update({"snippets": snippets})
        await ctx.send(f"snippet `{key.lower()}` saved")

    @snippet.command(name="remove")
    async def snippet_remove(self, ctx: commands.Context, key: str) -> None:
        if self.cfg is None:
            return
        snippets = dict(self._config().snippets)
        if snippets.pop(key.lower(), None) is None:
            await ctx.send("no such snippet")
            return
        self.cfg.update({"snippets": snippets})
        await ctx.send(f"snippet `{key.lower()}` removed")

    @commands.group(name="keyword", invoke_without_command=True)
    async def keyword(self, ctx: commands.Context) -> None:
        config = self._config()
        words = config.keywords if config else []
        await ctx.send(", ".join(f"`{w}`" for w in words) or "no keywords")

    @keyword.command(name="add")
    async def keyword_add(self, ctx: commands.Context, *, word: str) -> None:
        if self.cfg is None:
            return
        words = list(self._config().keywords)
        words.append(word)
        self.cfg.update({"keywords": words})
        await ctx.send(f"watching `{word.strip().lower()}`")

    @keyword.command(name="remove")
    async def keyword_remove(self, ctx: commands.Context, *, word: str) -> None:
        if self.cfg is None:
            return
        target = word.strip().lower()
        words = [w for w in self._config().keywords if w != target]
        self.cfg.update({"keywords": words})
        await ctx.send(f"stopped watching `{target}`")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Automation(bot))
