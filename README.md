# nyx

A modular Discord self-bot daemon with a real-time FastAPI control panel, hot-reloadable
cogs, and SQLite event logging.

The bot runs as your own Discord account, exposes a **localhost-only** web dashboard, and
streams its runtime telemetry to the browser over a WebSocket. Everything is driven from
one dark-mode page: start/stop/restart, cog toggles, presence, AFK auto-replies, keyword
watchers, snippets, and a searchable log of sniped messages and keyword hits.

> **Self-bots violate Discord's Terms of Service.** Running one can get your account
> terminated. This project exists for personal automation on accounts you control and
> accept the risk for. Use it on a throwaway account, not your main.

---

## Architecture

```
nyx/
├── nyx/
│   ├── config.py            Pydantic v2 config + .env secret handling (atomic writes)
│   ├── db.py                aiosqlite event store (WAL, serialized writes)
│   ├── bot/
│   │   ├── runner.py        client lifecycle, LogBus fan-out, CogManager hot-reload
│   │   └── cogs/
│   │       ├── core.py      ping, userinfo, serverinfo, avatar, purge, snipe, remind, calc
│   │       └── automation.py presence, AFK responder, keyword watcher, snippets
│   └── web/
│       ├── app.py           FastAPI control plane (PIN auth, REST, /ws/logs)
│       └── static/index.html single-page dark dashboard (vanilla JS, no build step)
├── tests/                   155 unit + integration tests
├── data/                    SQLite database (gitignored)
├── config.json              runtime settings (gitignored, hot-reloaded)
└── .env                     DISCORD_TOKEN + WEB_UI_PIN (gitignored, chmod 600)
```

**Data flow**

1. `BotRunner` owns the discord.py-self client. The web UI calls `/api/bot/start|stop|restart`.
2. Every runtime event is published to `LogBus`, which fans out to each WebSocket subscriber
   through its own bounded queue — a slow browser tab can never stall the bot.
3. Cogs are loaded as extensions. `CogManager` can load, unload, and **reload** them at
   runtime without dropping the gateway connection.
4. `Database` writes keyword hits, snipes, and command audits to SQLite in WAL mode behind
   an asyncio lock, so concurrent writers serialize cleanly.
5. `ConfigManager` stats `config.json` on every read and re-parses when the mtime changes,
   so edits from the dashboard take effect immediately.

**Standby mode.** With an empty `DISCORD_TOKEN` the daemon boots into `standby` instead of
crashing — the dashboard is fully usable before you have a token.

---

## Dashboard

The control panel is a single-page vanilla-JS app with no build step.

- **Two themes** — *midnight* (near-black `#04050a`) and *light* (pure white). The toggle
  in the header switches between them with a circular reveal, and the choice persists in
  `localStorage`. First visit follows your OS `prefers-color-scheme`.
- **Motion** — staggered card entrance, spring-eased toggles, ripple feedback on buttons,
  tweened status numbers, animated console lines, and a spring toast. Everything respects
  `prefers-reduced-motion`.
- **Live** — status polls every 4s while the console streams over `/ws/logs`; the socket
  reconnects automatically.

---

## Install

```bash
git clone https://github.com/mqverk/nyx.git ~/nyx
cd ~/nyx
python3.12 -m venv venv
./venv/bin/pip install -r requirements.txt

cp .env.example .env
chmod 600 .env
# edit .env: set WEB_UI_PIN (6 digits) and DISCORD_TOKEN
```

Generate a PIN if you want one:

```bash
python3 -c "import secrets; print(f'{secrets.randbelow(1000000):06d}')"
```

Run it:

```bash
./venv/bin/python -m uvicorn nyx.web.app:create_default_app --factory \
    --host 127.0.0.1 --port 8765
```

Then open <http://127.0.0.1:8765> and enter your PIN.

---

## Remote access (SSH tunnel)

The dashboard binds to `127.0.0.1` only and is never exposed to the network. To reach it
from your laptop, forward the port over SSH:

```bash
ssh -N -L 8765:127.0.0.1:8765 root@your-vps-host
```

Then browse to <http://127.0.0.1:8765> locally. Keep the tunnel open while you use the UI.

---

## systemd

```bash
sudo cp deploy/nyx.service /etc/systemd/system/nyx.service
sudo systemctl daemon-reload
sudo systemctl enable --now nyx
systemctl status nyx
```

Verify it is loopback-only:

```bash
ss -tulpn | grep 8765     # expect 127.0.0.1:8765, never 0.0.0.0:8765
```

Logs: `journalctl -u nyx -f`

---

## Command reference

Default prefix is `.` (configurable in `config.json` or the dashboard).

### Core

| Command | Description |
| --- | --- |
| `.ping` | Gateway latency |
| `.userinfo [member]` | Account id and creation date |
| `.serverinfo` | Current guild id and member count |
| `.avatar [member]` | Avatar URL |
| `.purge <n>` | Delete up to `n` of **your own** recent messages, rate-limit safe |
| `.snipe` | Last deleted message in this channel |
| `.remind <duration> <text>` | Reminder after `10s`, `5m`, `1h30m`, `2d` |
| `.calc <expr>` | Safe arithmetic (`+ - * / // % **`, parentheses) |
| `.spoiler <text>` | Rewrite your own message with per-character `||spoiler||` tags |
| `.smallcaps <text>` | Rewrite your own message in Unicode small caps |
| `.mock <text>` | Rewrite your own message with alternating case |
| `.banner [@user]` | Profile banner URL |
| `.roles [@user]` | Member roles, highest first |
| `.firstmsg` | Jump link to the earliest message in the channel |

### Automation

| Command | Description |
| --- | --- |
| `.afk on\|off\|<message>` | Toggle the AFK auto-responder |
| `.presence <status> [text]` | Set `online`/`idle`/`dnd`/`invisible` + custom activity |
| `.snippet` | List snippets |
| `.snippet add <key> <value>` | Create a snippet |
| `.snippet remove <key>` | Delete a snippet |
| `.keyword` | List watched keywords |
| `.keyword add <word>` | Watch a keyword |
| `.keyword remove <word>` | Stop watching |

Snippets expand when a message is exactly the key (or `key <args>`). Keywords match whole
words only, case-insensitively.

### Media

| Command | Description |
| --- | --- |
| `.editsnipe` / `.es` | Original text of the last edited message in the channel |
| `.ghost <duration> <text>` | Post text that self-destructs after `duration` (max 24h) |
| `.bookmark` | Reply to a message to save it to SQLite |
| `.quote` | Reply to a message to render a dark-mode PNG quote card |

### Productivity

| Command | Description |
| --- | --- |
| `.seen [@user]` | When a user was last active, and where |
| `.todo add <text>` | Create a todo |
| `.todo list` | Show your todos |
| `.todo done <id>` | Mark a todo complete |
| `.todo remove <id>` | Delete a todo |
| `.statuscycle on\|off` | Rotate presence through `status_cycle` presets |
| `.tz <zone>` | Convert the time — `Asia/Kolkata`, `UTC+5:30`, `london` |

Todos are scoped to their owner: one user can never list, complete, or delete another
user's todos. Status rotation is clamped to a **60-second minimum** server-side.

---

## HTTP API

All routes except `/api/login` require the session cookie issued by a correct PIN.

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/api/login` | `{pin}` → sets HttpOnly session cookie |
| POST | `/api/logout` | Revoke session |
| GET | `/api/session` | Auth probe |
| GET | `/api/status` | Bot status, latency, uptime, guilds |
| GET | `/api/token` | `{configured, masked}` — never the raw token |
| PUT | `/api/token` | Write a new token to `.env` atomically |
| POST | `/api/bot/start` \| `stop` \| `restart` | Supervisor controls |
| GET | `/api/cogs` | Discovered cogs with loaded/enabled state |
| POST | `/api/cogs/{name}/toggle` | Enable/disable a cog |
| POST | `/api/cogs/{name}/reload` | Hot-reload a cog |
| GET/PUT | `/api/config` | Read / deep-merge config |
| GET | `/api/events?q=&limit=` | Search keyword hits and snipes |
| GET | `/api/bookmarks?q=` | List/search saved bookmarks |
| DELETE | `/api/bookmarks/{id}` | Delete a bookmark |
| GET | `/api/todos` | List todos for the bot account |
| POST | `/api/todos` | Create a todo |
| POST | `/api/todos/{id}/done` | Complete a todo |
| DELETE | `/api/todos/{id}` | Delete a todo |
| WS | `/ws/logs` | Live telemetry stream |

---

## Security model

- `.env`, `config.json`, `data/`, and `venv/` are gitignored. A pre-commit secret gate
  blocks any commit containing a token-shaped string, a 6-digit PIN assignment, or a
  forbidden path.
- The Discord token is **write-only** over the API: `GET /api/token` returns only
  `{configured, masked}`, where the mask reveals at most the last four characters.
- `.env` is rewritten through a temp file and forced to mode `600` on every write, so a
  partially written or world-readable secret never exists on disk.
- Sessions are random 32-byte tokens in an HttpOnly, `SameSite=strict` cookie with a 12-hour
  TTL. PIN comparison is constant-time, and the login endpoint is rate-limited.
- The server binds `127.0.0.1` only. There is no CORS, no docs endpoint, and no OpenAPI
  schema exposed.

---

## Tests

```bash
./venv/bin/python -m pytest
```

Covers config validation and live reload, SQLite concurrency and retention, standby boot,
cog hot-reload, safe AST evaluation, duration parsing, the 60-second AFK cooldown lock,
keyword matching, snippet expansion, PIN auth, token masking, config round-trips, WebSocket
streaming, and a route-contract check that every frontend call maps to a real backend route.

---

## License

MIT
