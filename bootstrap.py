#!/usr/bin/env python3
"""nyx zero-dependency bootstrapper.

Runs on a bare Python install, before any pip package exists. Standard library
only: sys, os, venv, subprocess, secrets, shutil, pathlib, time.

    python bootstrap.py                  # interactive
    python bootstrap.py --non-interactive  # for CI / automation
"""

from __future__ import annotations

import os
import secrets
import shutil
import subprocess
import sys
import time
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV_DIR = ROOT / "venv"
MIN_PYTHON = (3, 10)
PIN_LENGTH = 12
PIN_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
TOTAL_STEPS = 5

UNICODE_ART = [
    "███╗   ██╗  ██╗   ██╗  ██╗  ██╗",
    "████╗  ██║  ╚██╗ ██╔╝  ╚██╗██╔╝",
    "██╔██╗ ██║   ╚████╔╝    ╚███╔╝ ",
    "██║╚██╗██║    ╚██╔╝     ██╔██╗ ",
    "██║ ╚████║     ██║     ██╔╝ ██╗",
    "╚═╝  ╚═══╝     ╚═╝     ╚═╝  ╚═╝",
]

ASCII_ART = [
    " _   _ __   __ __  __",
    "| \\ | |\\ \\ / / \\ \\/ /",
    "|  \\| | \\ V /   \\  / ",
    "| |\\  |  | |    /  \\ ",
    "|_| \\_|  |_|   /_/\\_\\",
]

# purple -> cyan gradient, one colour per banner line
GRADIENT = (99, 105, 111, 117, 123, 129)


class Style:
    """Tiny ANSI helper with a plain-text fallback."""

    def __init__(self, color: bool, unicode_ok: bool) -> None:
        self.color = color
        self.unicode_ok = unicode_ok

    def __call__(self, code: str, text: str) -> str:
        if not self.color:
            return text
        return f"\033[{code}m{text}\033[0m"

    def fg(self, index: int, text: str) -> str:
        return self(f"38;5;{index}", text)

    def bold(self, text: str) -> str:
        return self("1", text)

    def dim(self, text: str) -> str:
        return self("2", text)

    @property
    def tick(self) -> str:
        return "✓" if self.unicode_ok else "+"

    @property
    def cross(self) -> str:
        return "✗" if self.unicode_ok else "x"

    @property
    def bullet(self) -> str:
        return "·" if self.unicode_ok else "-"


def supports_color() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    try:
        return bool(sys.stdout.isatty())
    except Exception:
        return False


def supports_unicode() -> bool:
    encoding = (getattr(sys.stdout, "encoding", "") or "").lower()
    return "utf" in encoding


def generate_pin(length: int = PIN_LENGTH) -> str:
    return "".join(secrets.choice(PIN_ALPHABET) for _ in range(length))


def venv_python() -> Path:
    if os.name == "nt":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def venv_pip() -> Path:
    if os.name == "nt":
        return VENV_DIR / "Scripts" / "pip.exe"
    return VENV_DIR / "bin" / "pip"


def read_env_value(path: Path, key: str) -> str:
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


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
def render_banner(style: Style) -> None:
    art = UNICODE_ART if style.unicode_ok else ASCII_ART
    print()
    for index, line in enumerate(art):
        colour = GRADIENT[min(index, len(GRADIENT) - 1)]
        print("  " + style.fg(colour, line))
    print()
    tagline = "discord self-bot  ·  web control panel"
    print("  " + style.dim(tagline))
    print("  " + style.dim("─" * 46 if style.unicode_ok else "-" * 46))
    print()


def step_header(style: Style, index: int, label: str) -> None:
    counter = style.fg(117, f"[{index}/{TOTAL_STEPS}]")
    print(f"  {counter} {style.bold(label)}")


def step_ok(style: Style, message: str) -> None:
    print(f"        {style.fg(42, style.tick)} {style.dim(message)}")


def step_fail(style: Style, message: str) -> None:
    print(f"        {style.fg(203, style.cross)} {message}")


def box(style: Style, lines: list[str], title: str = "") -> None:
    """Draw a framed box around plain-text lines."""
    if style.unicode_ok:
        tl, tr, bl, br, h, v = "┌", "┐", "└", "┘", "─", "│"
    else:
        tl, tr, bl, br, h, v = "+", "+", "+", "+", "-", "|"

    inner = max([len(line) for line in lines] + [len(title) + 2, 40])
    edge = style.fg(99, h * (inner + 2))

    print("  " + style.fg(99, tl) + edge + style.fg(99, tr))
    if title:
        pad = inner - len(title) - 1
        print(
            "  " + style.fg(99, v) + " "
            + style.fg(129, title) + " " * max(0, pad)
            + style.fg(99, v)
        )
        print("  " + style.fg(99, v) + style.dim(h * (inner + 2)) + style.fg(99, v))
    for line in lines:
        pad = inner - len(line)
        print("  " + style.fg(99, v) + " " + line + " " * pad + " " + style.fg(99, v))
    print("  " + style.fg(99, bl) + edge + style.fg(99, br))


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------
def check_python(style: Style) -> bool:
    step_header(style, 1, "checking Python version")
    current = ".".join(str(part) for part in sys.version_info[:3])
    if sys.version_info < MIN_PYTHON:
        step_fail(
            style,
            f"Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ required, found {current}",
        )
        return False
    step_ok(style, f"Python {current}")
    if sys.version_info >= (3, 13):
        print(
            "        "
            + style.fg(214, "note")
            + style.dim(
                " discord.py-self is tested on 3.10–3.12; on 3.13+ the bot may not import."
            )
        )
        print(
            "        "
            + style.dim("      use python3.12 bootstrap.py if the bot fails to start.")
        )
    return True


def create_venv(style: Style) -> bool:
    step_header(style, 2, "creating virtual environment")
    if venv_python().exists():
        step_ok(style, "venv/ already exists — reusing")
        return True
    try:
        venv.EnvBuilder(with_pip=True, clear=False).create(VENV_DIR)
    except Exception as exc:  # noqa: BLE001
        step_fail(style, f"could not create venv: {exc}")
        return False
    step_ok(style, f"created {VENV_DIR.name}/")
    return True


def install_requirements(style: Style) -> bool:
    step_header(style, 3, "installing requirements")
    requirements = ROOT / "requirements.txt"
    if not requirements.exists():
        step_fail(style, "requirements.txt is missing")
        return False
    pip = venv_pip()
    if not pip.exists():
        step_fail(style, "pip not found inside venv")
        return False

    started = time.time()
    try:
        subprocess.run(
            [str(pip), "install", "--quiet", "--upgrade", "pip"],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
        )
        subprocess.run(
            [str(pip), "install", "--quiet", "-r", str(requirements)],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
        )
    except subprocess.CalledProcessError as exc:
        step_fail(style, f"pip install failed (exit {exc.returncode})")
        return False
    step_ok(style, f"installed in {time.time() - started:.1f}s")
    return True


def write_runtime_files(style: Style) -> tuple[bool, str]:
    step_header(style, 4, "generating config.json and .env")

    config_path = ROOT / "config.json"
    if config_path.exists():
        step_ok(style, "config.json already present")
    else:
        example = ROOT / "config.example.json"
        if example.exists():
            shutil.copyfile(example, config_path)
        else:
            config_path.write_text("{}\n", encoding="utf-8")
        step_ok(style, "config.json created")

    env_path = ROOT / ".env"
    if env_path.exists():
        pin = read_env_value(env_path, "WEB_UI_PIN")
        step_ok(style, ".env already present — PIN preserved")
        return True, pin

    pin = generate_pin()
    template = ROOT / ".env.example"
    lines = template.read_text(encoding="utf-8").splitlines() if template.exists() else []

    out: list[str] = []
    seen = {"pin": False, "host": False, "port": False}
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("WEB_UI_PIN="):
            out.append(f"WEB_UI_PIN={pin}")
            seen["pin"] = True
        elif stripped.startswith(("WEB_UI_HOST=", "WEB_HOST=")):
            out.append("WEB_UI_HOST=127.0.0.1")
            seen["host"] = True
        elif stripped.startswith(("WEB_UI_PORT=", "WEB_PORT=")):
            out.append("WEB_UI_PORT=8765")
            seen["port"] = True
        else:
            out.append(line)

    if not seen["pin"]:
        out.append(f"WEB_UI_PIN={pin}")
    if not seen["host"]:
        out.append("WEB_UI_HOST=127.0.0.1")
    if not seen["port"]:
        out.append("WEB_UI_PORT=8765")

    env_path.write_text("\n".join(out) + "\n", encoding="utf-8")
    try:
        os.chmod(env_path, 0o600)
    except OSError:
        pass
    step_ok(style, ".env created (mode 600)")
    return True, pin


def init_database(style: Style) -> bool:
    step_header(style, 5, "initializing SQLite tables")
    code = (
        "import asyncio\n"
        "from nyx.db import Database\n"
        "async def main():\n"
        "    db = Database('data/nyx.db')\n"
        "    await db.connect()\n"
        "    await db.close()\n"
        "asyncio.run(main())\n"
    )
    try:
        subprocess.run(
            [str(venv_python()), "-c", code],
            cwd=str(ROOT), check=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT,
        )
    except subprocess.CalledProcessError as exc:
        step_fail(style, f"schema init failed (exit {exc.returncode})")
        return False
    step_ok(style, "data/nyx.db ready")
    return True


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    non_interactive = "--non-interactive" in args or "-y" in args
    if any(arg in ("-h", "--help") for arg in args):
        print(__doc__)
        return 0

    style = Style(supports_color(), supports_unicode())
    render_banner(style)

    if not check_python(style):
        return 1
    if not create_venv(style):
        return 1
    if not install_requirements(style):
        return 1
    ok, pin = write_runtime_files(style)
    if not ok:
        return 1
    if not init_database(style):
        return 1

    python_cmd = (
        "venv\\Scripts\\python run.py" if os.name == "nt" else "venv/bin/python run.py"
    )
    print()
    box(
        style,
        [
            f"web ui    {style.fg(123, 'http://127.0.0.1:8765')}",
            f"pin       {style.bold(style.fg(222, pin))}",
            f"launch    {style.fg(117, python_cmd)}",
        ],
        title="ready",
    )
    print()
    print("  " + style.dim("next: run the launch command, open the URL, enter the PIN."))
    print("  " + style.dim("then paste a Discord token in the dashboard to go live."))
    print()

    if not non_interactive:
        try:
            input("  press enter to finish ")
        except (EOFError, KeyboardInterrupt):
            print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
