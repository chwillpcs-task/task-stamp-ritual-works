# Counter-stand copy — shop stamp ritual (option B fallback)

Status: current as of 2026-09-23. The one-tap prefill QR
(`oaMessage` deep link) gives "User not found" on the test phone,
so stands use the add-friend QR plus a printed stamp code until
the prefill quirk is resolved.

## What to print (per shop)

A card with, top to bottom:

1. The QR code: `qr/add-friend.png`
   (opens the TASK Community official account add-friend page)
2. This text, large enough to read at arm's length:

   **TASK スタンプラリー**
   1. Scan the QR and add TASK Community
   2. In the chat, send:

   **STAMP:uemon**

   (swap the code per shop: `STAMP:soft-house`, `STAMP:naitai`)

3. Small footer: "One stamp per shop per day · 5 stamps fill your card"

## Shop codes

| Shop | Code to print |
|---|---|
| Uemon's Heart (Hirose Farm, Obihiro) | STAMP:uemon |
| Soft Cream House | STAMP:soft-house |
| Naitai Terrace (Kamishihoro) | STAMP:naitai |

## When the prefill QR works again

Replace `qr/add-friend.png` with `qr/<code>-prefill.png` and shorten
the stand text to: "Scan, tap send — your stamp card appears."
