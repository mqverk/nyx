"""FastAPI control plane for nyx.

Binds to 127.0.0.1 only. Every mutating route requires a session cookie issued
after a correct ``WEB_UI_PIN``; the Discord token is write-only — the API can
report whether one is configured and a masked tail, but never returns it.
"""

from __future__ import annotations

import contextlib
import hmac
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from nyx.bootstrap import ensure_runtime_files, print_banner
from nyx.bot.runner import BotRunner, LogBus
from nyx.config import (
    CONFIG_PATH,
    ENV_PATH,
    ConfigManager,
    get_host,
    get_pin,
    get_port,
    get_token,
    load_env,
    mask_token,
    resolve_path,
    set_env_value,
)
from nyx.db import Database

COOKIE_NAME = "nyx_session"
SESSION_TTL = 12 * 3600
STATIC_DIR = Path(__file__).resolve().parent / "static"
MAX_LOGIN_ATTEMPTS = 8
LOGIN_WINDOW = 60.0


# ---------------------------------------------------------------------------
# Auth primitives
# ---------------------------------------------------------------------------
class SessionStore:
    """In-memory session tokens with TTL."""

    def __init__(self, ttl: int = SESSION_TTL) -> None:
        self.ttl = ttl
        self._sessions: dict[str, float] = {}

    def create(self) -> str:
        token = secrets.token_urlsafe(32)
        self._sessions[token] = time.time() + self.ttl
        return token

    def valid(self, token: str | None) -> bool:
        if not token:
            return False
        expiry = self._sessions.get(token)
        if expiry is None:
            return False
        if expiry < time.time():
            self._sessions.pop(token, None)
            return False
        return True

    def revoke(self, token: str | None) -> None:
        if token:
            self._sessions.pop(token, None)


class AttemptLimiter:
    """Very small brute-force guard for the PIN endpoint."""

    def __init__(self, limit: int = MAX_LOGIN_ATTEMPTS, window: float = LOGIN_WINDOW) -> None:
        self.limit = limit
        self.window = window
        self._hits: dict[str, list[float]] = {}

    def allow(self, key: str) -> bool:
        now = time.time()
        hits = [t for t in self._hits.get(key, []) if now - t < self.window]
        self._hits[key] = hits
        return len(hits) < self.limit

    def hit(self, key: str) -> None:
        self._hits.setdefault(key, []).append(time.time())


def pin_matches(candidate: str, expected: str) -> bool:
    """Constant-time PIN comparison."""
    if not expected:
        return False
    return hmac.compare_digest(str(candidate), str(expected))


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------
class LoginBody(BaseModel):
    pin: str = Field(min_length=1, max_length=64)


class TokenBody(BaseModel):
    token: str = Field(min_length=1, max_length=200)


class ConfigBody(BaseModel):
    patch: dict[str, Any] = Field(default_factory=dict)


class ToggleBody(BaseModel):
    enabled: bool


class TodoBody(BaseModel):
    text: str = Field(min_length=1, max_length=500)


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------
def create_app(
    runner: BotRunner,
    db: Database,
    config: ConfigManager,
    log_bus: LogBus | None = None,
) -> FastAPI:
    bus = log_bus or runner.log_bus
    sessions = SessionStore()
    limiter = AttemptLimiter()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        await db.connect()
        bus.publish("info", "web control plane ready", source="web")
        try:
            yield
        finally:
            await db.close()

    app = FastAPI(
        title="nyx",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )

    def require_session(request: Request) -> str:
        token = request.cookies.get(COOKIE_NAME)
        if not sessions.valid(token):
            raise HTTPException(status_code=401, detail="unauthorized")
        return token  # type: ignore[return-value]

    # -- auth --------------------------------------------------------------
    @app.post("/api/login")
    async def login(body: LoginBody, request: Request) -> JSONResponse:
        client = request.client.host if request.client else "unknown"
        if not limiter.allow(client):
            raise HTTPException(status_code=429, detail="too many attempts")
        if not pin_matches(body.pin, get_pin()):
            limiter.hit(client)
            bus.publish("warning", f"failed PIN attempt from {client}", source="web")
            raise HTTPException(status_code=401, detail="invalid pin")
        token = sessions.create()
        response = JSONResponse({"ok": True})
        response.set_cookie(
            COOKIE_NAME, token, httponly=True, samesite="strict", max_age=SESSION_TTL
        )
        bus.publish("info", "web session opened", source="web")
        return response

    @app.post("/api/logout")
    async def logout(request: Request, _: str = Depends(require_session)) -> JSONResponse:
        sessions.revoke(request.cookies.get(COOKIE_NAME))
        response = JSONResponse({"ok": True})
        # clear the stale cookie client-side as well as revoking it server-side
        response.delete_cookie(COOKIE_NAME, path="/")
        return response

    @app.get("/api/session")
    async def session_state(_: str = Depends(require_session)) -> dict[str, bool]:
        return {"authenticated": True}

    # -- status ------------------------------------------------------------
    @app.get("/api/status")
    async def status(_: str = Depends(require_session)) -> dict[str, Any]:
        snap = runner.snapshot()
        snap["token_configured"] = bool(get_token())
        snap["db_path"] = str(db.path)
        return snap

    # -- token (write-only) ------------------------------------------------
    @app.get("/api/token")
    async def token_status(_: str = Depends(require_session)) -> dict[str, Any]:
        token = get_token()
        return {"configured": bool(token), "masked": mask_token(token)}

    @app.put("/api/token")
    async def token_update(body: TokenBody, _: str = Depends(require_session)) -> dict[str, Any]:
        value = body.token.strip()
        if not value:
            raise HTTPException(status_code=400, detail="empty token")
        set_env_value("DISCORD_TOKEN", value, ENV_PATH)
        bus.publish("info", "discord token updated", source="web")
        # deliberately never echo the raw token back
        return {"configured": True, "masked": mask_token(value)}

    # -- bot control -------------------------------------------------------
    @app.post("/api/bot/start")
    async def bot_start(_: str = Depends(require_session)) -> dict[str, Any]:
        return await runner.start()

    @app.post("/api/bot/stop")
    async def bot_stop(_: str = Depends(require_session)) -> dict[str, Any]:
        return await runner.stop()

    @app.post("/api/bot/restart")
    async def bot_restart(_: str = Depends(require_session)) -> dict[str, Any]:
        return await runner.restart()

    # -- cogs --------------------------------------------------------------
    @app.get("/api/cogs")
    async def cogs_list(_: str = Depends(require_session)) -> list[dict[str, Any]]:
        enabled = config.get().cogs
        manager = runner.cogs
        discovered = manager.discover() if manager else _discover_cogs()
        loaded = set(manager.loaded) if manager else set()
        return [
            {
                "name": name,
                "loaded": name in loaded,
                "enabled": enabled.get(name, True),
            }
            for name in discovered
        ]

    @app.post("/api/cogs/{name}/toggle")
    async def cog_toggle(
        name: str, body: ToggleBody, _: str = Depends(require_session)
    ) -> dict[str, Any]:
        if name not in _discover_cogs():
            raise HTTPException(status_code=404, detail="unknown cog")
        current = dict(config.get().cogs)
        current[name] = body.enabled
        config.update({"cogs": current})
        manager = runner.cogs
        if manager is not None:
            if body.enabled:
                await manager.load(name)
            else:
                await manager.unload(name)
        return {"name": name, "enabled": body.enabled}

    @app.post("/api/cogs/{name}/reload")
    async def cog_reload(name: str, _: str = Depends(require_session)) -> dict[str, Any]:
        if name not in _discover_cogs():
            raise HTTPException(status_code=404, detail="unknown cog")
        manager = runner.cogs
        if manager is None:
            raise HTTPException(status_code=409, detail="bot is not running")
        ok = await manager.reload(name)
        if not ok:
            raise HTTPException(status_code=500, detail="reload failed")
        return {"name": name, "reloaded": True}

    # -- config ------------------------------------------------------------
    @app.get("/api/config")
    async def config_get(_: str = Depends(require_session)) -> dict[str, Any]:
        return config.get().model_dump()

    @app.put("/api/config")
    async def config_put(body: ConfigBody, _: str = Depends(require_session)) -> dict[str, Any]:
        try:
            updated = config.update(body.patch)
        except Exception as exc:  # noqa: BLE001 - validation errors are user-facing
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        bus.publish("info", "config updated via web", source="web")
        return updated.model_dump()

    # -- events ------------------------------------------------------------
    @app.get("/api/events")
    async def events(
        q: str | None = None, limit: int = 50, _: str = Depends(require_session)
    ) -> dict[str, Any]:
        return await db.search_events(q, limit=limit)

    # -- bookmarks ---------------------------------------------------------
    @app.get("/api/bookmarks")
    async def bookmarks_list(
        q: str | None = None, limit: int = 100, _: str = Depends(require_session)
    ) -> dict[str, Any]:
        return {"items": await db.list_bookmarks(q, limit=limit)}

    @app.delete("/api/bookmarks/{bookmark_id}")
    async def bookmark_delete(
        bookmark_id: int, _: str = Depends(require_session)
    ) -> dict[str, Any]:
        if not await db.delete_bookmark(bookmark_id):
            raise HTTPException(status_code=404, detail="bookmark not found")
        return {"deleted": bookmark_id}

    # -- todos -------------------------------------------------------------
    def _owner_id() -> int:
        """Todos belong to the account the bot runs as (0 when stopped)."""
        user = getattr(runner.client, "user", None) if runner.client else None
        return int(getattr(user, "id", 0) or 0)

    @app.get("/api/todos")
    async def todos_list(_: str = Depends(require_session)) -> dict[str, Any]:
        return {"items": await db.list_todos(_owner_id())}

    @app.post("/api/todos")
    async def todo_create(body: TodoBody, _: str = Depends(require_session)) -> dict[str, Any]:
        text = body.text.strip()
        if not text:
            raise HTTPException(status_code=400, detail="empty todo")
        todo_id = await db.add_todo(_owner_id(), text)
        return {"id": todo_id, "text": text}

    @app.post("/api/todos/{todo_id}/done")
    async def todo_complete(todo_id: int, _: str = Depends(require_session)) -> dict[str, Any]:
        if not await db.complete_todo(_owner_id(), todo_id):
            raise HTTPException(status_code=404, detail="todo not found")
        return {"id": todo_id, "done": True}

    @app.delete("/api/todos/{todo_id}")
    async def todo_delete(todo_id: int, _: str = Depends(require_session)) -> dict[str, Any]:
        if not await db.remove_todo(_owner_id(), todo_id):
            raise HTTPException(status_code=404, detail="todo not found")
        return {"deleted": todo_id}

    # -- websocket ---------------------------------------------------------
    @app.websocket("/ws/logs")
    async def ws_logs(websocket: WebSocket) -> None:
        if not sessions.valid(websocket.cookies.get(COOKIE_NAME)):
            await websocket.close(code=4401)
            return
        await websocket.accept()
        queue = bus.subscribe()
        try:
            for event in bus.history[-50:]:
                await websocket.send_json(event)
            while True:
                event = await queue.get()
                await websocket.send_json(event)
        except WebSocketDisconnect:
            pass
        except Exception:  # noqa: BLE001
            pass
        finally:
            bus.unsubscribe(queue)

    # -- static ------------------------------------------------------------
    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        page = STATIC_DIR / "index.html"
        if not page.exists():
            return HTMLResponse("<h1>nyx</h1><p>dashboard not installed</p>", status_code=200)
        return HTMLResponse(page.read_text(encoding="utf-8"))

    return app


def _discover_cogs() -> list[str]:
    from nyx.bot.runner import CogManager

    class _Null:
        async def load_extension(self, name: str) -> None: ...
        async def unload_extension(self, name: str) -> None: ...
        async def reload_extension(self, name: str) -> None: ...

    return CogManager(_Null()).discover()  # type: ignore[arg-type]


def create_default_app() -> FastAPI:
    """Production entry point used by uvicorn --factory and systemd."""
    boot = ensure_runtime_files()
    load_env()
    print_banner(boot, token_configured=bool(get_token()))
    config = ConfigManager(CONFIG_PATH)
    db = Database(resolve_path(config.get().db_path))
    bus = LogBus()
    runner = BotRunner(config, log_bus=bus, db=db)
    return create_app(runner=runner, db=db, config=config, log_bus=bus)


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run(
        create_default_app(),
        host=get_host(),
        port=get_port(),
        log_level="info",
    )
