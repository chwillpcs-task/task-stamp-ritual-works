"""
Slice 1 — the shop stamp ritual (TASK L3C).

A customer at a soft-serve shop scans a QR code on the counter stand.
LINE opens the TASK official account chat with a prefilled STAMP:<shop-code>
message. The customer taps send; this service verifies the LINE signature,
records the stamp (one per user per shop per day), and replies with a
Flex Message stamp card showing progress toward 5.

Env vars:
  CHANNEL_SECRET        LINE channel secret (default: dev-secret-placeholder)
  CHANNEL_ACCESS_TOKEN  LINE channel access token (default: unset -> replies skipped)
  OA_BASIC_ID           LINE OA basic ID for QR links (default: dev-oa-basic-id)
  STAMPS_ENABLED        "true" (default) | "false" — Law 2 feature switch
  STAMPS_DB             SQLite path (default: ./stamps.db)
  DRY_RUN_REPLY         "1" -> skip LINE HTTP call; webhook response includes
                        the Flex payload under dev_flex_preview (dev/test only)
"""

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import sqlite3
from datetime import datetime, timezone

import requests
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("slice1")

# ---------------------------------------------------------------- config ---

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

SATO_LINE = (
    "A quiet moment worth remembering doesn't need fanfare — "
    "just fresh milk and where you stand."
)

app = FastAPI(title="TASK L3C — slice 1: stamp ritual")


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

def verify_signature(body: bytes, signature: str) -> bool:
    """HMAC-SHA256 of the raw body, base64-encoded, constant-time compare."""
    if not signature:
        return False
    mac = hmac.new(CHANNEL_SECRET.encode("utf-8"), body, hashlib.sha256).digest()
    expected = base64.b64encode(mac).decode("utf-8")
    return hmac.compare_digest(expected, signature)


# ---------------------------------------------------------- flex card ---

def build_stamp_flex(shop, total: int) -> dict:
    """Flex Message: shop names, progress circles toward GOAL_STAMPS, Sato line."""
    filled = min(total, GOAL_STAMPS)
    circles = [
        {
            "type": "text",
            "text": "●" if i < filled else "○",
            "size": "xl",
            "color": "#E8A33D" if i < filled else "#D8D8D8",
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
                        "text": "TASK スタンプ",
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
                        "text": SATO_LINE,
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

def send_reply(reply_token: str, messages: list) -> dict:
    """
    Send via LINE reply API. With no/placeholder credentials the call is
    skipped (dev mode) — never crashes, never logs secrets.
    """
    if DRY_RUN_REPLY or not CHANNEL_ACCESS_TOKEN or CHANNEL_ACCESS_TOKEN.startswith("dev-"):
        alt = next((m.get("altText", m.get("text", "")) for m in messages), "")
        log.info("reply skipped (dev mode, no live credentials): %s", alt[:80])
        return {"dry_run": True}
    try:
        r = requests.post(
            LINE_REPLY_URL,
            headers={
                "Authorization": "Bearer " + CHANNEL_ACCESS_TOKEN,
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
    }


@app.post("/webhook")
async def webhook(request: Request):
    body = await request.body()
    signature = request.headers.get("X-Line-Signature", "")

    if not verify_signature(body, signature):
        # Never log the secret or the received signature value.
        log.warning("rejected webhook: bad signature")
        return JSONResponse({"error": "bad signature"}, status_code=401)

    try:
        payload = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return JSONResponse({"error": "bad json"}, status_code=400)

    results = []
    for event in payload.get("events", []):
        if event.get("type") != "message":
            continue
        msg = event.get("message", {})
        if msg.get("type") != "text":
            continue
        reply_token = event.get("replyToken", "")
        user_id = event.get("source", {}).get("userId", "")
        outcome = handle_text(user_id, event.get("message", {}).get("text", ""))
        send_result = send_reply(reply_token, outcome["messages"])
        entry = {"outcome": outcome["outcome"]}
        if DRY_RUN_REPLY:
            # dev/test only: echo the payload that would have been sent
            entry["dev_flex_preview"] = outcome["messages"]
        entry["send"] = send_result
        results.append(entry)

    return {"ok": True, "events": results}


def handle_text(user_id: str, text: str) -> dict:
    """Core ritual logic. Returns {outcome, messages}."""
    if not STAMPS_ENABLED:
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
    log.info("stamp recorded: shop=%s total=%d", shop_code, total)
    return {"outcome": "stamped", "messages": [build_stamp_flex(shop, total)]}
