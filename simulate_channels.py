"""
Slice 2 live simulator — proves multi-channel routing with zero LINE involvement.

Launches the real service as a subprocess (its own registry, secrets, and
database), then drives it over HTTP with HMAC-signed webhook payloads:

  1. sim-a STAMP:uemon      -> 200, outcome=stamped, channel=sim-a, A theme
  2. sim-a same stamp again  -> 200, outcome=duplicate (daily dedup intact)
  3. sim-b STAMP:soft-house -> 200, outcome=stamped, channel=sim-b, B theme,
                               total=2 (one rally shared across channels)
  4. sim-b STAMP:naitai     -> 200, outcome=not_in_rally (shop subset)
  5. DEST_SIM_A + secret B  -> 401 (cross-channel secret rejected)
  6. sim-off STAMP:uemon    -> 200, outcome=paused (per-channel switch)
  7. no signature           -> 401

Usage:  python3 simulate_channels.py
"""

import base64
import hashlib
import hmac
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time

import requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="slice2-sim-")
PORT = 8001
BASE = f"http://127.0.0.1:{PORT}"

SECRET_A = "sim-secret-A"
SECRET_B = "sim-secret-B"
SECRET_OFF = "sim-secret-OFF"

REGISTRY = {
    "channels": [
        {
            "id": "sim-a",
            "destination": "DEST_SIM_A",
            "basic_id": "@sima",
            "display_name": "Sim A",
            "enabled": True,
            "secret_env": "SIM_A_SECRET",
            "token_env": "SIM_A_TOKEN",
            "theme": {"accent": "#AA0000", "header": "Sim A スタンプ",
                      "sato_line": "Sim Sato A."},
            "shops": None,
        },
        {
            "id": "sim-b",
            "destination": "",
            "basic_id": "@simb",
            "display_name": "Sim B",
            "enabled": True,
            "secret_env": "SIM_B_SECRET",
            "token_env": "SIM_B_TOKEN",
            "theme": {"accent": "#0000BB", "header": "Sim B スタンプ",
                      "sato_line": "Sim Sato B."},
            "shops": ["uemon", "soft-house"],
        },
        {
            "id": "sim-off",
            "destination": "DEST_SIM_OFF",
            "basic_id": "@simoff",
            "display_name": "Sim Off",
            "enabled": False,
            "secret_env": "SIM_OFF_SECRET",
            "token_env": "SIM_OFF_TOKEN",
            "shops": None,
        },
    ]
}

passed = []


def check(name, cond, detail=""):
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    passed.append(cond)
    if not cond:
        sys.exit(1)


def sign(body: bytes, secret: str) -> str:
    return base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()


def post_event(text, secret, destination, user_id, include_sig=True):
    payload = {
        "destination": destination,
        "events": [{
            "type": "message",
            "replyToken": "sim-reply-token",
            "source": {"type": "user", "userId": user_id},
            "message": {"type": "text", "text": text},
        }],
    }
    body = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if include_sig:
        headers["X-Line-Signature"] = sign(body, secret)
    return requests.post(f"{BASE}/webhook", data=body, headers=headers, timeout=10)


def db_count(user_id, shop_code=None):
    conn = sqlite3.connect(os.path.join(TMP, "sim.db"))
    try:
        if shop_code:
            return conn.execute(
                "SELECT COUNT(*) FROM stamps WHERE user_id=? AND shop_code=?"
                " AND date(stamped_at)=date('now')", (user_id, shop_code)).fetchone()[0]
        return conn.execute(
            "SELECT COUNT(*) FROM stamps WHERE user_id=?", (user_id,)).fetchone()[0]
    finally:
        conn.close()


def flex_of(resp):
    return resp.json()["events"][0]["dev_flex_preview"][0]


def main():
    reg_path = os.path.join(TMP, "channels.json")
    with open(reg_path, "w", encoding="utf-8") as f:
        json.dump(REGISTRY, f)

    venv_python = os.path.join(BASE_DIR, ".venv", "bin", "python")
    py = venv_python if os.path.exists(venv_python) else sys.executable
    env = dict(os.environ, CHANNELS_PATH=reg_path,
               STAMPS_DB=os.path.join(TMP, "sim.db"),
               DRY_RUN_REPLY="1",
               SIM_A_SECRET=SECRET_A, SIM_A_TOKEN="tok-A",
               SIM_B_SECRET=SECRET_B, SIM_B_TOKEN="tok-B",
               SIM_OFF_SECRET=SECRET_OFF, SIM_OFF_TOKEN="tok-OFF",
               # legacy names set to dummies: proves the registry path is used
               CHANNEL_SECRET="unused-legacy", CHANNEL_ACCESS_TOKEN="unused-legacy")
    proc = subprocess.Popen([py, "-m", "uvicorn", "app:app",
                             "--host", "127.0.0.1", "--port", str(PORT)],
                            cwd=BASE_DIR, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                h = requests.get(f"{BASE}/health", timeout=2)
                if h.status_code == 200:
                    break
            except requests.RequestException:
                time.sleep(0.2)
        else:
            check("service boots", False, "no /health after 20s")
        check("health reports 3 channels", h.json().get("channels") == 3,
              str(h.json()))

        u1, u2 = "U_simA_001", "U_simB_002"

        r = post_event("STAMP:uemon", SECRET_A, "DEST_SIM_A", u1)
        ev = r.json()["events"][0]
        check("1. sim-a stamp recorded", r.status_code == 200 and ev["outcome"] == "stamped"
              and ev["channel"] == "sim-a", f"channel={ev.get('channel')}")
        flex = flex_of(r)
        hdr = next(c["text"] for c in flex["contents"]["body"]["contents"]
                   if c.get("type") == "text" and "スタンプ" in c.get("text", ""))
        check("1. sim-a theme applied", hdr == "Sim A スタンプ", hdr)
        check("1. db has the stamp", db_count(u1, "uemon") == 1)

        r = post_event("STAMP:uemon", SECRET_A, "DEST_SIM_A", u1)
        check("2. duplicate declined", r.json()["events"][0]["outcome"] == "duplicate"
              and db_count(u1, "uemon") == 1)

        r = post_event("STAMP:soft-house", SECRET_B, "DEST_WHATEVER", u1)
        ev = r.json()["events"][0]
        check("3. sim-b stamp recorded (fallback routing)",
              r.status_code == 200 and ev["outcome"] == "stamped" and ev["channel"] == "sim-b",
              f"channel={ev.get('channel')}")
        flex = flex_of(r)
        hdr = next(c["text"] for c in flex["contents"]["body"]["contents"]
                   if c.get("type") == "text" and "スタンプ" in c.get("text", ""))
        box = next(c for c in flex["contents"]["body"]["contents"] if c.get("layout") == "horizontal")
        filled = [c["color"] for c in box["contents"] if c["text"] == "●"]
        check("3. rally shared (2/5) with B theme",
              len(filled) == 2 and set(filled) == {"#0000BB"} and hdr == "Sim B スタンプ",
              f"{len(filled)}/5 {hdr}")

        r = post_event("STAMP:naitai", SECRET_B, "DEST_WHATEVER", u2)
        check("4. shop subset enforced",
              r.json()["events"][0]["outcome"] == "not_in_rally" and db_count(u2) == 0)

        r = post_event("STAMP:uemon", SECRET_B, "DEST_SIM_A", u2)
        check("5. cross-channel secret rejected", r.status_code == 401)

        r = post_event("STAMP:uemon", SECRET_OFF, "DEST_SIM_OFF", u2)
        ev = r.json()["events"][0]
        check("6. disabled channel pauses",
              r.status_code == 200 and ev["outcome"] == "paused" and db_count(u2) == 0)

        r = post_event("STAMP:uemon", SECRET_A, "DEST_SIM_A", u2, include_sig=False)
        check("7. missing signature rejected", r.status_code == 401)

        print(f"\n{sum(passed)}/{len(passed)} simulator checks passed.")
    finally:
        proc.terminate()
        proc.wait(timeout=10)


if __name__ == "__main__":
    main()
