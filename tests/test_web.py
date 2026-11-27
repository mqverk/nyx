"""Integration tests for nyx.web.app — auth, config, token masking, WebSocket."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from nyx.bot.runner import BotRunner, LogBus
from nyx.config import ConfigManager
from nyx.db import Database
from nyx.web.app import create_app


@pytest.fixture
def env(tmp_path: Path, monkeypatch):
    pin = "1" * 6
    monkeypatch.setenv("WEB_UI_PIN", pin)
    monkeypatch.setenv("DISCORD_TOKEN", "")

    env_path = tmp_path / ".env"
    env_path.write_text("DISCORD_TOKEN=\n")
    monkeypatch.setattr("nyx.web.app.ENV_PATH", env_path)

    cfg = ConfigManager(tmp_path / "config.json")
    db = Database(tmp_path / "test.db")
    bus = LogBus()
    runner = BotRunner(cfg, log_bus=bus, token_provider=lambda: "", db=db)
    app = create_app(runner=runner, db=db, config=cfg, log_bus=bus)

    with TestClient(app) as client:
        yield SimpleNamespace(
            client=client, pin=pin, cfg=cfg, bus=bus, db=db,
            runner=runner, env_path=env_path,
        )


def login(env) -> None:
    response = env.client.post("/api/login", json={"pin": env.pin})
    assert response.status_code == 200, response.text


# --------------------------------------------------------------------------
# Auth
# --------------------------------------------------------------------------
def test_unauthenticated_requests_rejected(env):
    for path in ("/api/status", "/api/config", "/api/token", "/api/cogs", "/api/events"):
        assert env.client.get(path).status_code == 401, path


def test_wrong_pin_rejected(env):
    response = env.client.post("/api/login", json={"pin": "9" * 6})
    assert response.status_code == 401
    assert env.client.get("/api/status").status_code == 401


def test_login_grants_access(env):
    login(env)
    assert env.client.get("/api/session").status_code == 200
    assert env.client.get("/api/status").status_code == 200


def test_logout_revokes_session(env):
    login(env)
    assert env.client.post("/api/logout").status_code == 200
    assert env.client.get("/api/status").status_code == 401


def test_logout_clears_cookie_client_side(env):
    login(env)
    response = env.client.post("/api/logout")
    cookie_header = response.headers.get("set-cookie", "")
    assert "nyx_session=" in cookie_header
    assert "Max-Age=0" in cookie_header or 'nyx_session=""' in cookie_header


def test_session_cookie_is_httponly(env):
    response = env.client.post("/api/login", json={"pin": env.pin})
    cookie_header = response.headers.get("set-cookie", "")
    assert "HttpOnly" in cookie_header
    assert "SameSite=strict" in cookie_header


# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------
def test_config_round_trip(env):
    login(env)
    original = env.client.get("/api/config").json()
    assert original["prefix"] == "."

    updated = env.client.put(
        "/api/config", json={"patch": {"prefix": "!", "afk": {"enabled": True}}}
    )
    assert updated.status_code == 200
    body = updated.json()
    assert body["prefix"] == "!"
    assert body["afk"]["enabled"] is True
    assert body["presence"]["status"] == "online"  # untouched sibling

    assert env.client.get("/api/config").json()["prefix"] == "!"


def test_config_rejects_invalid_patch(env):
    login(env)
    response = env.client.put("/api/config", json={"patch": {"prefix": "toolong"}})
    assert response.status_code == 422


# --------------------------------------------------------------------------
# Token masking
# --------------------------------------------------------------------------
def test_token_status_when_unset(env):
    login(env)
    body = env.client.get("/api/token").json()
    assert body == {"configured": False, "masked": ""}


def test_token_update_masks_and_never_echoes_raw(env):
    login(env)
    raw = ".".join(["MTIzNDU2Nzg5MDEyMzQ1Njc4", "GaBcDe", "abcdefghijklmnopqrstuvwxyz0123456789"])

    response = env.client.put("/api/token", json={"token": raw})
    assert response.status_code == 200
    assert raw not in response.text
    body = response.json()
    assert body["configured"] is True
    assert body["masked"].endswith(raw[-4:])
    assert raw[:20] not in body["masked"]

    # written to .env, not held in the response
    assert f"DISCORD_TOKEN={raw}" in env.env_path.read_text()

    # and a subsequent GET still only exposes the mask
    status = env.client.get("/api/token").json()
    assert status["configured"] is True
    assert raw not in str(status)


def test_token_update_rejects_empty(env):
    login(env)
    assert env.client.put("/api/token", json={"token": "   "}).status_code == 400


# --------------------------------------------------------------------------
# Bot control
# --------------------------------------------------------------------------
def test_bot_start_enters_standby_without_token(env):
    login(env)
    body = env.client.post("/api/bot/start").json()
    assert body["status"] == "standby"


def test_bot_stop_and_restart(env):
    login(env)
    env.client.post("/api/bot/start")
    assert env.client.post("/api/bot/stop").json()["status"] == "stopped"
    assert env.client.post("/api/bot/restart").json()["status"] == "standby"


def test_status_includes_token_flag(env):
    login(env)
    body = env.client.get("/api/status").json()
    assert body["token_configured"] is False
    assert "status" in body


# --------------------------------------------------------------------------
# Cogs
# --------------------------------------------------------------------------
def test_cogs_list_reports_discovered_cogs(env):
    login(env)
    cogs = env.client.get("/api/cogs").json()
    names = {c["name"] for c in cogs}
    assert {"core", "automation"} <= names
    assert all(c["enabled"] for c in cogs)


def test_cog_toggle_persists_to_config(env):
    login(env)
    response = env.client.post("/api/cogs/automation/toggle", json={"enabled": False})
    assert response.status_code == 200
    assert env.cfg.get().cogs["automation"] is False


def test_cog_toggle_unknown_returns_404(env):
    login(env)
    assert env.client.post("/api/cogs/nope/toggle", json={"enabled": True}).status_code == 404


def test_cog_reload_requires_running_bot(env):
    login(env)
    assert env.client.post("/api/cogs/core/reload").status_code == 409


def test_cog_reload_unknown_name_returns_404(env):
    login(env)
    assert env.client.post("/api/cogs/nope/reload").status_code == 404
    assert env.client.post("/api/cogs/..%2F..%2Fetc%2Fpasswd/reload").status_code == 404


# --------------------------------------------------------------------------
# Events
# --------------------------------------------------------------------------
def test_events_search_returns_both_tables(env):
    login(env)
    body = env.client.get("/api/events?q=hello").json()
    assert set(body) == {"keyword_hits", "snipes"}


# --------------------------------------------------------------------------
# Bookmarks
# --------------------------------------------------------------------------
def _seed_bookmark(path, content="saved message", author="alice"):
    import sqlite3
    import time as _t
    con = sqlite3.connect(str(path))
    con.execute(
        "INSERT INTO bookmarks (ts, content, author_name, channel_name) VALUES (?,?,?,?)",
        (_t.time(), content, author, "general"),
    )
    con.commit()
    con.close()


def test_bookmarks_empty(env):
    login(env)
    assert env.client.get("/api/bookmarks").json() == {"items": []}


def test_bookmarks_list_and_search(env):
    login(env)
    _seed_bookmark(env.db.path, "alpha note")
    _seed_bookmark(env.db.path, "beta note")

    assert len(env.client.get("/api/bookmarks").json()["items"]) == 2
    assert len(env.client.get("/api/bookmarks?q=alpha").json()["items"]) == 1


def test_bookmark_delete(env):
    login(env)
    _seed_bookmark(env.db.path)
    bookmark_id = env.client.get("/api/bookmarks").json()["items"][0]["id"]

    assert env.client.delete(f"/api/bookmarks/{bookmark_id}").status_code == 200
    assert env.client.get("/api/bookmarks").json()["items"] == []
    assert env.client.delete(f"/api/bookmarks/{bookmark_id}").status_code == 404


def test_bookmarks_require_auth(env):
    assert env.client.get("/api/bookmarks").status_code == 401
    assert env.client.delete("/api/bookmarks/1").status_code == 401


# --------------------------------------------------------------------------
# Todos
# --------------------------------------------------------------------------
def test_todo_crud_via_api(env):
    login(env)

    created = env.client.post("/api/todos", json={"text": "ship nyx"})
    assert created.status_code == 200
    todo_id = created.json()["id"]

    items = env.client.get("/api/todos").json()["items"]
    assert len(items) == 1
    assert items[0]["text"] == "ship nyx"
    assert items[0]["done"] == 0

    assert env.client.post(f"/api/todos/{todo_id}/done").status_code == 200
    assert env.client.get("/api/todos").json()["items"][0]["done"] == 1

    assert env.client.delete(f"/api/todos/{todo_id}").status_code == 200
    assert env.client.get("/api/todos").json()["items"] == []


def test_todo_unknown_id_returns_404(env):
    login(env)
    assert env.client.post("/api/todos/9999/done").status_code == 404
    assert env.client.delete("/api/todos/9999").status_code == 404


def test_todo_rejects_empty_text(env):
    login(env)
    assert env.client.post("/api/todos", json={"text": ""}).status_code == 422


def test_todos_require_auth(env):
    assert env.client.get("/api/todos").status_code == 401


# --------------------------------------------------------------------------
# Status presets
# --------------------------------------------------------------------------
def test_status_cycle_round_trip(env):
    login(env)
    updated = env.client.put(
        "/api/config",
        json={"patch": {"status_cycle": ["listening", "coding"], "status_cycle_interval": 120}},
    )
    assert updated.status_code == 200
    body = updated.json()
    assert body["status_cycle"] == ["listening", "coding"]
    assert body["status_cycle_interval"] == 120


def test_status_cycle_interval_is_clamped_server_side(env):
    login(env)
    body = env.client.put(
        "/api/config", json={"patch": {"status_cycle_interval": 5}}
    ).json()
    assert body["status_cycle_interval"] == 60


# --------------------------------------------------------------------------
# WebSocket
# --------------------------------------------------------------------------
def test_websocket_requires_session(env):
    with pytest.raises(WebSocketDisconnect):
        with env.client.websocket_connect("/ws/logs") as ws:
            ws.receive_json()


def test_websocket_streams_live_events(env):
    login(env)
    with env.client.websocket_connect("/ws/logs") as ws:
        env.bus.publish("info", "stream-test-marker", source="test")
        for _ in range(80):
            event = ws.receive_json()
            if event.get("message") == "stream-test-marker":
                assert event["level"] == "info"
                return
    pytest.fail("marker event never arrived over the websocket")
