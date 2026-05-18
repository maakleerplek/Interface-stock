import os
import sys
import time
import json
import requests
from enum import Enum
from dotenv import load_dotenv

# Try to import evdev for direct hardware access
try:
    import evdev
    from evdev import ecodes
    HAS_EVDEV = True
except ImportError:
    HAS_EVDEV = False

# Load InvenTree credentials
load_dotenv()
INVENTREE_URL = os.getenv("INVENTREE_URL", "https://10.72.3.68:8443")
INVENTREE_TOKEN = os.getenv("INVENTREE_TOKEN")
HTL_NAME = os.getenv("VITE_PAYMENT_NAME") or os.getenv("HTL_NAME", "HTL Makerspace")
HTL_CODE = os.getenv("HTL_CODE", "HTL001")
HTL_IBAN = os.getenv("VITE_PAYMENT_IBAN") or os.getenv("HTL_IBAN", "")
TV_PRESENTATION_URL = os.getenv("TV_PRESENTATION_URL", "")

print(f"DEBUG: Startup - URL: {INVENTREE_URL}")
print(f"DEBUG: Startup - Token: {'SET' if INVENTREE_TOKEN else 'MISSING'}")

# Disable warnings for self-signed certificates
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# Special barcodes for checkout confirmation and cancellation
CONFIRM_BARCODE = "CONFIRM"
CANCEL_BARCODE = "CANCEL"
REMOVE_BARCODE = "REMOVE"

# AZERTY Scan Code Map (for evdev)
# Removed duplicate key 12, kept it as '_' (often maps to '-' or '_' depending on scanner)
SCAN_CODES = {
    2: '1', 3: '2', 4: '3', 5: '4', 6: '5', 7: '6', 8: '7', 9: '8', 10: '9', 11: '0',
    12: '_', 13: '=', 
    16: 'Q', 17: 'W', 18: 'E', 19: 'R', 20: 'T', 21: 'Y', 22: 'U', 23: 'I', 24: 'O', 25: 'P',
    30: 'A', 31: 'S', 32: 'D', 33: 'F', 34: 'G', 35: 'H', 36: 'J', 37: 'K', 38: 'L',
    44: 'Z', 45: 'X', 46: 'C', 47: 'V', 48: 'B', 49: 'N', 50: 'M',
    51: ',', 52: '.', 53: '/', 57: ' ', 
}
if HAS_EVDEV:
    SCAN_CODES[ecodes.KEY_ENTER] = '\n'

# Fallback character map for manual terminal input
AZERTY_MAP = {
    '&': '1', 'é': '2', '"': '3', "'": '4', '(': '5',
    '§': '6', 'è': '7', '!': '8', 'ç': '9', 'à': '0',
    ')': '-', '_': '_', '=': '=', '+': '+',
}

def decode_manual_input(scanned_text):
    return "".join(AZERTY_MAP.get(c, c) for c in scanned_text)

# --- State Machine ---
class AppState(Enum):
    IDLE = "IDLE"                           # Ready for new customer
    SHOPPING = "SHOPPING"                   # Cart has items
    CANCEL_CONFIRM = "CANCEL_CONFIRM"       # Asked if they really want to cancel
    CHECKOUT_CONFIRM = "CHECKOUT_CONFIRM"   # Showing summary, waiting to lock in
    QR_DISPLAY = "QR_DISPLAY"               # Stock removed, showing payment info

# --- Barcode Cache ---

class BarcodeCache:
    def __init__(self, cache_file="barcode_cache.json"):
        self.cache_file = cache_file
        self.cache = {}
        self.load()

    def load(self):
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, "r") as f:
                    self.cache = json.load(f)
                print(f"DEBUG: Loaded {len(self.cache)} items from cache.")
            except Exception as e:
                print(f"DEBUG: Error loading cache: {e}")
                self.cache = {}

    def save(self):
        try:
            with open(self.cache_file, "w") as f:
                json.dump(self.cache, f)
        except Exception as e:
            print(f"DEBUG: Error saving cache: {e}")

    def get(self, barcode):
        return self.cache.get(barcode)

    def set(self, barcode, part_detail):
        if part_detail:
            # Cache essential details for display and payment
            essential = {
                "pk": part_detail.get("pk"),
                "name": part_detail.get("name"),
                "pricing_min": part_detail.get("pricing_min"),
                "pricing_max": part_detail.get("pricing_max"),
                "sell_price": part_detail.get("sell_price"),
                "thumbnail": part_detail.get("thumbnail"),
                "image": part_detail.get("image"),
                "category_detail": part_detail.get("category_detail"),
                "category": part_detail.get("category"),
                "_stock_item_pk": part_detail.get("_stock_item_pk"),
            }
            self.cache[barcode] = essential
            self.save()

BARCODE_CACHE = BarcodeCache()

# --- InvenTree API Functions ---

def fetch_part_details(part_id):
    if not part_id: return None
    headers = {"Authorization": f"Token {INVENTREE_TOKEN}"}
    url = f"{INVENTREE_URL}/api/part/{part_id}/"
    try:
        response = requests.get(url, headers=headers, timeout=5, verify=False)
        if response.status_code == 200:
            return response.json()
    except Exception as e:
        print(f"DEBUG: Error fetching part {part_id}: {e}")
    return None

def get_item_by_barcode(barcode):
    cached_part = BARCODE_CACHE.get(barcode)
    if cached_part:
        return cached_part

    if not INVENTREE_TOKEN:
        print("Error: INVENTREE_TOKEN not configured")
        return None

    headers = {"Authorization": f"Token {INVENTREE_TOKEN}"}
    
    # Attempt 1: Barcode API
    url = f"{INVENTREE_URL}/api/barcode/"
    try:
        response = requests.post(url, data={"barcode": barcode}, headers=headers, timeout=5, verify=False)
        if response.status_code == 200:
            res = response.json()
            
            def extract_from_obj(obj):
                if not isinstance(obj, dict): return None
                instance = obj.get("instance")
                if isinstance(instance, dict):
                    return instance.get("part_detail") or fetch_part_details(instance.get("part"))
                return obj.get("part_detail") or fetch_part_details(obj.get("part"))

            part = None
            if "stockitem" in res:
                s_obj = res["stockitem"]
                stock_item_pk = s_obj.get("pk") or (s_obj.get("instance", {}).get("pk") if isinstance(s_obj.get("instance"), dict) else None)
                part = extract_from_obj(s_obj)
                if part and stock_item_pk:
                    part["_stock_item_pk"] = stock_item_pk
            elif "part" in res:
                p_obj = res["part"]
                if isinstance(p_obj, dict):
                    if "instance" in p_obj: part = p_obj["instance"]
                    elif "name" in p_obj: part = p_obj
                if not part:
                    part = fetch_part_details(p_obj)
            
            if part:
                BARCODE_CACHE.set(barcode, part)
                return part
    except Exception:
        pass

    # Attempt 2 & 3: Search Part by EXACT Barcode or IPN
    try:
        variants = list(dict.fromkeys([barcode, barcode.lower(), barcode.upper()]))
        for v in variants:
            url = f"{INVENTREE_URL}/api/part/?barcode={v}&category_detail=true"
            response = requests.get(url, headers=headers, timeout=5, verify=False)
            if response.status_code == 200:
                items = response.json()
                results = items if isinstance(items, list) else items.get("results", [])
                for item in results:
                    if item.get("barcode", "").lower() == v.lower() or item.get("IPN", "").lower() == v.lower():
                        BARCODE_CACHE.set(barcode, item)
                        return item

        for v in variants:
            url = f"{INVENTREE_URL}/api/part/?IPN={v}&category_detail=true"
            response = requests.get(url, headers=headers, timeout=5, verify=False)
            if response.status_code == 200:
                items = response.json()
                results = items if isinstance(items, list) else items.get("results", [])
                for item in results:
                    if item.get("IPN", "").lower() == v.lower():
                        BARCODE_CACHE.set(barcode, item)
                        return item
    except Exception:
        pass

    # Attempt 4: Search StockItem by EXACT Barcode field
    try:
        url = f"{INVENTREE_URL}/api/stock/?barcode={barcode}&part_detail=true"
        response = requests.get(url, headers=headers, timeout=5, verify=False)
        if response.status_code == 200:
            items = response.json()
            results = items if isinstance(items, list) else items.get("results", [])
            for item in results:
                if item.get("barcode") == barcode:
                    stock_item_pk = item.get("pk")
                    part = item.get("part_detail") or fetch_part_details(item.get("part"))
                    if part:
                        part["_stock_item_pk"] = stock_item_pk
                        BARCODE_CACHE.set(barcode, part)
                        return part
    except Exception:
        pass

    return None

def extract_price(part_detail):
    if not part_detail: return 0.0
    if part_detail.get('pricing_max'):
        try: return float(part_detail['pricing_max'])
        except: pass
    if part_detail.get('pricing_min'):
        try: return float(part_detail['pricing_min'])
        except: pass
    if part_detail.get('sell_price'):
        try: return float(part_detail['sell_price'])
        except: pass
    return 0.0

def format_price(price):
    if price == 0.0: return "-"
    return f"€{price:.2f}"

def extract_category(part_detail):
    if not part_detail: return "uncategorized"
    cat_detail = part_detail.get('category_detail')
    if isinstance(cat_detail, dict) and cat_detail.get('name'):
        return cat_detail.get('name').lower()
    path = part_detail.get('category_path')
    if path and isinstance(path, str):
        return path.split('/')[-1].lower()
    if isinstance(part_detail.get('category_name'), str):
        return part_detail.get('category_name').lower()
    return "uncategorized"

def check_inventree_connection():
    if not INVENTREE_URL: return False
    try:
        url = f"{INVENTREE_URL}/api/"
        response = requests.get(url, timeout=3, verify=False)
        return response.status_code == 200
    except:
        return False

def find_stock_item_for_part(part_id):
    if not part_id: return None
    headers = {"Authorization": f"Token {INVENTREE_TOKEN}"}
    url = f"{INVENTREE_URL}/api/stock/?part={part_id}&in_stock=true"
    try:
        response = requests.get(url, headers=headers, timeout=5, verify=False)
        if response.status_code == 200:
            items = response.json()
            results = items if isinstance(items, list) else items.get("results", [])
            if results:
                results.sort(key=lambda x: float(x.get('quantity', 0)), reverse=True)
                return results[0].get('pk')
    except Exception:
        pass
    return None

def send_changelog_event(action, item_name, quantity, price=None):
    if not TV_PRESENTATION_URL:
        return
    try:
        payload = {"action": action, "source": "interface-stock", "item_name": item_name, "quantity": int(quantity)}
        if price is not None:
            payload["price"] = round(float(price), 2)
        requests.post(f"{TV_PRESENTATION_URL}/api/changelog", json=payload, timeout=3)
    except Exception:
        pass

def remove_stock_from_inventree(cart):
    if not INVENTREE_TOKEN:
        return False, "INVENTREE_TOKEN not configured"

    headers = {"Authorization": f"Token {INVENTREE_TOKEN}", "Content-Type": "application/json"}
    url = f"{INVENTREE_URL}/api/stock/remove/"
    
    items_to_remove = []
    for part_detail, quantity in cart.items:
        stock_item_pk = part_detail.get('_stock_item_pk') or find_stock_item_for_part(part_detail.get('pk'))
        if stock_item_pk:
            items_to_remove.append({"pk": stock_item_pk, "quantity": float(quantity)})

    if not items_to_remove:
        return False, "No stock items found to remove"

    payload = {"items": items_to_remove, "notes": f"Purchased via Interface-stock ({HTL_NAME})"}

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=10, verify=False)
        if response.status_code in [200, 201]:
            for part_detail, quantity in cart.items:
                unit_price = extract_price(part_detail)
                total_price = unit_price * quantity if unit_price else None
                send_changelog_event("checkout", part_detail.get("name", "Unknown"), quantity, total_price)
            return True, ""
        else:
            return False, f"API Error: {response.status_code}"
    except Exception as e:
        return False, f"Exception: {str(e)}"

# --- Shopping Cart Management ---

class ShoppingCart:
    def __init__(self):
        self.items = []

    def add_item(self, part_detail):
        pk = part_detail.get('pk')
        spk = part_detail.get('_stock_item_pk')
        for i, (item, qty) in enumerate(self.items):
            if item.get('pk') == pk and item.get('_stock_item_pk') == spk:
                self.items[i] = (item, qty + 1)
                return
        self.items.append((part_detail, 1))

    def remove_last_item(self):
        if not self.items:
            return None
        part, qty = self.items[-1]
        if qty > 1:
            self.items[-1] = (part, qty - 1)
        else:
            self.items.pop()
        return part
        
    def get_total(self):
        total = 0.0
        for part_detail, qty in self.items:
            total += extract_price(part_detail) * qty
        return total
    
    def get_categories(self):
        categories = set()
        for part_detail, _ in self.items:
            categories.add(extract_category(part_detail))
        return sorted(categories)
    
    def get_description(self):
        categories = self.get_categories()
        if not categories:
            return f"{HTL_NAME} - Purchase"
        return f"{HTL_NAME}: " + ",".join(categories)
    
    def clear(self):
        self.items = []
    
    def is_empty(self):
        return len(self.items) == 0

# --- Output Formatting ---

def generate_epc_qr_text(amount, description):
    epc_data = [
        "BCD", "002", "1", "SCT", "",
        HTL_NAME, HTL_IBAN, f"EUR{amount:.2f}", "", "", description
    ]
    return "\n".join(epc_data)

def render_terminal(state, cart, message=None, item_name=None, item_price=None):
    # Clear terminal (optional, depending on preference, sticking to append for logs)
    print("\n" + "="*40)
    
    if state == AppState.IDLE:
        print(f"--- {HTL_NAME} ---")
        if message: print(f"*** {message} ***")
        print("READY! Scan an item to begin.")
        
    elif state == AppState.SHOPPING:
        print("--- CART ---")
        if message: print(f"*** {message} ***")
        if item_name:
            print(f"Last Added: {item_name} ({format_price(item_price)})")
            print("-" * 20)
        
        for part, qty in cart.items:
            print(f"{qty}x {part.get('name', 'Unknown')} - {format_price(extract_price(part) * qty)}")
        print(f"TOTAL: {format_price(cart.get_total())}")
        print("Commands: [CONFIRM] to checkout | [REMOVE] to undo | [CANCEL] to start over")
        
    elif state == AppState.CANCEL_CONFIRM:
        print("!!! CANCEL TRANSACTION !!!")
        print(f"Cart has {len(cart.items)} items.")
        print("Scan [CANCEL] again to clear cart.")
        print("Scan any item to go back.")
        
    elif state == AppState.CHECKOUT_CONFIRM:
        print("--- CHECKOUT ---")
        if message: print(f"*** {message} ***")
        for part, qty in cart.items:
            print(f"{qty}x {part.get('name', 'Unknown')}")
        print("-" * 20)
        print(f"GRAND TOTAL: {format_price(cart.get_total())}")
        print("Scan [CONFIRM] again to finalize and remove stock.")
        print("Scan [CANCEL] or [REMOVE] to go back.")
        
    elif state == AppState.PROCESSING:
        print("Processing checkout...")
        
    elif state == AppState.QR_DISPLAY:
        print("--- PAYMENT SUCCESS ---")
        print("Stock successfully removed from InvenTree.")
        print(f"Total Due: {format_price(cart.get_total())}")
        print(f"Description: {cart.get_description()}")
        if HTL_IBAN:
            print(f"IBAN: {HTL_IBAN}")
        else:
            print("WARNING: Payment IBAN not configured!")
        if message: print(f"\n{message}")
        print("\nScan [CONFIRM], [CANCEL], or [REMOVE] to start a new transaction.")

    print("="*40)

# --- Main App Logic ---

def handle_barcode(state, barcode, cart):
    """Returns (new_state, message, item_name, item_price)"""
    bc = barcode.upper()
    
    # 1. IDLE STATE
    if state == AppState.IDLE:
        if bc in (CONFIRM_BARCODE, CANCEL_BARCODE, REMOVE_BARCODE):
            return AppState.IDLE, "Cart is empty.", None, None
            
        part = get_item_by_barcode(barcode)
        if part:
            cart.add_item(part)
            return AppState.SHOPPING, None, part.get('name'), extract_price(part)
        return AppState.IDLE, f"Unknown Barcode: {barcode}", None, None

    # 2. QR DISPLAY STATE (Locked, waiting for clear)
    if state == AppState.QR_DISPLAY:
        if bc in (CONFIRM_BARCODE, CANCEL_BARCODE, REMOVE_BARCODE):
            cart.clear()
            return AppState.IDLE, "Transaction Complete. Ready.", None, None
        # Ignore normal item scans completely so they aren't added to old cart
        return AppState.QR_DISPLAY, "Please finish payment. Scan CONFIRM to start new transaction.", None, None

    # 3. SHOPPING STATE
    if state == AppState.SHOPPING:
        if bc == CANCEL_BARCODE:
            return AppState.CANCEL_CONFIRM, None, None, None
            
        elif bc == CONFIRM_BARCODE:
            if not check_inventree_connection():
                return AppState.SHOPPING, "ERROR: InvenTree offline. Cannot checkout.", None, None
            return AppState.CHECKOUT_CONFIRM, None, None, None
            
        elif bc == REMOVE_BARCODE:
            removed = cart.remove_last_item()
            if cart.is_empty():
                return AppState.IDLE, "Cart is now empty.", None, None
            msg = f"Removed {removed.get('name')}" if removed else "Nothing to remove."
            return AppState.SHOPPING, msg, None, None
            
        else:
            part = get_item_by_barcode(barcode)
            if part:
                cart.add_item(part)
                return AppState.SHOPPING, None, part.get('name'), extract_price(part)
            return AppState.SHOPPING, f"Unknown Barcode: {barcode}", None, None

    # 4. CANCEL CONFIRM STATE
    if state == AppState.CANCEL_CONFIRM:
        if bc == CANCEL_BARCODE:
            cart.clear()
            return AppState.IDLE, "Transaction cancelled.", None, None
        elif bc in (CONFIRM_BARCODE, REMOVE_BARCODE):
            return AppState.SHOPPING, "Cancellation aborted.", None, None
        else:
            # Treat item scan as "abort cancel, add item"
            part = get_item_by_barcode(barcode)
            if part:
                cart.add_item(part)
                return AppState.SHOPPING, "Cancellation aborted.", part.get('name'), extract_price(part)
            return AppState.SHOPPING, f"Unknown Barcode: {barcode}", None, None

    # 5. CHECKOUT CONFIRM STATE
    if state == AppState.CHECKOUT_CONFIRM:
        if bc == CONFIRM_BARCODE:
            # Execute checkout
            render_terminal(AppState.PROCESSING, cart)
            success, err_msg = remove_stock_from_inventree(cart)
            if success:
                qr_text = generate_epc_qr_text(cart.get_total(), cart.get_description())
                # Just store qr text in message for display
                return AppState.QR_DISPLAY, "QR Code Data:\n" + qr_text, None, None
            else:
                return AppState.SHOPPING, f"Checkout Failed: {err_msg}", None, None
                
        elif bc in (CANCEL_BARCODE, REMOVE_BARCODE):
            return AppState.SHOPPING, "Checkout aborted.", None, None
            
        else:
            part = get_item_by_barcode(barcode)
            if part:
                cart.add_item(part)
                return AppState.SHOPPING, "Added item. Please confirm checkout again.", part.get('name'), extract_price(part)
            return AppState.CHECKOUT_CONFIRM, f"Unknown Barcode: {barcode}", None, None

    return state, "Unexpected State", None, None

def find_scanner():
    if not HAS_EVDEV: return None
    try:
        for path in evdev.list_devices():
            dev = evdev.InputDevice(path)
            if any(x in dev.name.lower() for x in ["usbscn", "scanner", "keyboard", "barcode", "hid"]):
                return dev
    except: pass
    return None

import select

def read_scancode(device):
    barcode = ""
    try:
        while True:
            r, w, x = select.select([device], [], [], 1.0)
            if not r:
                return "" # Timeout, let main loop check things
            
            for event in device.read():
                if event.type == ecodes.EV_KEY:
                    data = evdev.categorize(event)
                    if data.keystate == 1: # Key Down
                        if data.scancode == ecodes.KEY_ENTER:
                            res = barcode.strip()
                            barcode = ""
                            if res: return res
                        else:
                            char = SCAN_CODES.get(data.scancode)
                            if char is not None:
                                barcode += char
    except Exception as e:
        print(f"Scanner read error: {e}")
        return None # Indicate device failure

def main():
    scanner = find_scanner()
    print("\n--- InvenTree Shopping System (Terminal Mode) ---")
    print(f"Makerspace: {HTL_NAME}")
    if scanner: print(f"Hardware Scanner: {scanner.name}")
    else: print("Mode: Terminal Input")
    
    cart = ShoppingCart()
    state = AppState.IDLE
    last_interaction = time.time()
    last_scan_time = 0
    TIMEOUT_SECONDS = 300 # 5 minutes for general timeout
    
    render_terminal(state, cart)
    
    try:
        while True:
            # 1. Timeout Check
            # We enforce timeout in any state except IDLE and QR_DISPLAY
            if state not in (AppState.IDLE, AppState.QR_DISPLAY):
                if time.time() - last_interaction > TIMEOUT_SECONDS:
                    print("\nInactivity timeout. Clearing cart.")
                    cart.clear()
                    state = AppState.IDLE
                    render_terminal(state, cart, "Timeout: Cart Cleared")
                    last_interaction = time.time()

            # 2. Get Input
            if scanner:
                barcode = read_scancode(scanner)
                if barcode is None:
                    # Scanner disconnected or error. Try to reconnect.
                    print("Scanner lost. Attempting to reconnect...")
                    time.sleep(2)
                    scanner = find_scanner()
                    if scanner:
                        print(f"Reconnected to {scanner.name}")
                    continue
            else:
                try:
                    raw = input("Scan: ").strip()
                    barcode = decode_manual_input(raw) if raw else ""
                except EOFError:
                    break

            if not barcode:
                continue

            # 3. Debounce
            current_time = time.time()
            if (current_time - last_scan_time) < 0.2:
                continue
                
            last_interaction = current_time
            last_scan_time = current_time
            print(f"Scanned: {barcode}")
            
            # 4. State Transition
            new_state, msg, item_name, item_price = handle_barcode(state, barcode, cart)
            state = new_state
            
            # 5. Render
            render_terminal(state, cart, msg, item_name, item_price)

    except KeyboardInterrupt:
        print("\nExiting...")

if __name__ == "__main__":
    main()
