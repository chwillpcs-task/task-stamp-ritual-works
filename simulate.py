"""
Slice 1 end-to-end simulator — proves the full ritual with zero LINE involvement.

Crafts fake LINE webhook payloads, HMAC-signs them with the channel secret,
POSTs to the local service, and asserts each step of the loop:

  1. valid STAMP:uemon      -> 200, outcome=stamped,  DB has the stamp
  2. same stamp again        -> 200, outcome=duplicate, DB unchanged (daily dedup)
  3. STAMP:soft-house        -> 200, outcome=stamped,  user total = 2
  4. bad signature           -> 401, nothing recorded
  5. STAMP:nope              -> 200, outcome=unknown_shop

Run the service first with DRY_RUN_REPLY=1 (webhook responses then include
the Flex payload under dev_flex_preview, which step 1 inspects).

Env (must match the service): CHANNEL_SECRET, STAMPS_DB, PORT (default 8000).
"""

import base64
import hashlib
import hmac
import json
import os
import sqlite3
import sys

import requests

SECRET = os.environ.get("CHANNEL_SECRET", "dev-secret-placeholder")
DB_PATH = os.environ.get("STAMPS_DB", os.path.join(os.path.dirname(os.path.abspath(__file__)), "stamps.db"))
BASE = f"http://127.0.0.1:{os.environ.get('PORT', '8000')}"
USER = "U_sim_user_001"

passed = []


def check(name: str, cond: bool, detail: str = "") -> None:
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail else ""))
    passed.append(cond)
    if not cond:
        sys.exit(1)


def sign(body: bytes) -> str:
    mac = hmac.new(SECRET.encode(), body, hashlib.sha256).digest()
    return base64.b64encode(mac).decode()


def post_event(text: str, user_id: str = USER, signature: str | None = None) -> requests.Response:
    payload = {
        "events": [
            {
                "type": "message",
                "replyToken": "sim-reply-token",
                "source": {"type": "user", "userId": user_id},
                "message": {"type": "text", "text": text},
            }
        ]
    }
    body = json.dumps(payload).encode()
    sig = signature if signature is not None else sign(body)
    return requests.post(
        BASE + "/webhook",
        data=body,
        headers={"Content-Type": "application/json", "X-Line-Signature": sig},
        timeout=10,
    )


def db_count(user_id: str, shop_code: str | None = None) -> int:
    conn = sqlite3.connect(DB_PATH)
    try:
        if shop_code:
            q = ("SELECT COUNT(*) FROM stamps WHERE user_id=? AND shop_code=? "
                 "AND date(stamped_at)=date('now')")
            return conn.execute(q, (user_id, shop_code)).fetchone()[0]
        return conn.execute("SELECT COUNT(*) FROM stamps WHERE user_id=?", (user_id,)).fetchone()[0]
    finally:
        conn.close()


def outcome_of(resp: requests.Response) -> str:
    return resp.json()["events"][0]["outcome"]


def main() -> None:
    print(f"simulating against {BASE} (db: {DB_PATH})\n")

    # 1. first stamp at uemon
    r = post_event("STAMP:uemon")
    check("valid stamp -> 200", r.status_code == 200, f"got {r.status_code}")
    check("outcome=stamped", outcome_of(r) == "stamped")
    flex = r.json()["events"][0].get("dev_flex_preview", [{}])[0]
    check("flex preview present", flex.get("type") == "flex")
    circles = flex["contents"]["body"]["contents"][4]["contents"]
    check("5 progress circles", len(circles) == 5)
    check("1 filled circle", sum(1 for c in circles if c["text"] == "●") == 1)
    check("stamp in db", db_count(USER, "uemon") == 1)

    # 2. same shop same day -> duplicate, no new row
    r = post_event("stamp:UEMON")  # case-insensitive
    check("repeat stamp -> 200", r.status_code == 200)
    check("outcome=duplicate", outcome_of(r) == "duplicate")
    check("daily dedup holds", db_count(USER, "uemon") == 1)

    # 3. second shop -> total 2
    r = post_event("  STAMP:soft-house  ")  # whitespace-tolerant
    check("second shop -> stamped", outcome_of(r) == "stamped")
    check("user total = 2", db_count(USER) == 2)

    # 4. bad signature -> 401, nothing recorded
    r = post_event("STAMP:naitai", signature="bogus")
    check("bad signature -> 401", r.status_code == 401, f"got {r.status_code}")
    check("nothing recorded for naitai", db_count(USER, "naitai") == 0)

    # 5. unknown shop code
    r = post_event("STAMP:nope")
    check("unknown shop -> 200", r.status_code == 200)
    check("outcome=unknown_shop", outcome_of(r) == "unknown_shop")

    print(f"\nALL {len(passed)} SIMULATION CHECKS PASSED — the loop works end to end.")


if __name__ == "__main__":
    main()
