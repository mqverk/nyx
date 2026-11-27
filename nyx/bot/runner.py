"""Self-bot runner and hot-reload cog manager.

``BotRunner`` owns the discord.py-self client lifecycle and is driven by the
web UI supervisor (start / stop / restart). It degrades safely: with an empty
``DISCORD_TOKEN`` it boots into *standby* instead of crashing, so the dashboard
is usable before a token is configured.

``LogBus`` is a fan-out pub/sub queue that streams runtime telemetry to any
number of WebSocket subscribers without blocking the bot.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import logging
import pkgutil
import time
from collections import deque
from typing import Any, Callable

import discord
from discord.ext import commands

from nyx.config import ConfigManager, get_token

log = logging.getLogger("nyx.bot")

STATUS_STOPPED = "stopped"
STATUS_STARTING = "starting"
STATUS_RUNNING = "running"
STATUS_STANDBY = "standby"
STATUS_ERROR = "error"


# ---------------------------------------------------------------------------
# Log fan-out
# ---------------------------------------------------------------------------
class LogBus:
    """In-memory pub/sub for runtime log lines.

    Each subscriber gets its own bounded queue; a slow consumer drops events
    rather than stalling the bot. A small ring buffer lets a freshly connected
    dashboard backfill recent history.
    """

    def __init__(self, history: int = 200, queue_size: int = 500) -> None:
        self._subs: set[asyncio.Queue[dict[str, Any]]] = set()
        self._history: deque[dict[str, Any]] = deque(maxlen=history)
        self._queue_size = queue_size

    def publish(self, level: str, message: str, **fields: Any) -> dict[str, Any]:
        event = {"ts": time.time(), "level": level, "message": message, **fields}
        self._history.append(event)
        for queue in list(self._subs):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass
        return event

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=self._queue_size)
        self._subs.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._subs.discard(queue)

    @property
    def history(self) -> list[dict[str, Any]]:
        return list(self._history)

    @property
    def subscriber_count(self) -> int:
        return len(self._subs)


# ---------------------------------------------------------------------------
# Cog manager
# ---------------------------------------------------------------------------
class CogManager:
    """Discovers, loads, unloads and hot-reloads cogs at runtime."""

    def __init__(
        self,
        bot: commands.Bot,
        package: str = "nyx.bot.cogs",
        log_bus: LogBus | None = None,
    ) -> None:
        self.bot = bot
        self.package = package
        self.log_bus = log_bus
        self._loaded: set[str] = set()

    # -- helpers -----------------------------------------------------------
    def _emit(self, level: str, message: str) -> None:
        if self.log_bus is not None:
            self.log_bus.publish(level, message, source="cogs")
        else:
            getattr(log, level if level != "error" else "error")(message)

    def _ext(self, name: str) -> str:
        return f"{self.package}.{name}"

    # -- public API --------------------------------------------------------
    def discover(self) -> list[str]:
        """Return importable cog module names shipped in the cogs package."""
        try:
            pkg = importlib.import_module(self.package)
        except ModuleNotFoundError:
            return []
        return sorted(
            module.name
            for module in pkgutil.iter_modules(pkg.__path__)
            if not module.name.startswith("_")
        )

    @property
    def loaded(self) -> list[str]:
        return sorted(self._loaded)

    async def load(self, name: str) -> bool:
        if name in self._loaded:
            return True
        try:
            await self.bot.load_extension(self._ext(name))
        except Exception as exc:  # noqa: BLE001 - surface any extension error
            self._emit("error", f"cog load failed: {name}: {exc}")
            return False
        self._loaded.add(name)
        self._emit("info", f"cog loaded: {name}")
        return True

    async def unload(self, name: str) -> bool:
        if name not in self._loaded:
            return True
        try:
            await self.bot.unload_extension(self._ext(name))
        except Exception as exc:  # noqa: BLE001
            self._emit("error", f"cog unload failed: {name}: {exc}")
            return False
        self._loaded.discard(name)
        self._emit("info", f"cog unloaded: {name}")
        return True

    async def reload(self, name: str) -> bool:
        """Hot-reload a cog without restarting the client."""
        if name not in self._loaded:
            return await self.load(name)
        try:
            await self.bot.reload_extension(self._ext(name))
        except Exception as exc:  # noqa: BLE001
            self._emit("error", f"cog reload failed: {name}: {exc}")
            return False
        self._emit("info", f"cog reloaded: {name}")
        return True

    async def load_enabled(self, enabled: dict[str, bool]) -> dict[str, bool]:
        """Load every cog marked enabled in config; returns per-cog results."""
        results: dict[str, bool] = {}
        for name in self.discover():
            if enabled.get(name, True):
                results[name] = await self.load(name)
        return results

    async def unload_all(self) -> None:
        for name in list(self._loaded):
            await self.unload(name)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------
class BotRunner:
    """Owns the self-bot client and exposes a supervisor-friendly API."""

    def __init__(
        self,
        config: ConfigManager,
        log_bus: LogBus | None = None,
        token_provider: Callable[[], str] = get_token,
        db: Any | None = None,
    ) -> None:
        self.config = config
        self.log_bus = log_bus or LogBus()
        self._token_provider = token_provider
        self.db = db
        self._client: commands.Bot | None = None
        self._task: asyncio.Task[None] | None = None
        self._status = STATUS_STOPPED
        self._started_at: float | None = None
        self._last_error: str | None = None
        self._cogs: CogManager | None = None
        self._lock = asyncio.Lock()

    # -- introspection -----------------------------------------------------
    @property
    def status(self) -> str:
        return self._status

    @property
    def client(self) -> commands.Bot | None:
        return self._client

    @property
    def cogs(self) -> CogManager | None:
        return self._cogs

    def snapshot(self) -> dict[str, Any]:
        """Serialisable state for the dashboard status cards."""
        client = self._client
        user = getattr(client, "user", None)
        latency = 0.0
        if client is not None:
            with contextlib.suppress(Exception):
                latency = round(float(client.latency) * 1000, 1)
        uptime = 0.0
        if self._started_at and self._status == STATUS_RUNNING:
            uptime = round(time.time() - self._started_at, 1)
        return {
            "status": self._status,
            "user": str(user) if user else None,
            "user_id": getattr(user, "id", None),
            "latency_ms": latency,
            "uptime_s": uptime,
            "guilds": len(getattr(client, "guilds", []) or []),
            "cogs": self._cogs.loaded if self._cogs else [],
            "last_error": self._last_error,
        }

    # -- client construction ----------------------------------------------
    def _build_client(self) -> commands.Bot:
        cfg = self.config.get()
        # discord.py-self has no Intents/self_bot kwargs: the client is
        # inherently a user account and receives every gateway event.
        client: commands.Bot = commands.Bot(
            command_prefix=cfg.prefix,
            help_command=None,
        )
        # dependency injection for cogs loaded as extensions
        client.db = self.db  # type: ignore[attr-defined]
        client.config_manager = self.config  # type: ignore[attr-defined]
        client.log_bus = self.log_bus  # type: ignore[attr-defined]

        @client.event
        async def on_ready() -> None:  # pragma: no cover - needs live gateway
            self._status = STATUS_RUNNING
            self._started_at = time.time()
            self.log_bus.publish(
                "info", f"logged in as {client.user}", source="bot"
            )
            await self._apply_presence(client)

        return client

    async def _apply_presence(self, client: commands.Bot) -> None:
        cfg = self.config.get()
        presence = cfg.presence
        activity = None
        if presence.text or presence.emoji:
            with contextlib.suppress(Exception):
                activity = discord.CustomActivity(
                    name=presence.text or None,
                    emoji=presence.emoji or None,
                )
        status_map = {
            "online": discord.Status.online,
            "idle": discord.Status.idle,
            "dnd": discord.Status.dnd,
            "invisible": discord.Status.invisible,
        }
        with contextlib.suppress(Exception):
            await client.change_presence(
                status=status_map.get(presence.status, discord.Status.online),
                activity=activity,
            )

    # -- lifecycle ---------------------------------------------------------
    async def start(self) -> dict[str, Any]:
        async with self._lock:
            if self._status in (STATUS_RUNNING, STATUS_STARTING):
                return self.snapshot()

            token = (self._token_provider() or "").strip()
            if not token:
                self._status = STATUS_STANDBY
                self._last_error = None
                self.log_bus.publish(
                    "warning",
                    "DISCORD_TOKEN is empty — running in standby mode",
                    source="bot",
                )
                return self.snapshot()

            self._status = STATUS_STARTING
            self._last_error = None
            self._client = self._build_client()
            self._cogs = CogManager(self._client, log_bus=self.log_bus)
            await self._cogs.load_enabled(self.config.get().cogs)
            self._task = asyncio.create_task(self._run(token), name="nyx-bot")
            self.log_bus.publish("info", "bot starting", source="bot")
            return self.snapshot()

    async def _run(self, token: str) -> None:
        assert self._client is not None
        try:
            await self._client.start(token)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - login failures must not kill the UI
            self._status = STATUS_ERROR
            self._last_error = str(exc)
            self.log_bus.publish("error", f"bot crashed: {exc}", source="bot")
        finally:
            if self._status not in (STATUS_ERROR, STATUS_STANDBY):
                self._status = STATUS_STOPPED

    async def stop(self) -> dict[str, Any]:
        async with self._lock:
            if self._cogs is not None:
                with contextlib.suppress(Exception):
                    await self._cogs.unload_all()
            if self._client is not None:
                with contextlib.suppress(Exception):
                    await self._client.close()
            if self._task is not None and not self._task.done():
                self._task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await self._task
            self._client = None
            self._task = None
            self._cogs = None
            self._started_at = None
            self._status = STATUS_STOPPED
            self.log_bus.publish("info", "bot stopped", source="bot")
            return self.snapshot()

    async def restart(self) -> dict[str, Any]:
        await self.stop()
        return await self.start()
