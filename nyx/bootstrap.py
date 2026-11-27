"""First-run bootstrap.

A fresh clone has no ``.env``, no ``config.json`` and no ``data/`` directory.
``ensure_runtime_files`` creates all three so ``python run.py`` works with zero
manual setup, and ``print_banner`` tells the user where to go and what PIN to
type — without ever echoing the Discord token.
"""

from __future__ import annotations

import os
import secrets
import shutil
from dataclasses import dataclass
from pathlib import Path

from nyx.config import ROOT

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
PIN_LENGTH = 12

# No 0/O/1/I/L — the PIN gets typed by hand from a terminal.
PIN_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def generate_pin(length: int = PIN_LENGTH) -> str:
    """Generate a random, unambiguous PIN."""
    return "".join(secrets.choice(PIN_ALPHABET) for _ in range(length))


def read_env_value(path: Path, key: str) -> str:
    """Read a single ``KEY=value`` from an env file (no side effects)."""
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or "=" not in stripped:
                continue
            name, _, value = stripped.partition("=")
            if name.strip() == key:
                return value.strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


@dataclass
class BootstrapResult:
    """What the bootstrap created, plus the values the user needs."""

    created_env: bool = False
    created_config: bool = False
    created_data_dir: bool = False
    pin: str = ""
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"


def _write_env(env_path: Path, template: Path, pin: str) -> None:
    lines: list[str] = []
    if template.exists():
        lines = template.read_text(encoding="utf-8").splitlines()

    out: list[str] = []
    seen = {"pin": False, "host": False, "port": False}
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("WEB_UI_PIN="):
            out.append(f"WEB_UI_PIN={pin}")
            seen["pin"] = True
        elif stripped.startswith(("WEB_UI_HOST=", "WEB_HOST=")):
            out.append(f"WEB_UI_HOST={DEFAULT_HOST}")
            seen["host"] = True
        elif stripped.startswith(("WEB_UI_PORT=", "WEB_PORT=")):
            out.append(f"WEB_UI_PORT={DEFAULT_PORT}")
            seen["port"] = True
        else:
            out.append(line)

    if not seen["pin"]:
        out.append(f"WEB_UI_PIN={pin}")
    if not seen["host"]:
        out.append(f"WEB_UI_HOST={DEFAULT_HOST}")
    if not seen["port"]:
        out.append(f"WEB_UI_PORT={DEFAULT_PORT}")

    env_path.write_text("\n".join(out) + "\n", encoding="utf-8")
    os.chmod(env_path, 0o600)


def ensure_runtime_files(root: Path | str | None = None) -> BootstrapResult:
    """Create ``.env``, ``config.json`` and ``data/`` when they are missing.

    Idempotent: existing files are never overwritten, so a configured install
    keeps its PIN, token, and settings.
    """
    base = Path(root) if root is not None else ROOT
    env_path = base / ".env"
    config_path = base / "config.json"
    data_dir = base / "data"
    result = BootstrapResult()

    if not data_dir.exists():
        data_dir.mkdir(parents=True, exist_ok=True)
        result.created_data_dir = True

    if not config_path.exists():
        example = base / "config.example.json"
        if example.exists():
            shutil.copyfile(example, config_path)
        else:
            config_path.write_text("{}\n", encoding="utf-8")
        result.created_config = True

    if not env_path.exists():
        pin = generate_pin()
        _write_env(env_path, base / ".env.example", pin)
        result.created_env = True
        result.pin = pin
    else:
        result.pin = read_env_value(env_path, "WEB_UI_PIN")
        result.host = (
            read_env_value(env_path, "WEB_UI_HOST")
            or read_env_value(env_path, "WEB_HOST")
            or DEFAULT_HOST
        )
        raw_port = (
            read_env_value(env_path, "WEB_UI_PORT")
            or read_env_value(env_path, "WEB_PORT")
            or str(DEFAULT_PORT)
        )
        try:
            result.port = int(raw_port)
        except ValueError:
            result.port = DEFAULT_PORT

    return result


def print_banner(result: BootstrapResult, *, token_configured: bool = False) -> None:
    """Print the startup banner. Never prints the Discord token."""
    first_run = result.created_env or result.created_config
    lines = [
        "",
        "  ┌──────────────────────────────────────────────┐",
        "  │  nyx — discord self-bot control panel        │",
        "  └──────────────────────────────────────────────┘",
        f"   web ui   {result.url}",
        f"   pin      {result.pin or '(not set)'}",
        f"   token    {'configured' if token_configured else 'not set — standby mode'}",
    ]
    if first_run:
        lines.append("   note     first run: .env and config.json were generated")
    lines.append("")
    print("\n".join(lines), flush=True)
