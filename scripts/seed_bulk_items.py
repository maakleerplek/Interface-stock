"""Put the weight items and the filament spools in InvenTree.

    INVENTREE_URL=http://10.72.3.68 INVENTREE_TOKEN=... python scripts/seed_bulk_items.py

Safe to run twice: it finds categories, locations and parts by name or IPN and
only creates what is missing. Prices and stock are only set on a fresh part, so
real values entered later in InvenTree are never overwritten. Barcodes and
images are filled in on existing parts when they are still empty.

Weight items are sold per 100 g block. Nobody counts the blocks, so each gets
one stock item with a large quantity in an uncounted location: the scanner,
the web till and the TV all expect stock, and this way they need no changes.

Filament: FormFutura 1 kg spools, bought at FILAMENT_COST and sold at
FILAMENT_PRICE. The barcode is the EAN on the box and spool label, so scanning
the spool itself finds the part. Pictures are in filament_images/ (manufacturer
product shots; Matt Autumn Red is recoloured from Forest Green, no shot exists).
"""
import os

import requests

URL = os.environ.get("INVENTREE_URL", "http://10.72.3.68").rstrip("/")
S = requests.Session()
S.headers["Authorization"] = f"Token {os.environ['INVENTREE_TOKEN']}"
S.verify = False

IMAGES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "filament_images")
BULK_QTY = 10000
FILAMENT_COST = 8.44
FILAMENT_PRICE = 20.00

# (category, location, [(name, IPN, sale price, purchase price, stock, barcode)])
ITEMS = [
    ("Per gewicht", "Bulk (niet geteld)", [
        ("Nuts & bolts (100 g)", "BULK-NB-100G", 5.00, None, BULK_QTY, None),
    ]),
    ("Filament", "Filament-rek", [
        (f"{name} (1 kg)", ipn, FILAMENT_PRICE, FILAMENT_COST, qty, ean)
        for name, ipn, qty, ean in [
            ("PLA Matt Black", "FIL-PLA-MBLK", 3, "8720847069139"),
            ("PLA Matt Mustard Yellow", "FIL-PLA-MYEL", 1, "8720847069191"),
            ("PLA Dark Blue", "FIL-PLA-DBLU", 3, "8720847069214"),
            ("PLA Forest Green", "FIL-PLA-FGRN", 2, "8720847069238"),
            ("PLA Matt Autumn Red", "FIL-PLA-MRED", 2, "8720847069252"),
            ("PLA Medium Grey", "FIL-PLA-MGRY", 2, "8720847069276"),
            ("PLA White", "FIL-PLA-WHT", 3, "8720847069290"),
            ("rTPU 95A Traffic White", "FIL-TPU95-WHT", 2, "8720847069702"),
            ("rTPU 90A Traffic White", "FIL-TPU90-WHT", 1, "8720847069726"),
            ("rTPU 90A Traffic Black", "FIL-TPU90-BLK", 1, "8720847069733"),
            ("rTPU 95A Traffic Black", "FIL-TPU95-BLK", 2, "8720847069757"),
        ]
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


def link_barcode(part, barcode):
    """Attach the box EAN to the part. InvenTree refuses a barcode that is
    already linked, which on a rerun just means it's done."""
    r = S.post(f"{URL}/api/barcode/link/", json={"barcode": barcode, "part": part["pk"]}, timeout=15)
    if r.ok:
        print(f"    barcode {barcode}")
    elif part.get("barcode_hash"):
        pass
    else:
        print(f"    barcode {barcode} NOT linked: {r.status_code} {r.text[:120]}")


def upload_image(part):
    path = os.path.join(IMAGES, f"{part['IPN']}.jpg")
    if part.get("image") or not os.path.exists(path):
        return
    with open(path, "rb") as f:
        r = S.patch(f"{URL}/api/part/{part['pk']}/", files={"image": (os.path.basename(path), f, "image/jpeg")}, timeout=30)
    print(f"    image {'ok' if r.ok else f'failed: {r.status_code}'}")


def main():
    for cat_name, loc_name, parts in ITEMS:
        cat = find_or_create("part/category/", cat_name)
        loc = find_or_create("stock/location/", loc_name)
        existing = {p["IPN"]: p for p in get("part/", category=cat) if p.get("IPN")}
        for name, ipn, price, cost, qty, barcode in parts:
            part = existing.get(ipn)
            if part:
                print(f"  exists: {name} ({ipn})")
            else:
                part = post("part/", {
                    "name": name, "IPN": ipn, "category": cat, "active": True,
                    "salable": True, "purchaseable": True, "component": False,
                    "minimum_stock": 0, "description": name,
                })
                post("part/sale-price/", {"part": part["pk"], "quantity": 1, "price": price, "price_currency": "EUR"})
                stock = {"part": part["pk"], "location": loc, "quantity": qty}
                if cost is not None:
                    stock.update(purchase_price=cost, purchase_price_currency="EUR")
                post("stock/", stock)
                print(f"  created: {name} ({ipn}) €{price:.2f}, stock {qty}")
            if barcode:
                link_barcode(part, barcode)
            upload_image(part)


if __name__ == "__main__":
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    main()
