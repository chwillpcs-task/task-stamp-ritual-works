"""
Generate counter-stand QR codes, one PNG per shop.

Each QR encodes a LINE OA deep link that opens the TASK official account
chat with the stamp message prefilled — the customer scans, taps send,
and the ritual begins:

    https://line.me/R/oaMessage/{OA_BASIC_ID}/?STAMP:{shop-code}

Env: OA_BASIC_ID (default: dev-oa-basic-id — replace before printing).

Print notes: black on white, at least 3-4 cm square, matte paper,
test-scan with LINE's built-in QR reader before laminating.
"""

import json
import os

import qrcode

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OA_BASIC_ID = os.environ.get("OA_BASIC_ID", "dev-oa-basic-id")


def main() -> None:
    with open(os.path.join(BASE_DIR, "seed_shops.json"), encoding="utf-8") as f:
        shops = json.load(f)
    out_dir = os.path.join(BASE_DIR, "qr")
    os.makedirs(out_dir, exist_ok=True)

    if OA_BASIC_ID == "dev-oa-basic-id":
        print("NOTE: OA_BASIC_ID is the dev placeholder — replace it before printing.")

    for s in shops:
        url = f"https://line.me/R/oaMessage/{OA_BASIC_ID}/?STAMP:{s['shop_code']}"
        img = qrcode.make(url, box_size=10, border=4)
        path = os.path.join(out_dir, f"{s['shop_code']}.png")
        img.save(path)
        print(f"{s['shop_code']:12s} -> {path}")
        print(f"{'':12s}    {url}")


if __name__ == "__main__":
    main()
