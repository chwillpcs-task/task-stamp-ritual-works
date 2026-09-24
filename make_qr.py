"""
Generate QR codes for the shop stamp ritual.

Two link styles (see notes below):

1. ADD-FRIEND (current default, verified working):
     https://line.me/R/ti/p/%40664ickdm
   One shared QR. The customer adds the OA, then sends STAMP:<shop-code>
   printed on the counter stand. The ti/p add-friend page was verified
   to resolve for this OA.

2. OA-MESSAGE PREFILL (experimental, pending LINE client fix):
     https://line.me/R/oaMessage/%40664ickdm/?STAMP:<shop-code>
   One QR per shop; opens the OA chat with the stamp text prefilled.
   As of 2026-09-23 this gives "User not found" on the test phone in
   all variants (bare ID, raw @, %40) and both scanners (camera app,
   LINE built-in reader). Kept in the generator for retesting later.

Env: OA_BASIC_ID (default: 664ickdm).

Print notes: black on white, at least 3-4 cm square, matte paper.
"""

import os

import qrcode

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OA_BASIC_ID = os.environ.get("OA_BASIC_ID", "664ickdm")


def save(url: str, filename: str) -> None:
    img = qrcode.make(url, box_size=10, border=4)
    path = os.path.join(BASE_DIR, "qr", filename)
    img.save(path)
    print(f"{filename:16s} -> {url}")


def main() -> None:
    os.makedirs(os.path.join(BASE_DIR, "qr"), exist_ok=True)
    # 1. add-friend fallback (current)
    save(f"https://line.me/R/ti/p/%40{OA_BASIC_ID}", "add-friend.png")
    # 2. per-shop prefill (experimental)
    for code in ("uemon", "soft-house", "naitai"):
        save(
            f"https://line.me/R/oaMessage/%40{OA_BASIC_ID}/?STAMP:{code}",
            f"{code}-prefill.png",
        )


if __name__ == "__main__":
    main()
