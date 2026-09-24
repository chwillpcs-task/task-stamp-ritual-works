# Slice 1 — Stamp Ritual: Permanent Hosting Runbook

**Status:** LIVE since 2026-09-23
**URL:** https://TASKL3C.pythonanywhere.com
**Health:** https://TASKL3C.pythonanywhere.com/health
**Webhook (set in LINE Developers → TASK Community → Messaging API):** https://TASKL3C.pythonanywhere.com/webhook
**Host:** PythonAnywhere free (Beginner), username `TASKL3C`
**Channel:** TASK Community (`@664ickdm`)

## What's running

- App: FastAPI + uvicorn, served as an ASGI site via `pa website create`
- Code: `/home/TASKL3C/task-stamp-ritual-works` (cloned from `chwillpcs-task/task-stamp-ritual-works` on GitHub)
- venv: `/home/TASKL3C/task-stamp-ritual-works/venv`
- Database: `/home/TASKL3C/task-stamp-ritual-works/stamps.db` (SQLite, on PythonAnywhere's persistent disk — survives reloads)

## The site command (recipe)

Secrets are NOT stored here. When (re-)creating the site, fill the two
placeholders from the password manager, then run as one line in a
PythonAnywhere Bash console:

```
pa website create --domain TASKL3C.pythonanywhere.com --command '/usr/bin/env http_proxy=http://proxy.server:3128 https_proxy=http://proxy.server:3128 HTTP_PROXY=http://proxy.server:3128 HTTPS_PROXY=http://proxy.server:3128 CHANNEL_SECRET=<channel-secret> CHANNEL_ACCESS_TOKEN=<channel-access-token> OA_BASIC_ID=@664ickdm STAMPS_ENABLED=true /home/TASKL3C/task-stamp-ritual-works/venv/bin/uvicorn --app-dir /home/TASKL3C/task-stamp-ritual-works --uds ${DOMAIN_SOCKET} app:app'
```

Why each piece exists:
- `/usr/bin/env` prefix — PythonAnywhere execs the command without a shell, so bare `VAR=value` prefixes fail ("No such file or directory"). `env` sets them properly.
- `*_proxy` vars — free accounts must route outbound traffic through `proxy.server:3128`; without these the LINE reply API call fails.
- `${DOMAIN_SOCKET}` — supplied by PythonAnywhere; do not hard-code a socket path.
- `STAMPS_ENABLED=true` — kill switch. Set to `false` to pause stamping (data is preserved).

## Routine operations (Bash console on PythonAnywhere)

```bash
# Restart the app
pa website reload --domain TASKL3C.pythonanywhere.com

# Pull latest code and restart
cd ~/task-stamp-ritual-works && git pull && venv/bin/pip install -r requirements.txt && pa website reload --domain TASKL3C.pythonanywhere.com

# Pause / resume stamping (no `pa website update` exists — delete + re-create)
pa website delete --domain TASKL3C.pythonanywhere.com
# then re-run the create command above with STAMPS_ENABLED=false (or true)

# Inspect the database
cd ~/task-stamp-ritual-works && python3 -c "import sqlite3; c=sqlite3.connect('stamps.db'); print(c.execute('SELECT shop_code, stamped_at FROM stamps ORDER BY stamped_at DESC LIMIT 20').fetchall())"
```

## Logs (all lowercase)

```
/var/log/taskl3c.pythonanywhere.com.error.log    # app tracebacks
/var/log/taskl3c.pythonanywhere.com.server.log  # uvicorn access lines
/var/log/taskl3c.pythonanywhere.com.access.log  # frontend access log
```

## PythonAnywhere quirks learned the hard way

1. `pa` CLI is not preinstalled: `pip install --upgrade pythonanywhere`, plus an API token from the Account page.
2. ASGI sites do NOT appear in the Web tab. Manage them only via `pa website ...` commands.
3. There is no `pa website update`. Change anything → `pa website delete` + `pa website create`.
4. Log file names are lowercase (`taskl3c...`), even though the domain displays uppercase.
5. First health check after create may 404 for a few seconds — refresh.
6. PythonAnywhere labels ASGI hosting experimental. If it ever goes away, the fallback is Render paid (+disk, ~$7.25/mo).

## Slice 2 — multi-channel operations

- The channel registry is `channels.json` in the project dir. It is read on
  **every webhook**, so editing it (add a channel, flip `"enabled"`) takes
  effect immediately — no reload needed.
- Adding a channel:
  1. Add its entry to `channels.json` (`id`, `destination` if known, `basic_id`,
     `display_name`, `enabled`, `secret_env`/`token_env`, `theme`, `shops`).
  2. Add its secret + token to the site command's env (the names must match
     `secret_env`/`token_env`) → `pa website delete` + `pa website create`.
  3. Reprint that channel's QR codes (its own add-friend URL).
- Each channel carries its own LINE message quota, so variants spread quota load.
- If the on-server git clone gets dirty from file uploads, resync it with:
  `cd ~/task-stamp-ritual-works && git fetch origin && git reset --hard origin/main`
  (only when GitHub is the source of truth and local changes are expendable).

## Before public use

- The LINE OA greeting is a 3-card sequence (see `OA_GREETING.md`): text
  `Hello, {name}! 🍦`, then `soft-cream-greeting.webp`,
  `card-2-more-ice-cream.webp`, `card-3-ice-cream-menu.webp`. No instruction
  card — stamp mechanics are discovered through exploring.
- The add-friend fallback URL is `https://line.me/R/ti/p/@664ickdm`; counter-stand copy lives in `STAND_COPY.md`.
