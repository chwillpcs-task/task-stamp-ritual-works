"""Slice 1 tests: signature verification, stamp dedup, Flex shape."""

import base64
import hashlib
import hmac
import json
import os
import tempfile

# --- env must be set BEFORE importing app (it reads config at import) ---
_tmp = tempfile.mkdtemp(prefix="slice1-test-")
os.environ["CHANNEL_SECRET"] = "test-secret-abc"
os.environ["STAMPS_DB"] = os.path.join(_tmp, "test.db")
os.environ["DRY_RUN_REPLY"] = "1"

import app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

SECRET = "test-secret-abc"
USER = "U_test_user_9"


def sign(body: bytes) -> str:
    mac = hmac.new(SECRET.encode(), body, hashlib.sha256).digest()
    return base64.b64encode(mac).decode()


def post(client, text, secret=SECRET, include_sig=True):
    payload = {
        "events": [
            {
                "type": "message",
                "replyToken": "tok",
                "source": {"type": "user", "userId": USER},
                "message": {"type": "text", "text": text},
            }
        ]
    }
    body = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if include_sig:
        mac = hmac.new(secret.encode(), body, hashlib.sha256).digest()
        headers["X-Line-Signature"] = base64.b64encode(mac).decode()
    return client.post("/webhook", content=body, headers=headers)


def outcome(resp):
    return resp.json()["events"][0]["outcome"]


def test_signature_good():
    with TestClient(app.app) as client:
        r = post(client, "hello")
        assert r.status_code == 200, r.text


def test_signature_bad():
    with TestClient(app.app) as client:
        r = post(client, "hello", secret="wrong-secret")
        assert r.status_code == 401


def test_signature_missing():
    with TestClient(app.app) as client:
        r = post(client, "hello", include_sig=False)
        assert r.status_code == 401


def test_stamp_records_and_dedups_daily():
    with TestClient(app.app) as client:
        r = post(client, "STAMP:uemon")
        assert r.status_code == 200
        assert outcome(r) == "stamped"
        flex = r.json()["events"][0]["dev_flex_preview"][0]
        assert flex["type"] == "flex"

        # same shop, same day -> duplicate, still 1 row
        r2 = post(client, "stamp:UEMON")
        assert outcome(r2) == "duplicate"
        assert app.db_count_for_test(USER, "uemon") == 1

        # different shop -> new stamp, total 2
        r3 = post(client, "STAMP:naitai")
        assert outcome(r3) == "stamped"
        assert app.db_count_for_test(USER) == 2


def test_unknown_shop():
    with TestClient(app.app) as client:
        r = post(client, "STAMP:atlantis")
        assert r.status_code == 200
        assert outcome(r) == "unknown_shop"


def test_non_stamp_text_gets_help():
    with TestClient(app.app) as client:
        r = post(client, "konnichiwa")
        assert outcome(r) == "help"


def test_stamps_disabled_switch(monkeypatch):
    monkeypatch.setattr(app, "STAMPS_ENABLED", False)
    with TestClient(app.app) as client:
        r = post(client, "STAMP:uemon")
        assert outcome(r) == "paused"
        assert "pause" in r.json()["events"][0]["dev_flex_preview"][0]["text"]


def test_flex_shape():
    shop = {"shop_code": "uemon", "name": "Hirose Farm Uemon's Heart",
            "name_ja": "広瀬牧場 ウエモンズハート", "town": "Obihiro, Hokkaido"}
    flex = app.build_stamp_flex(shop, 3)
    assert flex["type"] == "flex"
    assert "altText" in flex and flex["altText"]
    body = flex["contents"]["body"]["contents"]
    texts = [c.get("text", "") for c in body if c.get("type") == "text"]
    assert any("広瀬牧場" in t for t in texts), "JP shop name present"
    assert any("Hirose Farm" in t for t in texts), "romaji shop name present"
    assert any("3 / 5" in t for t in texts), "progress counter present"
    assert any("fresh milk and where you stand" in t for t in texts), "Sato line present"
    circles_box = next(c for c in body if c.get("layout") == "horizontal")
    circles = circles_box["contents"]
    assert len(circles) == 5
    assert sum(1 for c in circles if c["text"] == "●") == 3
    assert sum(1 for c in circles if c["text"] == "○") == 2
    # capped at goal
    flex7 = app.build_stamp_flex(shop, 7)
    circles7 = next(c for c in flex7["contents"]["body"]["contents"]
                    if c.get("layout") == "horizontal")["contents"]
    assert all(c["text"] == "●" for c in circles7)
