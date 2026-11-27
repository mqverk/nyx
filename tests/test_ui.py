"""HTTP verification for the dashboard: renders cleanly and every frontend
API call maps to a real backend route."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from nyx.bot.runner import BotRunner, LogBus
from nyx.config import ConfigManager
from nyx.db import Database
from nyx.web.app import STATIC_DIR, create_app

INDEX = STATIC_DIR / "index.html"


@pytest.fixture
def app_client(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("WEB_UI_PIN", "1" * 6)
    monkeypatch.setenv("DISCORD_TOKEN", "")
    monkeypatch.setattr("nyx.web.app.ENV_PATH", tmp_path / ".env")
    (tmp_path / ".env").write_text("DISCORD_TOKEN=\n")

    cfg = ConfigManager(tmp_path / "config.json")
    db = Database(tmp_path / "t.db")
    bus = LogBus()
    runner = BotRunner(cfg, log_bus=bus, token_provider=lambda: "", db=db)
    app = create_app(runner=runner, db=db, config=cfg, log_bus=bus)
    with TestClient(app) as client:
        yield client, app


def test_index_file_exists():
    assert INDEX.exists(), "dashboard html missing"


def test_root_renders_cleanly(app_client):
    client, _ = app_client
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    body = response.text
    assert "<title>nyx" in body
    assert body.count("<html") == 1
    assert body.count("</html>") == 1


@pytest.mark.parametrize(
    "element_id",
    [
        "s-status", "s-ping", "s-uptime", "s-guilds", "s-user",
        "btn-start", "btn-stop", "btn-restart",
        "console", "cogs", "events", "evq",
        "token", "token-save", "p-status", "p-text", "p-emoji",
        "afk-on", "afk-msg", "kw", "snip", "prefix", "pin",
        "bookmarks", "bmq", "todos", "todo-input", "cycle-presets", "cycle-interval",
    ],
)
def test_dashboard_contains_required_controls(app_client, element_id):
    _, _ = app_client
    html = INDEX.read_text(encoding="utf-8")
    assert f'id="{element_id}"' in html, f"missing control #{element_id}"


def _frontend_paths() -> set[str]:
    html = INDEX.read_text(encoding="utf-8")
    raw = set(re.findall(r"[\"'`](/api/[^\"'`\s]*)[\"'`]", html))
    raw |= set(re.findall(r"[\"'`](/ws/[^\"'`\s]*)[\"'`]", html))
    normalised = set()
    for path in raw:
        path = path.split("?")[0]
        path = re.sub(r"\$\{[^}]*\}", "{p}", path)
        normalised.add(path)
    return normalised


def _backend_paths(app) -> set[str]:
    paths = set()
    for route in app.routes:
        path = getattr(route, "path", None)
        if not path:
            continue
        paths.add(re.sub(r"\{[^}]+\}", "{p}", path))
    return paths


def test_every_frontend_call_matches_a_backend_route(app_client):
    _, app = app_client
    frontend = _frontend_paths()
    backend = _backend_paths(app)

    assert frontend, "no API calls detected in the dashboard"

    # a frontend placeholder {p} may match any single backend path segment,
    # but the literal parts must line up exactly (so a typo still fails)
    missing = []
    for path in sorted(frontend):
        pattern = "^" + re.sub(r"\{p\}", "[^/]+", re.escape(path).replace(r"\{p\}", "{p}")) + "$"
        if not any(re.match(pattern, route) for route in backend):
            missing.append(path)
    assert not missing, f"frontend calls with no backend route: {missing}"


def test_backend_exposes_expected_core_routes(app_client):
    _, app = app_client
    backend = _backend_paths(app)
    for expected in (
        "/api/login", "/api/logout", "/api/session", "/api/status",
        "/api/token", "/api/bot/start", "/api/bot/stop", "/api/bot/restart",
        "/api/cogs", "/api/cogs/{p}/toggle",
        "/api/cogs/{p}/reload", "/api/config", "/api/events", "/ws/logs",
        "/api/bookmarks", "/api/bookmarks/{p}",
        "/api/todos", "/api/todos/{p}/done", "/api/todos/{p}",
    ):
        assert expected in backend, f"missing backend route {expected}"


def test_dashboard_has_no_inline_secrets():
    html = INDEX.read_text(encoding="utf-8")
    assert not re.search(r"[A-Za-z0-9_-]{23,28}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{27,}", html)
    assert "DISCORD_TOKEN=" not in html


def test_dashboard_uses_relative_api_paths():
    """Absolute URLs would break behind an SSH tunnel on a different host."""
    html = INDEX.read_text(encoding="utf-8")
    assert "http://127.0.0.1:8765" not in html
    assert "http://localhost:8765" not in html
