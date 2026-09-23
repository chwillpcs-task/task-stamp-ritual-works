# Slice 1 — The Shop Stamp Ritual

> **The dream.** A customer at a soft-serve shop notices the little stand on the
> counter and scans the QR code. LINE opens the TASK official account chat with
> a message already written — `STAMP:uemon`. They tap send. A moment later a
> stamp card slides in: the shop's name in Japanese and romaji, one gold circle
> filled toward five, and a quiet line from Sato — *"A quiet moment worth
> remembering doesn't need fanfare — just fresh milk and where you stand."*
> Nobody explained anything. Nobody signed up for anything. They just came
> together about ice cream, and the system noticed. That is the whole slice:
> the smallest complete loop of the delight layer.

## What it does

A Python + FastAPI service that receives LINE webhooks and runs the ritual:

- `POST /webhook` — verifies `X-Line-Signature` (HMAC-SHA256 against
  `CHANNEL_SECRET`), handles text messages matching `STAMP:<shop-code>`
  (case-insensitive, whitespace-tolerant), and replies through the LINE
  reply API.
- **Stamp store** — SQLite (`stamps.db`). `shops` seeded from `seed_shops.json`
  (3 real, verified Tokachi shops); `stamps` records `(user_id, shop_code,
  stamped_at)`. Anti-spam: **one stamp per user per shop per day**.
- **Flex Message stamp card** — shop name (JP + romaji), filled/empty progress
  circles toward 5, one restrained Sato-voiced line. Unknown codes, non-stamp
  text, and repeats get polite plain-text replies instead.
- **Law 2 switch** — `STAMPS_ENABLED=false` pauses the rally with a gentle
  message; stamps already earned are untouched.

## Env vars

| Var | Default | Notes |
|---|---|---|
| `CHANNEL_SECRET` | `dev-secret-placeholder` | LINE channel secret. Replace at deploy. Never in chat/logs. |
| `CHANNEL_ACCESS_TOKEN` | *(unset)* | LINE channel access token. Unset/placeholder → replies skipped gracefully (dev mode). |
| `OA_BASIC_ID` | `dev-oa-basic-id` | LINE OA basic ID, baked into QR links. Replace before printing. |
| `STAMPS_ENABLED` | `true` | `false` pauses the rally (Law 2). |
| `STAMPS_DB` | `./stamps.db` | SQLite path. |
| `DRY_RUN_REPLY` | `0` | `1` → skip the LINE HTTP call; webhook response includes the Flex payload under `dev_flex_preview` (dev/test only). |

## Run it

```bash
cd ~/workspace/task-l3c/slice-01-stamp-ritual
.venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 8000
# health:  curl localhost:8000/health
```

## Prove it (no LINE account needed)

```bash
# terminal 1 — service in dry-run mode
DRY_RUN_REPLY=1 .venv/bin/python -m uvicorn app:app --port 8000

# terminal 2 — full-loop simulation: signs fake webhooks, asserts each step
DRY_RUN_REPLY=1 .venv/bin/python simulate.py
# -> ALL 15 SIMULATION CHECKS PASSED
```

## Test it

```bash
.venv/bin/python -m pytest tests/ -q
# 8 passed: signature good/bad/missing, stamp + daily dedup,
# unknown shop, help text, STAMPS_ENABLED switch, Flex JSON shape
```

## QR codes

```bash
.venv/bin/python make_qr.py   # -> qr/<shop-code>.png
```

Each QR encodes `https://line.me/R/oaMessage/{OA_BASIC_ID}/?STAMP:{shop-code}` —
scanning opens the OA chat with the stamp message prefilled; the customer taps
send. **Printing notes:** black on white, ≥3–4 cm square, matte paper;
test-scan with LINE's built-in QR reader before laminating; regenerate after
setting the real `OA_BASIC_ID`.

## Deploy notes (deliberately left for deploy time)

1. **Public HTTPS** — the webhook must be reachable by LINE (Render, Fly.io,
   a VPS, etc.). Local dev can use a tunnel for testing only.
2. **LINE Developers console → Messaging API → Webhook URL**: set to
   `https://<your-host>/webhook`, enable "Use webhook".
3. **Secrets**: set `CHANNEL_SECRET` and `CHANNEL_ACCESS_TOKEN` as env vars
   on the host. Keep the channel secret out of chat, logs, and screenshots.
   This service never prints secrets to logs.
4. Run with `DRY_RUN_REPLY=0` (or unset) in production so real replies send.
5. **Verify with your own phone**: scan a printed QR, tap send, confirm the
   stamp card arrives. Then scan again the same day and confirm the polite
   duplicate message. (Per the standing rule: only Carl's eyes verify the
   in-hand feel.)
6. Back up `stamps.db` on a schedule — it is the system of record for slice 1.

## What's intentionally not here

Real LINE credentials (placeholders only), a LIFF app (Law 5 — never),
merchant onboarding (research only, no outreach), and slices 2+ (passport,
trails, campaigns). This slice is complete: it either fully works or it
doesn't exist — and it works.
