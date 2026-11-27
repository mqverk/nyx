# nyx

**A modular Discord self-bot with a real-time web control panel.**

Clone it, install the dependencies, run one command. nyx generates its own config, picks a
PIN, and opens a live dashboard — no manual setup, no build step, no database to provision.

Works on **Linux, macOS, and Windows**.

---

## ⚠️ Disclaimer

nyx drives a **user account**, not a bot account. Self-botting violates
[Discord's Terms of Service](https://discord.com/terms) and can get an account permanently
terminated.

This project is published for **educational purposes** and for automating accounts you own
and accept the risk for. **Use a throwaway account, never your main.** The authors accept no
liability for banned accounts, lost data, or anything else that follows from running this.

---

## Features

- **Hot-reloadable cogs** — load, unload, and reload command modules at runtime without
  dropping the gateway connection.
- **Real-time dashboard** — a single-page control panel streamed over WebSocket, with live
  console telemetry, status cards, and cog toggles. Midnight and light themes.
- **SQLite event store** — sniped messages, keyword hits, bookmarks, todos, and command
  audits, all in one WAL-mode database with no server to run.
- **Rate-limit-safe automation** — `purge` paces its deletes, AFK replies are locked to one
  per user/channel per 60s, and status rotation can never fire faster than once a minute.
- **Zero-config first run** — missing `.env`, `config.json`, and `data/` are created
  automatically, with a random PIN printed to your terminal.
- **Portable** — every path resolves relative to the repo, so it runs from any directory.
- **Standby mode** — boots and serves the dashboard even with no Discord token configured.

---

## Quickstart

**1. Clone**

```bash
git clone https://github.com/mqverk/nyx.git
cd nyx
```

**2. Create a virtualenv and install**

```bash
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

**3. Run**

```bash
python run.py
```

That's it. On first launch nyx prints something like:

```
  ┌──────────────────────────────────────────────┐
  │  nyx — discord self-bot control panel        │
  └──────────────────────────────────────────────┘
   web ui   http://127.0.0.1:8765
   pin      QJ3PS4F6TAQP
   token    not set — standby mode
```

Open the URL, enter the PIN, and paste a Discord token into the dashboard to bring the bot
online. The token is write-only — the API never returns it.

**Requirements:** Python 3.10+ and a C toolchain only if a wheel is unavailable for your
platform. Everything else is pure Python.

---

## Configuration

nyx reads two files, both created automatically on first run and both gitignored:

| File | Purpose |
| --- | --- |
| `.env` | Secrets: `DISCORD_TOKEN`, `WEB_UI_PIN`, `WEB_UI_HOST`, `WEB_UI_PORT` (mode `600`) |
| `config.json` | Runtime settings: prefix, presence, AFK, keywords, snippets, cogs, status presets |

`config.json` is hot-reloaded — edit it from the dashboard or on disk and the change applies
immediately. `.env` is rewritten atomically through a temp file and re-chmodded to `600` on
every write.

### CLI flags

```bash
python run.py --host 0.0.0.0 --port 9000   # override .env
python run.py --no-banner                  # quiet startup
python -m nyx --help                       # equivalent entrypoint
```

---

## Command reference

Default prefix is `.` — change it in `config.json` or the dashboard.

### Core

| Command | Description |
| --- | --- |
| `.ping` | Gateway latency |
| `.userinfo [@user]` | Account id and creation date |
| `.serverinfo` | Guild id and member count |
| `.avatar [@user]` | Avatar URL |
| `.banner [@user]` | Profile banner URL |
| `.roles [@user]` | Member roles, highest first |
| `.purge <n>` | Delete up to `n` of **your own** recent messages, rate-limit safe |
| `.snipe` | Last deleted message in this channel |
| `.firstmsg` | Jump link to the earliest message in the channel |
| `.remind <duration> <text>` | Reminder after `10s`, `5m`, `1h30m`, `2d` |
| `.calc <expr>` | Safe arithmetic (`+ - * / // % **`, parentheses) |
| `.spoiler <text>` | Rewrite your message with per-character `\|\|spoiler\|\|` tags |
| `.smallcaps <text>` | Rewrite your message in Unicode small caps |
| `.mock <text>` | Rewrite your message with alternating case |

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
words only, case-insensitively. Todos are owner-scoped — one user can never list, complete,
or delete another user's todos.

---

## Dashboard

A single-page vanilla-JS app with no build step.

- **Two themes** — *midnight* (`#04050a`) and *light* (pure white). The header toggle
  switches with a circular reveal and remembers your choice in `localStorage`.
- **Motion** — staggered card entrance, spring-eased toggles, ripple feedback, tweened
  status numbers, animated console lines. Respects `prefers-reduced-motion`.
- **Live** — status polls every 4s while the console streams over `/ws/logs`; the socket
  reconnects automatically.
- **Panels** — start/stop/restart, cog toggles, presence, AFK, keywords, snippets, status
  presets, bookmarks, todos, and a searchable sniped/keyword table.

---

## VPS deployment

By default nyx binds `127.0.0.1` — reachable only from the machine itself. To serve it on a
VPS, set the bind address and put a firewall in front of it:

```bash
# .env
WEB_UI_HOST=0.0.0.0
WEB_UI_PORT=8765
```

Then install the systemd unit, replacing the two placeholders:

```bash
sed -e 's|<YOUR_USER>|youruser|g' \
    -e 's|<PATH_TO_NYX>|/srv/nyx|g' \
    deploy/nyx.service.example | sudo tee /etc/systemd/system/nyx.service

sudo systemctl daemon-reload
sudo systemctl enable --now nyx
systemctl status nyx
```

```bash
ss -tulpn | grep 8765     # expect 0.0.0.0:8765 when exposed
journalctl -u nyx -f      # logs
```

> **Exposing the dashboard to the internet means the PIN is your only defence.** Use a long
> random PIN, and prefer a firewall allowlist or an SSH tunnel over a public bind:
>
> ```bash
> ssh -N -L 8765:127.0.0.1:8765 you@your-vps
> ```

---

## Architecture

```
nyx/
├── run.py                   entrypoint (python run.py)
├── nyx/
│   ├── __main__.py          python -m nyx
│   ├── cli.py               argparse: --host / --port / --no-banner
│   ├── bootstrap.py         first-run .env, config.json, data/ + PIN banner
│   ├── config.py            Pydantic v2 config, atomic .env writes, path resolution
│   ├── db.py                aiosqlite store (WAL, serialized writes)
│   ├── bot/
│   │   ├── runner.py        client lifecycle, LogBus fan-out, CogManager hot-reload
│   │   └── cogs/            core · media · productivity · automation
│   └── web/
│       ├── app.py           FastAPI control plane (PIN auth, REST, /ws/logs)
│       └── static/index.html single-page dashboard
├── deploy/nyx.service.example
├── tests/                   290+ unit and integration tests
└── LICENSE                  MIT
```

**Data flow**

1. `BotRunner` owns the discord.py-self client; the dashboard drives it over
   `/api/bot/start|stop|restart`.
2. Every runtime event goes to `LogBus`, which fans out to each WebSocket subscriber through
   its own bounded queue — a slow browser tab can never stall the bot.
3. Cogs are extensions; `CogManager` loads, unloads, and reloads them live.
4. `Database` serializes writes behind an asyncio lock in WAL mode.
5. `ConfigManager` stats `config.json` on every read and re-parses on mtime change.

---

## Security model

- `.env`, `config.json`, `data/`, and `venv/` are gitignored. A pre-commit hook blocks any
  commit containing a token-shaped string, a PIN assignment, or a forbidden path.
- The Discord token is **write-only** over the API: `GET /api/token` returns only
  `{configured, masked}`, revealing at most the last four characters.
- `.env` is written through a temp file and forced to `600`.
- Sessions are 32-byte random tokens in an HttpOnly, `SameSite=strict` cookie with a 12-hour
  TTL. PIN comparison is constant-time and login is rate-limited.
- No CORS, no docs endpoint, no OpenAPI schema exposed.

---

## Tests

```bash
python -m pytest
```

Covers config validation and live reload, path portability, first-run bootstrap, SQLite
concurrency and retention, standby boot, cog hot-reload, safe AST evaluation, duration
parsing, the 60-second AFK cooldown, keyword matching, snippet expansion, timezone
resolution, PIN auth, token masking, WebSocket streaming, and a route-contract check that
every frontend call maps to a real backend route.

---

## License

[MIT](LICENSE)
