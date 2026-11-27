"""Tests for nyx.config — loading, validation, live reload, and secret handling."""

from __future__ import annotations

import json
import os
import stat
import time
from pathlib import Path

import pytest
from pydantic import ValidationError

from nyx.config import (
    AppConfig,
    ConfigManager,
    mask_token,
    set_env_value,
)


# --------------------------------------------------------------------------
# Schema validation
# --------------------------------------------------------------------------
def test_defaults_are_sane():
    cfg = AppConfig()
    assert cfg.prefix == "."
    assert cfg.presence.status == "online"
    assert cfg.afk.enabled is False
    assert cfg.cogs == {"core": True, "automation": True}
    assert cfg.db_path.endswith(".db")


def test_prefix_validation_rejects_empty_and_long():
    with pytest.raises(ValidationError):
        AppConfig(prefix="")
    with pytest.raises(ValidationError):
        AppConfig(prefix="toolong")
    assert AppConfig(prefix="!").prefix == "!"


def test_keywords_are_normalised_and_deduped():
    cfg = AppConfig(keywords=["  Hello ", "hello", "WORLD", "", "  "])
    assert cfg.keywords == ["hello", "world"]


def test_log_level_is_uppercased_and_validated():
    assert AppConfig(log_level="debug").log_level == "DEBUG"
    with pytest.raises(ValidationError):
        AppConfig(log_level="LOUD")


def test_invalid_presence_status_rejected():
    with pytest.raises(ValidationError):
        AppConfig(presence={"status": "flying"})


# --------------------------------------------------------------------------
# ConfigManager: create / load / save / update
# --------------------------------------------------------------------------
def test_creates_default_file_when_missing(tmp_path: Path):
    path = tmp_path / "config.json"
    mgr = ConfigManager(path)
    cfg = mgr.get()
    assert path.exists()
    assert cfg.prefix == "."
    assert json.loads(path.read_text())["prefix"] == "."


def test_load_round_trip(tmp_path: Path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"prefix": "?", "keywords": ["Nyx"]}))
    mgr = ConfigManager(path)
    cfg = mgr.get()
    assert cfg.prefix == "?"
    assert cfg.keywords == ["nyx"]


def test_update_deep_merges_and_persists(tmp_path: Path):
    path = tmp_path / "config.json"
    mgr = ConfigManager(path)
    mgr.get()
    cfg = mgr.update({"presence": {"text": "listening"}, "afk": {"enabled": True}})
    assert cfg.presence.text == "listening"
    assert cfg.presence.status == "online"  # untouched sibling preserved
    assert cfg.afk.enabled is True
    on_disk = json.loads(path.read_text())
    assert on_disk["presence"]["text"] == "listening"


def test_live_reload_picks_up_external_edit(tmp_path: Path):
    path = tmp_path / "config.json"
    mgr = ConfigManager(path)
    assert mgr.get().prefix == "."

    # simulate the web UI rewriting the file behind our back
    time.sleep(0.01)
    path.write_text(json.dumps({"prefix": "$"}))
    os.utime(path, (time.time() + 5, time.time() + 5))

    assert mgr.get().prefix == "$"


def test_invalid_json_on_disk_raises(tmp_path: Path):
    path = tmp_path / "config.json"
    path.write_text("{not json")
    with pytest.raises(json.JSONDecodeError):
        ConfigManager(path).get()


def test_save_is_atomic_no_temp_left_behind(tmp_path: Path):
    path = tmp_path / "config.json"
    mgr = ConfigManager(path)
    mgr.save(AppConfig(prefix="!"))
    leftovers = [p.name for p in tmp_path.iterdir() if p.name.startswith(".config-")]
    assert leftovers == []


# --------------------------------------------------------------------------
# Secrets
# --------------------------------------------------------------------------
def test_mask_token_never_reveals_more_than_tail():
    # assembled at runtime so the literal never resembles a real token
    token = ".".join(
        ["MTIzNDU2Nzg5MDEyMzQ1Njc4", "GaBcDe", "abcdefghijklmnopqrstuvwxyz0123456789"]
    )
    masked = mask_token(token)
    assert masked.endswith(token[-4:])
    assert token[:20] not in masked
    assert masked.count("*") >= 20
    assert mask_token("") == ""


def test_set_env_value_is_atomic_and_mode_600(tmp_path: Path):
    env = tmp_path / ".env"
    pin = "1" * 6
    env.write_text(f"DISCORD_TOKEN=\nWEB_UI_PIN={pin}\n")
    set_env_value("DISCORD_TOKEN", "secret-token-value", env)

    text = env.read_text()
    assert "DISCORD_TOKEN=secret-token-value" in text
    assert f"WEB_UI_PIN={pin}" in text  # other keys preserved
    assert text.count("DISCORD_TOKEN=") == 1  # replaced, not appended
    mode = stat.S_IMODE(env.stat().st_mode)
    assert mode == 0o600, f"expected 600, got {oct(mode)}"


def test_set_env_value_appends_new_key(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text(f"WEB_UI_PIN={'2' * 6}\n")
    set_env_value("DISCORD_TOKEN", "abc", env)
    assert "DISCORD_TOKEN=abc" in env.read_text()
