"""Rich terminal startup screen for nyx.

Degrades to plain text when ``rich`` is unavailable, so a broken install still
boots the daemon instead of crashing on an import.
"""

from __future__ import annotations

import importlib
import pkgutil
import sqlite3
from pathlib import Path
from typing import Any

try:  # pragma: no cover - presence check only
    import rich  # noqa: F401

    RICH_AVAILABLE = True
except ImportError:  # pragma: no cover
    RICH_AVAILABLE = False

BANNER = [
    "███╗   ██╗  ██╗   ██╗  ██╗  ██╗",
    "████╗  ██║  ╚██╗ ██╔╝  ╚██╗██╔╝",
    "██╔██╗ ██║   ╚████╔╝    ╚███╔╝ ",
    "██║╚██╗██║    ╚██╔╝     ██╔██╗ ",
    "██║ ╚████║     ██║     ██╔╝ ██╗",
    "╚═╝  ╚═══╝     ╚═╝     ╚═╝  ╚═╝",
]

GRADIENT = ("#7c5cff", "#8b6bff", "#9a7aff", "#5f8bff", "#3fb6e8", "#00e0b8")


def mask_pin(pin: str) -> str:
    """Show only the last two characters of a PIN."""
    if not pin:
        return "(not set)"
    if len(pin) <= 2:
        return "•" * len(pin)
    return "•" * (len(pin) - 2) + pin[-2:]


def database_status(path: Path) -> tuple[bool, str]:
    """Return (ready, human description) for the SQLite file."""
    try:
        if not path.exists():
            return False, "not initialized"
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            cursor = connection.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
            )
            tables = int(cursor.fetchone()[0])
        finally:
            connection.close()
        return tables > 0, f"{tables} tables"
    except Exception:  # noqa: BLE001
        return False, "unreadable"


def discover_cogs() -> list[str]:
    """List cog modules shipped in nyx.bot.cogs."""
    try:
        package = importlib.import_module("nyx.bot.cogs")
        return sorted(
            module.name
            for module in pkgutil.iter_modules(package.__path__)
            if not module.name.startswith("_")
        )
    except Exception:  # noqa: BLE001
        return []


def render_startup(
    *,
    mode: str,
    url: str,
    pin: str,
    show_pin: bool,
    prefix: str,
    cogs: list[str],
    db_path: Path,
    db_ready: bool,
    db_detail: str = "",
    first_run: bool = False,
    console: Any | None = None,
) -> None:
    """Render the NYX banner and a framed status card."""
    if not RICH_AVAILABLE:
        _render_plain(
            mode=mode, url=url, pin=pin, show_pin=show_pin, prefix=prefix,
            cogs=cogs, db_path=db_path, db_ready=db_ready, db_detail=db_detail,
        )
        return

    from rich.align import Align
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    out = console or Console()

    banner = Text()
    for index, line in enumerate(BANNER):
        banner.append(line + "\n", style=f"bold {GRADIENT[min(index, len(GRADIENT) - 1)]}")

    out.print()
    out.print(banner)
    out.print(Align.center(Text("discord self-bot · web control panel", style="dim")))
    out.print()

    table = Table.grid(padding=(0, 2))
    table.add_column(style="dim", justify="right", no_wrap=True)
    table.add_column(overflow="fold")

    active = mode.upper() == "ACTIVE"
    table.add_row("bot mode", Text(mode.upper(), style="bold green" if active else "bold yellow"))
    table.add_row("dashboard", Text(url, style="bold cyan"))
    table.add_row(
        "web ui pin",
        Text(pin if show_pin else mask_pin(pin), style="bold white" if show_pin else "dim"),
    )
    table.add_row("prefix", Text(prefix, style="magenta"))
    table.add_row("cogs", Text(", ".join(cogs) if cogs else "none", style="white"))
    table.add_row(
        "database",
        Text(
            f"{db_path.name} · {db_detail or ('ready' if db_ready else 'missing')}",
            style="green" if db_ready else "red",
        ),
    )

    title = "nyx · first run" if first_run else "nyx"
    out.print(
        Panel(
            table,
            title=f"[bold magenta]{title}[/]",
            border_style="magenta",
            padding=(1, 2),
        )
    )
    if not show_pin:
        out.print(Text("  pin hidden — pass --show-pin to reveal it", style="dim"))
    out.print()


def _render_plain(
    *, mode: str, url: str, pin: str, show_pin: bool, prefix: str,
    cogs: list[str], db_path: Path, db_ready: bool, db_detail: str,
) -> None:
    print()
    for line in BANNER:
        print("  " + line)
    print("  discord self-bot · web control panel")
    print("  " + "-" * 46)
    print(f"  bot mode   {mode.upper()}")
    print(f"  dashboard  {url}")
    print(f"  web ui pin {pin if show_pin else mask_pin(pin)}")
    print(f"  prefix     {prefix}")
    print(f"  cogs       {', '.join(cogs) if cogs else 'none'}")
    print(f"  database   {db_path.name} · {db_detail or ('ready' if db_ready else 'missing')}")
    print()
