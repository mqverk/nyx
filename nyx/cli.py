"""Command-line entrypoint shared by ``run.py`` and ``python -m nyx``."""

from __future__ import annotations

import argparse

from nyx import __version__
from nyx.bootstrap import ensure_runtime_files, print_banner
from nyx.config import get_host, get_port, get_token, load_env


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

    host = args.host or get_host()
    port = args.port or get_port()
    boot.host, boot.port = host, port

    if not args.no_banner:
        print_banner(boot, token_configured=bool(get_token()))

    import uvicorn

    from nyx.web.app import create_default_app

    uvicorn.run(
        create_default_app(announce=False),
        host=host,
        port=port,
        log_level="info",
    )
    return 0
