"""Tests for nyx.bot.runner — standby boot, lifecycle, and cog hot-reload."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from nyx.bot.runner import (
    STATUS_STANDBY,
    STATUS_STOPPED,
    BotRunner,
    CogManager,
    LogBus,
)
from nyx.config import ConfigManager


class FakeBot:
    """Minimal stand-in for commands.Bot that records extension calls."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.loaded: set[str] = set()
        self.fail_on: set[str] = set()

    async def load_extension(self, name: str) -> None:
        self.calls.append(("load", name))
        if name in self.fail_on:
            raise RuntimeError(f"boom loading {name}")
        self.loaded.add(name)

    async def unload_extension(self, name: str) -> None:
        self.calls.append(("unload", name))
        self.loaded.discard(name)

    async def reload_extension(self, name: str) -> None:
        self.calls.append(("reload", name))
        if name in self.fail_on:
            raise RuntimeError(f"boom reloading {name}")


@pytest.fixture
def cfg(tmp_path: Path) -> ConfigManager:
    return ConfigManager(tmp_path / "config.json")


# --------------------------------------------------------------------------
# Standby / lifecycle
# --------------------------------------------------------------------------
async def test_standby_boot_without_token(cfg: ConfigManager):
    bus = LogBus()
    runner = BotRunner(cfg, log_bus=bus, token_provider=lambda: "")

    snap = await runner.start()

    assert snap["status"] == STATUS_STANDBY
    assert runner.client is None
    assert snap["user"] is None
    assert any("standby" in e["message"].lower() for e in bus.history)


async def test_standby_does_not_create_task(cfg: ConfigManager):
    runner = BotRunner(cfg, token_provider=lambda: "   ")
    await runner.start()
    assert runner._task is None  # no gateway connection attempted


async def test_snapshot_shape(cfg: ConfigManager):
    runner = BotRunner(cfg, token_provider=lambda: "")
    snap = await runner.start()
    assert set(snap) == {
        "status", "user", "user_id", "latency_ms", "uptime_s",
        "guilds", "cogs", "last_error",
    }
    assert snap["latency_ms"] == 0.0
    assert snap["uptime_s"] == 0.0


async def test_stop_from_standby_is_safe(cfg: ConfigManager):
    runner = BotRunner(cfg, token_provider=lambda: "")
    await runner.start()
    snap = await runner.stop()
    assert snap["status"] == STATUS_STOPPED


async def test_restart_returns_to_standby(cfg: ConfigManager):
    runner = BotRunner(cfg, token_provider=lambda: "")
    await runner.start()
    snap = await runner.restart()
    assert snap["status"] == STATUS_STANDBY


async def test_start_is_idempotent_while_running(cfg: ConfigManager):
    runner = BotRunner(cfg, token_provider=lambda: "")
    await runner.start()
    first = runner.snapshot()
    second = await runner.start()
    assert first == second


def test_build_client_uses_configured_prefix(cfg: ConfigManager):
    cfg.save(cfg.get().model_copy(update={"prefix": "!"}))
    runner = BotRunner(cfg, token_provider=lambda: "")
    client = runner._build_client()
    assert client.command_prefix == "!"
    assert client.help_command is None


# --------------------------------------------------------------------------
# LogBus
# --------------------------------------------------------------------------
async def test_log_bus_fans_out_to_subscribers():
    bus = LogBus()
    q1, q2 = bus.subscribe(), bus.subscribe()
    bus.publish("info", "hello", source="test")

    assert (await asyncio.wait_for(q1.get(), 1))["message"] == "hello"
    assert (await asyncio.wait_for(q2.get(), 1))["message"] == "hello"
    assert bus.subscriber_count == 2

    bus.unsubscribe(q1)
    assert bus.subscriber_count == 1


async def test_log_bus_history_is_bounded():
    bus = LogBus(history=3)
    for i in range(10):
        bus.publish("info", f"m{i}")
    assert [e["message"] for e in bus.history] == ["m7", "m8", "m9"]


async def test_log_bus_slow_subscriber_does_not_block():
    bus = LogBus(queue_size=2)
    q = bus.subscribe()
    for i in range(10):
        bus.publish("info", f"m{i}")  # must never raise QueueFull
    assert q.qsize() == 2


# --------------------------------------------------------------------------
# CogManager
# --------------------------------------------------------------------------
async def test_cog_load_unload_reload():
    bot = FakeBot()
    bus = LogBus()
    mgr = CogManager(bot, log_bus=bus)

    assert await mgr.load("core") is True
    assert mgr.loaded == ["core"]
    assert ("load", "nyx.bot.cogs.core") in bot.calls

    assert await mgr.reload("core") is True
    assert ("reload", "nyx.bot.cogs.core") in bot.calls

    assert await mgr.unload("core") is True
    assert mgr.loaded == []
    assert ("unload", "nyx.bot.cogs.core") in bot.calls


async def test_cog_load_failure_is_reported_not_raised():
    bot = FakeBot()
    bot.fail_on = {"nyx.bot.cogs.broken"}
    bus = LogBus()
    mgr = CogManager(bot, log_bus=bus)

    assert await mgr.load("broken") is False
    assert mgr.loaded == []
    assert any("failed" in e["message"] for e in bus.history)


async def test_cog_reload_of_unloaded_cog_loads_it():
    bot = FakeBot()
    mgr = CogManager(bot)
    assert await mgr.reload("core") is True
    assert ("load", "nyx.bot.cogs.core") in bot.calls


async def test_load_enabled_respects_flags():
    bot = FakeBot()
    mgr = CogManager(bot)
    mgr.discover = lambda: ["core", "automation"]  # type: ignore[method-assign]
    results = await mgr.load_enabled({"core": True, "automation": False})
    assert results == {"core": True}
    assert mgr.loaded == ["core"]


async def test_discover_returns_list_for_real_package():
    mgr = CogManager(FakeBot())
    found = mgr.discover()
    assert isinstance(found, list)
    assert all(not n.startswith("_") for n in found)


async def test_unload_all_clears_loaded():
    bot = FakeBot()
    mgr = CogManager(bot)
    await mgr.load("a")
    await mgr.load("b")
    await mgr.unload_all()
    assert mgr.loaded == []
