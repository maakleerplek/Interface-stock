"""Put the weight items and filament spools in InvenTree, with test prices.

    INVENTREE_URL=http://10.72.3.68 INVENTREE_TOKEN=... python scripts/seed_bulk_items.py

Safe to run twice: it finds categories, locations and parts by name and only
creates what is missing. Prices and stock are only set on a fresh part, so real
values entered later in InvenTree are never overwritten.

Weight items are sold per 100 g block. Nobody counts the blocks, so each gets
one stock item with a large quantity in an uncounted location: the scanner,
the web till and the TV all expect stock, and this way they need no changes.
"""
import os

import requests

URL = os.environ.get("INVENTREE_URL", "http://10.72.3.68").rstrip("/")
S = requests.Session()
S.headers["Authorization"] = f"Token {os.environ['INVENTREE_TOKEN']}"
S.verify = False

BULK_QTY = 10000

# (category, location, [(name, IPN, price, stock)])
ITEMS = [
    ("Per gewicht", "Bulk (niet geteld)", [
        ("Nuts & bolts (100 g)", "BULK-NB-100G", 5.00, BULK_QTY),
    ]),
    ("Filament", "Filament-rek", [
        ("PLA zwart (spoel)", "FIL-PLA-BLK", 20.00, 3),
        ("PLA wit (spoel)", "FIL-PLA-WHT", 20.00, 3),
        ("PLA grijs (spoel)", "FIL-PLA-GRY", 20.00, 3),
        ("PETG zwart (spoel)", "FIL-PETG-BLK", 20.00, 3),
    ]),
]


def get(path, **params):
    r = S.get(f"{URL}/api/{path}", params={"limit": 500, **params}, timeout=15)
    r.raise_for_status()
    data = r.json()
    return data["results"] if isinstance(data, dict) else data


def post(path, body):
    r = S.post(f"{URL}/api/{path}", json=body, timeout=15)
    if not r.ok:
        raise SystemExit(f"POST {path} failed: {r.status_code} {r.text}")
    return r.json()


def find_or_create(path, name):
    for row in get(path, name=name):
        if row["name"] == name:
            return row["pk"]
    pk = post(path, {"name": name})["pk"]
    print(f"created {path} '{name}' = {pk}")
    return pk


def main():
    for cat_name, loc_name, parts in ITEMS:
        cat = find_or_create("part/category/", cat_name)
        loc = find_or_create("stock/location/", loc_name)
        existing = {p["IPN"]: p["pk"] for p in get("part/", category=cat) if p.get("IPN")}
        for name, ipn, price, qty in parts:
            if ipn in existing:
                print(f"  exists: {name} ({ipn})")
                continue
            pk = post("part/", {
                "name": name, "IPN": ipn, "category": cat, "active": True,
                "salable": True, "purchaseable": True, "component": False,
                "minimum_stock": 0, "description": name,
            })["pk"]
            post("part/sale-price/", {"part": pk, "quantity": 1, "price": price, "price_currency": "EUR"})
            post("stock/", {"part": pk, "location": loc, "quantity": qty})
            print(f"  created: {name} ({ipn}) €{price:.2f}, stock {qty}")


if __name__ == "__main__":
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    main()
