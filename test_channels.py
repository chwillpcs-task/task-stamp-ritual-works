"""Slice 2 tests: one backend, many channels.

Covers: destination-pinned routing, signature-fallback routing, per-channel
themes, a shared stamp rally across channels, per-channel on/off, shop
subsets, cross-channel secret rejection, and the slice-1 legacy fallback.
"""

import base64
import hashlib
import hmac
import json
import os

import pytest

import app  # noqa: E402  (already imported by test_app with test env)
import channels  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

SECRET_A = "test-secret-A"
SECRET_B = "test-secret-B"
SECRET_OFF = "test-secret-OFF"

REGISTRY = {
    "channels": [
        {
            "id": "chan-a",
            "destination": "DEST_A",
            "basic_id": "@aaa",
            "display_name": "Channel A",
            "enabled": True,
            "secret_env": "CH_A_SECRET",
            "token_env": "CH_A_TOKEN",
            "theme": {
                "accent": "#AA0000",
                "header": "A スタンプ",
                "sato_line": "Sato says A.",
            },
            "shops": None,
        },
        {
            "id": "chan-b",
            "destination": "",  # unpinned: routes by signature match
            "basic_id": "@bbb",
            "display_name": "Channel B",
            "enabled": True,
            "secret_env": "CH_B_SECRET",
            "token_env": "CH_B_TOKEN",
            "theme": {
                "accent": "#0000BB",
                "header": "B スタンプ",
                "sato_line": "Sato says B.",
            },
            "shops": ["uemon", "soft-house"],
        },
        {
            "id": "chan-off",
            "destination": "DEST_OFF",
            "basic_id": "@off",
            "display_name": "Channel Off",
            "enabled": False,
            "secret_env": "CH_OFF_SECRET",
            "token_env": "CH_OFF_TOKEN",
            "shops": None,
        },
    ]
}


@pytest.fixture()
def chan_env(tmp_path, monkeypatch):
    """Isolated registry, secrets, and database for each test."""
    reg = tmp_path / "channels.json"
    reg.write_text(json.dumps(REGISTRY), encoding="utf-8")
    db = tmp_path / "ch.db"
    monkeypatch.setenv("CHANNELS_PATH", str(reg))
    monkeypatch.setenv("CH_A_SECRET", SECRET_A)
    monkeypatch.setenv("CH_A_TOKEN", "token-A")
    monkeypatch.setenv("CH_B_SECRET", SECRET_B)
    monkeypatch.setenv("CH_B_TOKEN", "token-B")
    monkeypatch.setenv("CH_OFF_SECRET", SECRET_OFF)
    monkeypatch.setenv("CH_OFF_TOKEN", "token-OFF")
    monkeypatch.setattr(app, "DB_PATH", str(db))
    return str(reg)


def post_ch(client, text, secret, destination, user_id, include_sig=True):
    payload = {
        "destination": destination,
        "events": [
            {
                "type": "message",
                "replyToken": "tok",
                "source": {"type": "user", "userId": user_id},
                "message": {"type": "text", "text": text},
            }
        ],
    }
    body = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    if include_sig:
        mac = hmac.new(secret.encode(), body, hashlib.sha256).digest()
        headers["X-Line-Signature"] = base64.b64encode(mac).decode()
    return client.post("/webhook", content=body, headers=headers)


def flex_of(resp):
    return resp.json()["events"][0]["dev_flex_preview"][0]


def header_text(flex):
    body = flex["contents"]["body"]["contents"]
    return next(c["text"] for c in body if c.get("type") == "text" and "スタンプ" in c.get("text", ""))


def filled_colors(flex):
    body = flex["contents"]["body"]["contents"]
    box = next(c for c in body if c.get("layout") == "horizontal")
    return [c["color"] for c in box["contents"] if c["text"] == "●"]


def sato_line(flex):
    body = flex["contents"]["body"]["contents"]
    return next(c["text"] for c in body if c.get("type") == "text" and c.get("style") == "italic")


def test_pinned_channel_stamp_themed(chan_env):
    with TestClient(app.app) as client:
        r = post_ch(client, "STAMP:uemon", SECRET_A, "DEST_A", "U_ch_1")
        assert r.status_code == 200, r.text
        ev = r.json()["events"][0]
        assert ev["outcome"] == "stamped"
        assert ev["channel"] == "chan-a"
        flex = flex_of(r)
        assert header_text(flex) == "A スタンプ"
        assert filled_colors(flex) == ["#AA0000"]
        assert sato_line(flex) == "Sato says A."


def test_unpinned_channel_routes_by_signature(chan_env):
    with TestClient(app.app) as client:
        r = post_ch(client, "STAMP:uemon", SECRET_B, "DEST_SOMETHING_ELSE", "U_ch_2")
        assert r.status_code == 200, r.text
        ev = r.json()["events"][0]
        assert ev["outcome"] == "stamped"
        assert ev["channel"] == "chan-b"
        assert header_text(flex_of(r)) == "B スタンプ"


def test_rally_is_shared_across_channels(chan_env):
    """Cosmetic differences only: the stamp count is one rally."""
    with TestClient(app.app) as client:
        r1 = post_ch(client, "STAMP:uemon", SECRET_A, "DEST_A", "U_ch_3")
        assert r1.json()["events"][0]["outcome"] == "stamped"
        r2 = post_ch(client, "STAMP:soft-house", SECRET_B, "DEST_X", "U_ch_3")
        ev2 = r2.json()["events"][0]
        assert ev2["outcome"] == "stamped"
        assert ev2["channel"] == "chan-b"
        # second stamp overall -> 2 filled circles in channel B's theme
        assert len(filled_colors(flex_of(r2))) == 2
        assert filled_colors(flex_of(r2)) == ["#0000BB", "#0000BB"]
        assert app.db_count_for_test("U_ch_3") == 2


def test_cross_channel_secret_rejected(chan_env):
    with TestClient(app.app) as client:
        r = post_ch(client, "STAMP:uemon", SECRET_B, "DEST_A", "U_ch_4")
        assert r.status_code == 401
        assert app.db_count_for_test("U_ch_4") == 0


def test_missing_signature_rejected(chan_env):
    with TestClient(app.app) as client:
        r = post_ch(client, "STAMP:uemon", SECRET_A, "DEST_A", "U_ch_5", include_sig=False)
        assert r.status_code == 401


def test_disabled_channel_pauses(chan_env):
    with TestClient(app.app) as client:
        r = post_ch(client, "STAMP:uemon", SECRET_OFF, "DEST_OFF", "U_ch_6")
        assert r.status_code == 200
        ev = r.json()["events"][0]
        assert ev["outcome"] == "paused"
        assert ev["channel"] == "chan-off"
        assert app.db_count_for_test("U_ch_6") == 0


def test_shop_subset_enforced(chan_env):
    with TestClient(app.app) as client:
        r = post_ch(client, "STAMP:naitai", SECRET_B, "DEST_X", "U_ch_7")
        ev = r.json()["events"][0]
        assert ev["outcome"] == "not_in_rally"
        assert app.db_count_for_test("U_ch_7") == 0
        # allowed shop on the same channel still works
        r2 = post_ch(client, "STAMP:uemon", SECRET_B, "DEST_X", "U_ch_7")
        assert r2.json()["events"][0]["outcome"] == "stamped"


def test_global_switch_still_pauses_channels(chan_env, monkeypatch):
    monkeypatch.setattr(app, "STAMPS_ENABLED", False)
    with TestClient(app.app) as client:
        r = post_ch(client, "STAMP:uemon", SECRET_A, "DEST_A", "U_ch_8")
        assert r.json()["events"][0]["outcome"] == "paused"
        assert app.db_count_for_test("U_ch_8") == 0


def test_legacy_fallback_without_registry(tmp_path, monkeypatch):
    """No registry file -> behaves exactly like slice 1."""
    db = tmp_path / "legacy.db"
    monkeypatch.setenv("CHANNELS_PATH", str(tmp_path / "nope.json"))
    monkeypatch.setattr(app, "DB_PATH", str(db))
    with TestClient(app.app) as client:
        # test_app's env: CHANNEL_SECRET=test-secret-abc, no destination
        r = post_ch(client, "STAMP:uemon", "test-secret-abc", "", "U_ch_9")
        assert r.status_code == 200, r.text
        ev = r.json()["events"][0]
        assert ev["outcome"] == "stamped"
        assert ev["channel"] == "legacy"
        assert header_text(flex_of(r)) == "TASK スタンプ"
