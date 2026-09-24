"""
Slice 2 — channel registry: one backend, many LINE channels.

Every LINE channel (official account) points its webhook at this one backend.
Each webhook event carries a `destination` field = the bot user ID of the OA
that received it. This module maps an event to a channel entry holding
non-secret config. Secrets stay in environment variables named per channel,
so they never land in the repo.

channels.json schema:
  { "channels": [ {
      "id": "task-community",    # our label; also the theme/ops handle
      "destination": "U...",      # LINE bot user ID; "" = route by signature
      "basic_id": "@664ickdm",    # for QR links / display
      "display_name": "TASK Community",
      "enabled": true,            # Law 2: per-channel on/off switch
      "secret_env": "CHANNEL_SECRET",       # env var with channel secret
      "token_env": "CHANNEL_ACCESS_TOKEN",  # env var with access token
      "theme": { "accent": "#E8A33D", "header": "TASK スタンプ",
                 "sato_line": "..." },
      "shops": null               # null = all shops; or ["uemon", ...]
  } ] }

Routing rule (stateless, no writes):
  1. If the event's destination matches a pinned entry, verify the signature
     with that entry's secret only. Mismatch -> reject (401).
  2. Otherwise, try unpinned entries in order; the first whose secret verifies
     the signature wins. (This is how a single channel with no destination
     configured keeps working, exactly like slice 1.)
  3. No match -> reject (401).

The registry is read on every webhook, so editing channels.json (e.g.
flipping "enabled") takes effect without restarting the service.
"""

import base64
import hashlib
import hmac
import json
import logging
import os

log = logging.getLogger("slice2")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CHANNELS_PATH = os.path.join(BASE_DIR, "channels.json")

DEFAULT_THEME = {
    "accent": "#E8A33D",
    "header": "TASK スタンプ",
    "sato_line": (
        "A quiet moment worth remembering doesn't need fanfare — "
        "just fresh milk and where you stand."
    ),
}


def channels_path() -> str:
    return os.environ.get("CHANNELS_PATH", DEFAULT_CHANNELS_PATH)


def load_channels(path: str | None = None) -> list:
    """Load the registry. Returns [] when missing/unreadable (caller falls back)."""
    p = path or channels_path()
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, ValueError) as exc:
        log.warning("channel registry unreadable (%s): %s", p, type(exc).__name__)
        return []
    channels = []
    for entry in data.get("channels", []):
        theme = dict(DEFAULT_THEME)
        if entry.get("theme"):
            theme.update(entry["theme"])
        channels.append(
            {
                "id": entry.get("id", ""),
                "destination": entry.get("destination") or "",
                "basic_id": entry.get("basic_id", ""),
                "display_name": entry.get("display_name", entry.get("id", "")),
                "enabled": bool(entry.get("enabled", True)),
                "secret_env": entry.get("secret_env", "CHANNEL_SECRET"),
                "token_env": entry.get("token_env", "CHANNEL_ACCESS_TOKEN"),
                "theme": theme,
                # None = every shop; a list restricts this channel's rally
                "shops": entry.get("shops"),
            }
        )
    return channels


def get_secret(channel: dict) -> str:
    return os.environ.get(channel["secret_env"], "")


def get_token(channel: dict) -> str:
    return os.environ.get(channel["token_env"], "")


def verify_signature(body: bytes, signature: str, secret: str) -> bool:
    """HMAC-SHA256 of the raw body, base64-encoded, constant-time compare."""
    if not signature or not secret:
        return False
    mac = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()
    expected = base64.b64encode(mac).decode("utf-8")
    return hmac.compare_digest(expected, signature)


def resolve_channel(channels: list, destination: str, body: bytes,
                    signature: str) -> dict | None:
    """
    Map a webhook event to its channel entry (or None = reject).
    Never logs secrets or signature values.
    """
    # 1. pinned destination: that channel's secret, and no other
    if destination:
        for ch in channels:
            if ch["destination"] and ch["destination"] == destination:
                if verify_signature(body, signature, get_secret(ch)):
                    return ch
                log.warning("rejected webhook: bad signature for channel %s", ch["id"])
                return None
    # 2. unpinned entries: first signature match wins
    for ch in channels:
        if not ch["destination"] and verify_signature(body, signature, get_secret(ch)):
            return ch
    log.warning("rejected webhook: no channel matched")
    return None
