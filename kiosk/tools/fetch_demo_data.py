#!/usr/bin/env python3
"""Snapshot of the sellable InvenTree parts for `--demo`: names, categories,
sale prices, stock and thumbnails. Read-only; nothing in InvenTree changes.

    INVENTREE_URL=http://10.72.1.246 INVENTREE_TOKEN=... python3 tools/fetch_demo_data.py

INVENTREE_HOST overrides the Host header, for reaching the server over
Tailscale while its proxy only answers to the LAN address.
Writes demo-data/catalog.json and demo-data/images/.
"""
import json
import os
import pathlib
import urllib.request

URL = os.environ["INVENTREE_URL"].rstrip("/")
HEADERS = {"Authorization": f"Token {os.environ['INVENTREE_TOKEN']}"}
if os.environ.get("INVENTREE_HOST"):
    HEADERS["Host"] = os.environ["INVENTREE_HOST"]
OUT = pathlib.Path(__file__).resolve().parent.parent / "demo-data"


def get(path):
    req = urllib.request.Request(URL + path, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.read()


def get_all(path):
    rows = []
    while path:
        data = json.loads(get(path))
        if isinstance(data, list):
            return data
        rows += data["results"]
        nxt = data.get("next")
        path = nxt[nxt.index("/api/"):] if nxt else None
    return rows


# Cheapest-quantity sale price break per part, as refresh_sale_prices does.
best = {}
for b in get_all("/api/part/sale-price/?limit=500"):
    qty, price = float(b["quantity"]), float(b["price"])
    if b["part"] not in best or qty < best[b["part"]][0]:
        best[b["part"]] = (qty, price)

(OUT / "images").mkdir(parents=True, exist_ok=True)
catalog = []
for p in get_all("/api/part/?active=true&limit=500"):
    if p["pk"] not in best:
        continue  # no sale price: not sold at the kiosk
    image = None
    if p.get("thumbnail"):
        name = f"{p['pk']}{pathlib.Path(p['thumbnail']).suffix or '.png'}"
        try:
            (OUT / "images" / name).write_bytes(get(p["thumbnail"]))
            image = f"images/{name}"
        except Exception as e:
            print(f"no image for {p['name']}: {e}")
    catalog.append({
        "pk": p["pk"],
        "name": p["name"],
        "category": (p.get("category_name") or "uncategorized").lower(),
        "price": best[p["pk"]][1],
        "stock": float(p.get("in_stock") or 0),
        "barcodes": [c for c in (p.get("IPN"), p.get("barcode")) if c],
        "image": image,
    })

catalog.sort(key=lambda c: (c["category"], c["name"]))
(OUT / "catalog.json").write_text(json.dumps(catalog, indent=1, ensure_ascii=False))
print(f"{len(catalog)} parts, {sum(1 for c in catalog if c['image'])} images -> {OUT}")
