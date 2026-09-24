"""
Slice 1 — the shop stamp ritual (TASK L3C), wrapped by slice 2 multi-channel.

A customer at a soft-serve shop scans a QR code on the counter stand.
LINE opens the TASK official account chat with a prefilled STAMP:<shop-code>
message. The customer taps send; this service verifies the LINE signature,
records the stamp (one per user per shop per day), and replies with a
Flex Message stamp card showing progress toward 5.

Slice 2: one backend serves many LINE channels. Each webhook event carries a
`destination` (the bot user ID of the receiving OA); channels.py maps it to a
registry entry with that channel's secrets (from env), theme, enabled switch,
and optional shop subset. The stamp engine below is unchanged.

Env vars:
  CHANNELS_PATH           registry file (default: ./channels.json); read per
                          webhook, so edits apply without a restart
  CHANNEL_SECRET / CHANNEL_ACCESS_TOKEN
                          legacy single-channel secret env names; the registry
                          points at them via secret_env/token_env per channel
  OA_BASIC_ID             LINE OA basic ID for QR links (default: dev-oa-basic-id)
  STAMPS_ENABLED          "true" (default) | "false" — Law 2 global switch
  STAMPS_DB               SQLite path (default: ./stamps.db)
  DRY_RUN_REPLY           "1" -> skip LINE HTTP call; webhook response includes
                          the Flex payload under dev_flex_preview (dev/test only)
"""

import json
import logging
import os
import re
import sqlite3
from datetime import datetime, timezone

import requests
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from channels import (
    DEFAULT_THEME,
    get_secret,
    get_token,
    load_channels,
    resolve_channel,
    verify_signature,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("slice1")

# ---------------------------------------------------------------- config ---

# Legacy single-channel secret names; the registry's secret_env/token_env
# point here for the default channel, and per-channel names for the rest.
CHANNEL_SECRET = os.environ.get("CHANNEL_SECRET", "dev-secret-placeholder")
CHANNEL_ACCESS_TOKEN = os.environ.get("CHANNEL_ACCESS_TOKEN", "")
OA_BASIC_ID = os.environ.get("OA_BASIC_ID", "dev-oa-basic-id")
STAMPS_ENABLED = os.environ.get("STAMPS_ENABLED", "true").lower() not in ("0", "false", "no", "off")
DRY_RUN_REPLY = os.environ.get("DRY_RUN_REPLY", "0").lower() in ("1", "true", "yes")
GOAL_STAMPS = 5

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("STAMPS_DB", os.path.join(BASE_DIR, "stamps.db"))
SEED_PATH = os.path.join(BASE_DIR, "seed_shops.json")

LINE_REPLY_URL = "https://api.line.me/v2/bot/message/reply"

STAMP_RE = re.compile(r"^\s*stamp\s*:\s*([A-Za-z0-9_-]+)\s*$", re.IGNORECASE)

app = FastAPI(title="TASK L3C — slice 2: multi-channel stamp ritual")


# ------------------------------------------------------------------ db ---

def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = get_conn()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS shops (
                shop_code TEXT PRIMARY KEY,
                name      TEXT NOT NULL,
                name_ja   TEXT NOT NULL,
                town      TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS stamps (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    TEXT NOT NULL,
                shop_code  TEXT NOT NULL,
                stamped_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_stamps_user_shop
                ON stamps(user_id, shop_code, stamped_at);
            """
        )
        with open(SEED_PATH, encoding="utf-8") as f:
            shops = json.load(f)
        for s in shops:
            conn.execute(
                "INSERT OR REPLACE INTO shops (shop_code, name, name_ja, town)"
                " VALUES (?, ?, ?, ?)",
                (s["shop_code"], s["name"], s["name_ja"], s["town"]),
            )
        conn.commit()
        n = conn.execute("SELECT COUNT(*) FROM shops").fetchone()[0]
        log.info("db ready: %d shops seeded", n)
    finally:
        conn.close()


def get_shop(shop_code: str):
    conn = get_conn()
    try:
        return conn.execute(
            "SELECT shop_code, name, name_ja, town FROM shops WHERE shop_code = ?",
            (shop_code.lower(),),
        ).fetchone()
    finally:
        conn.close()


def already_stamped_today(user_id: str, shop_code: str) -> bool:
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT 1 FROM stamps WHERE user_id = ? AND shop_code = ?"
            " AND date(stamped_at) = date('now') LIMIT 1",
            (user_id, shop_code),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def record_stamp(user_id: str, shop_code: str) -> int:
    """Record a stamp; return the user's lifetime stamp total."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO stamps (user_id, shop_code, stamped_at) VALUES (?, ?, ?)",
            (user_id, shop_code, now),
        )
        conn.commit()
        total = conn.execute(
            "SELECT COUNT(*) FROM stamps WHERE user_id = ?", (user_id,)
        ).fetchone()[0]
        return total
    finally:
        conn.close()


def db_count_for_test(user_id: str, shop_code: str | None = None) -> int:
    """Test/simulator helper: count a user's stamps (optionally per shop, today)."""
    conn = get_conn()
    try:
        if shop_code:
            return conn.execute(
                "SELECT COUNT(*) FROM stamps WHERE user_id = ? AND shop_code = ?"
                " AND date(stamped_at) = date('now')",
                (user_id, shop_code),
            ).fetchone()[0]
        return conn.execute(
            "SELECT COUNT(*) FROM stamps WHERE user_id = ?", (user_id,)
        ).fetchone()[0]
    finally:
        conn.close()


# ------------------------------------------------------------- security ---
# (signature verification lives in channels.py so the registry can use it too)


# ---------------------------------------------------------- flex card ---

def build_stamp_flex(shop, total: int, theme: dict | None = None) -> dict:
    """Flex Message: shop names, progress circles toward GOAL_STAMPS, Sato line.

    Theme (per channel): accent color for filled circles, header text, and a
    Sato line variant. Defaults keep slice-1's look.
    """
    theme = theme or DEFAULT_THEME
    accent = theme.get("accent", DEFAULT_THEME["accent"])
    filled = min(total, GOAL_STAMPS)
    circles = [
        {
            "type": "text",
            "text": "●" if i < filled else "○",
            "size": "xl",
            "color": accent if i < filled else "#D8D8D8",
            "flex": 1,
            "align": "center",
        }
        for i in range(GOAL_STAMPS)
    ]
    return {
        "type": "flex",
        "altText": f"Stamp {filled} of {GOAL_STAMPS} — {shop['name']}",
        "contents": {
            "type": "bubble",
            "body": {
                "type": "box",
                "layout": "vertical",
                "spacing": "md",
                "contents": [
                    {
                        "type": "text",
                        "text": theme.get("header", DEFAULT_THEME["header"]),
                        "size": "xs",
                        "color": "#8A8A8A",
                    },
                    {
                        "type": "text",
                        "text": shop["name_ja"],
                        "weight": "bold",
                        "size": "xl",
                        "wrap": True,
                    },
                    {
                        "type": "text",
                        "text": f"{shop['name']} · {shop['town']}",
                        "size": "sm",
                        "color": "#8A8A8A",
                        "wrap": True,
                    },
                    {"type": "separator"},
                    {
                        "type": "box",
                        "layout": "horizontal",
                        "spacing": "sm",
                        "contents": circles,
                    },
                    {
                        "type": "text",
                        "text": f"{filled} / {GOAL_STAMPS}",
                        "size": "sm",
                        "color": "#8A8A8A",
                        "align": "center",
                    },
                    {"type": "separator"},
                    {
                        "type": "text",
                        "text": theme.get("sato_line", DEFAULT_THEME["sato_line"]),
                        "size": "sm",
                        "color": "#555555",
                        "wrap": True,
                        "style": "italic",
                    },
                ],
            },
        },
    }


def text_message(text: str) -> dict:
    return {"type": "text", "text": text}


# ------------------------------------------------------------ reply ---

def send_reply(reply_token: str, messages: list, token: str) -> dict:
    """
    Send via LINE reply API using the given channel's access token.
    With no/placeholder token the call is skipped (dev mode) — never
    crashes, never logs secrets.
    """
    if DRY_RUN_REPLY or not token or token.startswith("dev-"):
        alt = next((m.get("altText", m.get("text", "")) for m in messages), "")
        log.info("reply skipped (dev mode, no live credentials): %s", alt[:80])
        return {"dry_run": True}
    try:
        r = requests.post(
            LINE_REPLY_URL,
            headers={
                "Authorization": "Bearer " + token,
                "Content-Type": "application/json",
            },
            json={"replyToken": reply_token, "messages": messages},
            timeout=10,
        )
        if r.status_code >= 300:
            # status only — never the token, never the body
            log.warning("LINE reply API returned http=%s", r.status_code)
        else:
            log.info("LINE reply sent ok")
        return {"http_status": r.status_code}
    except Exception as exc:  # network down, DNS, timeouts — all graceful
        log.warning("LINE reply failed: %s", type(exc).__name__)
        return {"error": type(exc).__name__}


# ------------------------------------------------------------ webhook ---

@app.on_event("startup")
def on_startup():
    init_db()


@app.get("/health")
def health():
    return {
        "ok": True,
        "stamps_enabled": STAMPS_ENABLED,
        "dry_run_reply": DRY_RUN_REPLY,
        "channels": len(load_channels()),
    }


def legacy_channel() -> dict:
    """Slice-1 fallback when the registry is missing/unreadable."""
    return {
        "id": "legacy",
        "destination": "",
        "basic_id": OA_BASIC_ID,
        "display_name": "legacy",
        "enabled": True,
        "secret_env": "CHANNEL_SECRET",
        "token_env": "CHANNEL_ACCESS_TOKEN",
        "theme": dict(DEFAULT_THEME),
        "shops": None,
    }


@app.post("/webhook")
async def webhook(request: Request):
    body = await request.body()
    signature = request.headers.get("X-Line-Signature", "")

    try:
        payload = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return JSONResponse({"error": "bad json"}, status_code=400)

    destination = payload.get("destination", "")
    channels = load_channels()
    if channels:
        channel = resolve_channel(channels, destination, body, signature)
        if channel is None:
            # Never log the secret or the received signature value.
            return JSONResponse({"error": "bad signature"}, status_code=401)
    else:
        # No registry: behave exactly like slice 1.
        channel = legacy_channel()
        if not verify_signature(body, signature, get_secret(channel)):
            log.warning("rejected webhook: bad signature")
            return JSONResponse({"error": "bad signature"}, status_code=401)

    token = get_token(channel)
    results = []
    for event in payload.get("events", []):
        if event.get("type") != "message":
            continue
        msg = event.get("message", {})
        if msg.get("type") != "text":
            continue
        reply_token = event.get("replyToken", "")
        user_id = event.get("source", {}).get("userId", "")
        outcome = handle_text(user_id, event.get("message", {}).get("text", ""), channel)
        send_result = send_reply(reply_token, outcome["messages"], token)
        entry = {"outcome": outcome["outcome"], "channel": channel["id"]}
        if DRY_RUN_REPLY:
            # dev/test only: echo the payload that would have been sent
            entry["dev_flex_preview"] = outcome["messages"]
        entry["send"] = send_result
        results.append(entry)

    return {"ok": True, "events": results}


def handle_text(user_id: str, text: str, channel: dict) -> dict:
    """Core ritual logic. Returns {outcome, messages}. Stamp engine untouched."""
    if not STAMPS_ENABLED or not channel.get("enabled", True):
        return {
            "outcome": "paused",
            "messages": [
                text_message(
                    "The stamp rally is taking a quiet pause right now. "
                    "Your stamps are safe — come back soon."
                )
            ],
        }

    m = STAMP_RE.match(text or "")
    if not m:
        return {
            "outcome": "help",
            "messages": [
                text_message(
                    "Scan the QR code on the shop counter stand and tap send — "
                    "I'll stamp your card. (Or send STAMP:<shop-code>.)"
                )
            ],
        }

    shop_code = m.group(1).lower()
    shop = get_shop(shop_code)
    if not shop:
        return {
            "outcome": "unknown_shop",
            "messages": [
                text_message(
                    "Hmm, I don't recognize that shop code. "
                    "Scan the QR on the counter stand and I'll take it from there."
                )
            ],
        }

    allowed = channel.get("shops")
    if allowed is not None and shop_code not in allowed:
        return {
            "outcome": "not_in_rally",
            "messages": [
                text_message(
                    f"{shop['name_ja']} isn't part of this channel's rally — "
                    "but your other stamps are safe. Try one of this "
                    "channel's shops."
                )
            ],
        }

    if already_stamped_today(user_id, shop_code):
        return {
            "outcome": "duplicate",
            "messages": [
                text_message(
                    f"Already stamped at {shop['name_ja']} today — "
                    "one stamp per shop per day. See you tomorrow, "
                    "or try another shop."
                )
            ],
        }

    total = record_stamp(user_id, shop_code)
    log.info("stamp recorded: shop=%s total=%d channel=%s", shop_code, total, channel["id"])
    return {
        "outcome": "stamped",
        "messages": [build_stamp_flex(shop, total, channel.get("theme"))],
    }
