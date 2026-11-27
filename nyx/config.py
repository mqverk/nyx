"""Configuration loading for nyx.

Two independent sources:

* ``.env``        -> secrets only (``DISCORD_TOKEN``, ``WEB_UI_PIN``), loaded
                     with python-dotenv and never committed.
* ``config.json`` -> user-editable runtime settings, validated by a Pydantic v2
                     model and hot-reloadable while the daemon is running.

``ConfigManager`` stats the file on every ``get()`` and re-parses when the mtime
changes, so the web UI can rewrite ``config.json`` and the bot picks the change
up without a restart.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any, Literal

from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"
CONFIG_PATH = ROOT / "config.json"

__all__ = [
    "ROOT",
    "ENV_PATH",
    "CONFIG_PATH",
    "PresenceConfig",
    "AfkConfig",
    "AppConfig",
    "ConfigManager",
    "load_env",
    "get_token",
    "get_pin",
    "mask_token",
    "set_env_value",
]


class PresenceConfig(BaseModel):
    """Custom rich presence shown on the self-bot account."""

    status: Literal["online", "idle", "dnd", "invisible"] = "online"
    text: str = ""
    emoji: str = ""
    state: str = ""


class AfkConfig(BaseModel):
    """Auto-reply behaviour when the account is marked away."""

    enabled: bool = False
    message: str = "I'm away right now — I'll get back to you."


class AppConfig(BaseModel):
    """Validated runtime configuration stored in ``config.json``."""

    prefix: str = "."
    presence: PresenceConfig = Field(default_factory=PresenceConfig)
    afk: AfkConfig = Field(default_factory=AfkConfig)
    keywords: list[str] = Field(default_factory=list)
    snippets: dict[str, str] = Field(default_factory=dict)
    cogs: dict[str, bool] = Field(
        default_factory=lambda: {"core": True, "automation": True}
    )
    log_level: str = "INFO"
    db_path: str = "data/nyx.db"

    @field_validator("prefix")
    @classmethod
    def _validate_prefix(cls, value: str) -> str:
        if not value or len(value) > 5:
            raise ValueError("prefix must be between 1 and 5 characters")
        if value.strip() != value:
            raise ValueError("prefix must not contain leading/trailing whitespace")
        return value

    @field_validator("keywords")
    @classmethod
    def _normalise_keywords(cls, value: list[str]) -> list[str]:
        seen: list[str] = []
        for item in value:
            word = (item or "").strip().lower()
            if word and word not in seen:
                seen.append(word)
        return seen

    @field_validator("log_level")
    @classmethod
    def _validate_level(cls, value: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = (value or "INFO").upper()
        if upper not in allowed:
            raise ValueError(f"log_level must be one of {sorted(allowed)}")
        return upper


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge ``patch`` into ``base`` in place and return it."""
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


class ConfigManager:
    """Thread-safe loader/writer with mtime-based live reload."""

    def __init__(self, path: Path | str = CONFIG_PATH) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self._cache: AppConfig | None = None
        self._mtime: float = -1.0

    # -- internals ---------------------------------------------------------
    def _current_mtime(self) -> float:
        try:
            return self.path.stat().st_mtime
        except FileNotFoundError:
            return -1.0

    # -- public API --------------------------------------------------------
    def load(self) -> AppConfig:
        """Force a parse from disk, creating a default file when absent."""
        with self._lock:
            if not self.path.exists():
                return self.save(AppConfig())
            raw_text = self.path.read_text(encoding="utf-8").strip() or "{}"
            config = AppConfig.model_validate(json.loads(raw_text))
            self._cache = config
            self._mtime = self._current_mtime()
            return config

    def get(self) -> AppConfig:
        """Return the current config, reloading if the file changed on disk."""
        with self._lock:
            if self._cache is None or self._current_mtime() != self._mtime:
                return self.load()
            return self._cache

    def save(self, config: AppConfig) -> AppConfig:
        """Atomically persist ``config`` and refresh the cache."""
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = config.model_dump()
            fd, tmp = tempfile.mkstemp(
                dir=str(self.path.parent), prefix=".config-", suffix=".json"
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(payload, handle, indent=2)
                    handle.write("\n")
                os.replace(tmp, self.path)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)
            self._cache = config
            self._mtime = self._current_mtime()
            return config

    def update(self, patch: dict[str, Any]) -> AppConfig:
        """Deep-merge ``patch`` into the live config and persist it."""
        with self._lock:
            merged = _deep_merge(self.get().model_dump(), patch)
            return self.save(AppConfig.model_validate(merged))


# ---------------------------------------------------------------------------
# Secrets (.env)
# ---------------------------------------------------------------------------
def load_env(path: Path | str = ENV_PATH, override: bool = False) -> None:
    """Load ``.env`` into ``os.environ`` (no-op when the file is missing)."""
    load_dotenv(dotenv_path=str(path), override=override)


def get_token() -> str:
    """Return the Discord user token, or an empty string in standby mode."""
    return (os.getenv("DISCORD_TOKEN") or "").strip()


def get_pin() -> str:
    """Return the web UI PIN."""
    return (os.getenv("WEB_UI_PIN") or "").strip()


def mask_token(token: str) -> str:
    """Render a token safe for display: never reveal more than the last 4."""
    if not token:
        return ""
    tail = token[-4:] if len(token) >= 4 else ""
    return f"{'*' * 20}{tail}"


def set_env_value(key: str, value: str, path: Path | str = ENV_PATH) -> None:
    """Atomically set ``key=value`` in ``.env``, preserving other entries.

    The file is rewritten through a temp file and forced to mode 600 so a
    partially written or world-readable secret never exists on disk.
    """
    env_path = Path(path)
    lines: list[str] = []
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines()

    prefix = f"{key}="
    replaced = False
    output: list[str] = []
    for line in lines:
        if line.strip().startswith(prefix):
            output.append(f"{key}={value}")
            replaced = True
        else:
            output.append(line)
    if not replaced:
        output.append(f"{key}={value}")

    env_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(env_path.parent), prefix=".env-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write("\n".join(output) + "\n")
        os.chmod(tmp, 0o600)
        os.replace(tmp, env_path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    os.chmod(env_path, 0o600)
    os.environ[key] = value
