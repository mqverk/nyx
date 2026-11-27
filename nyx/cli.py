"""Command-line entrypoint shared by ``run.py`` and ``python -m nyx``."""

from __future__ import annotations

import argparse
import os

from nyx import __version__
from nyx.bootstrap import ensure_runtime_files, generate_pin
from nyx.cli_ui import database_status, discover_cogs, render_startup
from nyx.config import (
    CONFIG_PATH,
    ConfigManager,
    get_host,
    get_pin,
    get_port,
    get_token,
    load_env,
    resolve_path,
    set_env_value,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nyx",
        description="Modular Discord self-bot with a local web control panel.",
        epilog="Config lives in .env and config.json next to this package.",
    )
    parser.add_argument(
        "--host",
        default=None,
        help="bind address, overrides WEB_UI_HOST (default 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="bind port, overrides WEB_UI_PORT (default 8765)",
    )
    parser.add_argument(
        "--show-pin",
        action="store_true",
        help="print the web UI PIN on startup",
    )
    parser.add_argument(
        "--reset-pin",
        action="store_true",
        help="generate a new web UI PIN, save it to .env, and print it",
    )
    parser.add_argument(
        "--no-banner",
        action="store_true",
        help="suppress the startup banner",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"nyx {__version__}",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    boot = ensure_runtime_files()
    load_env()

    if args.reset_pin:
        new_pin = generate_pin()
        set_env_value("WEB_UI_PIN", new_pin)
        os.environ["WEB_UI_PIN"] = new_pin
        boot.pin = new_pin
        boot.created_env = True  # treat as first run so the PIN is displayed

    host = args.host or get_host()
    port = args.port or get_port()

    if not args.no_banner:
        config = ConfigManager(CONFIG_PATH)
        db_path = resolve_path(config.get().db_path)
        db_ready, db_detail = database_status(db_path)
        render_startup(
            mode="ACTIVE" if get_token() else "STANDBY",
            url=f"http://{host}:{port}",
            pin=get_pin(),
            show_pin=args.show_pin or args.reset_pin or boot.created_env,
            prefix=config.get().prefix,
            cogs=discover_cogs(),
            db_path=db_path,
            db_ready=db_ready,
            db_detail=db_detail,
            first_run=boot.created_env,
        )

    import uvicorn

    from nyx.web.app import create_default_app

    uvicorn.run(
        create_default_app(announce=False),
        host=host,
        port=port,
        log_level="info",
    )
    return 0
