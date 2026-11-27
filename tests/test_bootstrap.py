"""Tests for nyx.bootstrap — zero-config first run on a fresh clone."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from nyx.bootstrap import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    PIN_LENGTH,
    ensure_runtime_files,
    generate_pin,
    print_banner,
    read_env_value,
)
from nyx.config import ROOT


@pytest.fixture
def fresh_clone(tmp_path: Path) -> Path:
    """A directory shaped like a fresh clone: templates only, no runtime files."""
    (tmp_path / "config.example.json").write_text(
        (ROOT / "config.example.json").read_text(encoding="utf-8"), encoding="utf-8"
    )
    (tmp_path / ".env.example").write_text(
        (ROOT / ".env.example").read_text(encoding="utf-8"), encoding="utf-8"
    )
    return tmp_path


# --------------------------------------------------------------------------
# PIN generation
# --------------------------------------------------------------------------
def test_generate_pin_is_twelve_chars():
    assert PIN_LENGTH == 12
    assert len(generate_pin()) == 12


def test_generate_pin_avoids_ambiguous_characters():
    for _ in range(20):
        assert not set(generate_pin()) & set("0O1IL")


def test_generate_pin_is_random():
    assert generate_pin() != generate_pin()


# --------------------------------------------------------------------------
# First-run bootstrap
# --------------------------------------------------------------------------
def test_bootstrap_creates_all_runtime_files(fresh_clone: Path):
    result = ensure_runtime_files(fresh_clone)

    assert result.created_env is True
    assert result.created_config is True
    assert result.created_data_dir is True
    assert (fresh_clone / ".env").is_file()
    assert (fresh_clone / "config.json").is_file()
    assert (fresh_clone / "data").is_dir()


def test_bootstrap_generates_twelve_char_pin(fresh_clone: Path):
    result = ensure_runtime_files(fresh_clone)
    assert len(result.pin) == 12
    assert read_env_value(fresh_clone / ".env", "WEB_UI_PIN") == result.pin


def test_bootstrap_env_is_mode_600(fresh_clone: Path):
    ensure_runtime_files(fresh_clone)
    mode = stat.S_IMODE((fresh_clone / ".env").stat().st_mode)
    assert mode == 0o600, f"expected 600, got {oct(mode)}"


def test_bootstrap_defaults_to_loopback(fresh_clone: Path):
    result = ensure_runtime_files(fresh_clone)
    assert result.host == DEFAULT_HOST == "127.0.0.1"
    assert result.port == DEFAULT_PORT == 8765
    assert result.url == "http://127.0.0.1:8765"

    text = (fresh_clone / ".env").read_text(encoding="utf-8")
    assert "WEB_UI_HOST=127.0.0.1" in text
    assert "WEB_UI_PORT=8765" in text


def test_bootstrap_leaves_token_blank(fresh_clone: Path):
    ensure_runtime_files(fresh_clone)
    assert read_env_value(fresh_clone / ".env", "DISCORD_TOKEN") == ""


def test_bootstrap_copies_config_example(fresh_clone: Path):
    ensure_runtime_files(fresh_clone)
    created = (fresh_clone / "config.json").read_text(encoding="utf-8")
    assert '"prefix"' in created


# --------------------------------------------------------------------------
# Idempotency — never clobber a configured install
# --------------------------------------------------------------------------
def test_bootstrap_is_idempotent(fresh_clone: Path):
    first = ensure_runtime_files(fresh_clone)
    second = ensure_runtime_files(fresh_clone)

    assert second.created_env is False
    assert second.created_config is False
    assert second.created_data_dir is False
    assert second.pin == first.pin  # PIN survives a restart


def test_bootstrap_preserves_existing_config(fresh_clone: Path):
    (fresh_clone / "config.json").write_text('{"prefix": "!"}', encoding="utf-8")
    ensure_runtime_files(fresh_clone)
    assert '"prefix": "!"' in (fresh_clone / "config.json").read_text(encoding="utf-8")


def test_bootstrap_reads_existing_host_and_port(fresh_clone: Path):
    (fresh_clone / ".env").write_text(
        "WEB_UI_PIN=ABCDEFGHJKMN\nWEB_UI_HOST=0.0.0.0\nWEB_UI_PORT=9000\n",
        encoding="utf-8",
    )
    result = ensure_runtime_files(fresh_clone)
    assert result.host == "0.0.0.0"
    assert result.port == 9000
    assert result.url == "http://0.0.0.0:9000"


def test_bootstrap_falls_back_to_legacy_env_keys(fresh_clone: Path):
    (fresh_clone / ".env").write_text(
        "WEB_UI_PIN=ABCDEFGHJKMN\nWEB_HOST=0.0.0.0\nWEB_PORT=8080\n",
        encoding="utf-8",
    )
    result = ensure_runtime_files(fresh_clone)
    assert result.host == "0.0.0.0"
    assert result.port == 8080


# --------------------------------------------------------------------------
# Banner
# --------------------------------------------------------------------------
def test_banner_shows_url_and_pin(fresh_clone: Path, capsys):
    result = ensure_runtime_files(fresh_clone)
    print_banner(result, token_configured=False)
    out = capsys.readouterr().out

    assert result.url in out
    assert result.pin in out
    assert "standby" in out


def test_banner_never_prints_the_token(fresh_clone: Path, capsys):
    result = ensure_runtime_files(fresh_clone)
    print_banner(result, token_configured=True)
    out = capsys.readouterr().out

    assert "configured" in out
    assert "DISCORD_TOKEN" not in out
    assert "gho_" not in out
    assert "mfa." not in out
