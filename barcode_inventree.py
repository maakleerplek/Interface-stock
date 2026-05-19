import os
import sys
import time
import json
import glob
import atexit
import textwrap
import requests
import threading
import qrcode
import queue
from io import BytesIO
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache
from PIL import Image, ImageDraw, ImageFont
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

# Persistent session with a connection pool — avoids TLS re-handshake on every request.
# On Pi 3, a full TLS handshake to a self-signed server costs ~300-500 ms.
from requests.adapters import HTTPAdapter
_adapter = HTTPAdapter(
    pool_connections=1,   # one host
    pool_maxsize=4,       # up to 4 parallel requests share the same connection
    max_retries=0,
)
API_SESSION = requests.Session()
if INVENTREE_TOKEN:
    API_SESSION.headers.update({
        "Authorization": f"Token {INVENTREE_TOKEN}",
        "Connection": "keep-alive",
    })
API_SESSION.verify = False  # Ignore self-signed cert warnings
API_SESSION.mount("https://", _adapter)
API_SESSION.mount("http://", _adapter)

# Background LCD rendering queue - maxsize=1 so we always render the LATEST frame
# We drain it before pushing so stale frames never block responsiveness
LCD_QUEUE = queue.Queue()

# Special barcodes for checkout confirmation and cancellation
CONFIRM_BARCODE = "CONFIRM"
CANCEL_BARCODE = "CANCEL"
REMOVE_BARCODE = "REMOVE"

# AZERTY Scan Code Map (for evdev)
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
    IDLE = "IDLE"
    SHOPPING = "SHOPPING"
    CANCEL_CONFIRM = "CANCEL_CONFIRM"
    CHECKOUT_CONFIRM = "CHECKOUT_CONFIRM"
    PROCESSING = "PROCESSING"
    QR_DISPLAY = "QR_DISPLAY"

# --- BRUTALISM DESIGN SYSTEM ---
COL_BG      = (10, 10, 10)       # Near-black background
COL_FG      = (240, 240, 240)    # Primary text
COL_ACCENT  = (255, 60, 20)      # Vermillion red
COL_ACCENT2 = (255, 220, 0)      # Yellow
COL_MUTED   = (100, 100, 100)    # Secondary text
COL_BLOCK   = (30, 30, 30)       # Panel backgrounds
COL_SUCCESS = (0, 200, 80)       # Confirmation, QR
COL_BORDER  = (240, 240, 240)    # Thick white borders
COL_DANGER  = (180, 30, 20)      # Cancel / error

L_WIDTH, L_HEIGHT = 320, 240
BORDER_W = 3

_FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
_HAS_FONT = os.path.exists(_FONT_PATH)
FONT_XL = ImageFont.truetype(_FONT_PATH, 26) if _HAS_FONT else ImageFont.load_default()
FONT_LG = ImageFont.truetype(_FONT_PATH, 18) if _HAS_FONT else ImageFont.load_default()
FONT_MD = ImageFont.truetype(_FONT_PATH, 14) if _HAS_FONT else ImageFont.load_default()
FONT_SM = ImageFont.truetype(_FONT_PATH, 11) if _HAS_FONT else ImageFont.load_default()

# Module-level reusable frame buffer — allocated once, cleared before each render.
# lcd_worker is single-threaded so concurrent access is not a concern.
# Saves ~230 KB allocation + GC pressure per render on Pi 3.
_LCD_FRAME = Image.new('RGB', (L_WIDTH, L_HEIGHT), COL_BG)
_LCD_DRAW  = ImageDraw.Draw(_LCD_FRAME)

def _new_frame(bg=None):
    """Clear and return the shared frame buffer instead of allocating a new Image."""
    _LCD_DRAW.rectangle([0, 0, L_WIDTH - 1, L_HEIGHT - 1], fill=bg or COL_BG)
    return _LCD_FRAME, _LCD_DRAW

# Persistent executor for parallel fallback barcode lookups.
# Creating ThreadPoolExecutor inside a with-block on every cache-miss
# spawns 4 threads per scan — expensive on Pi 3. Reuse one instead.
_FALLBACK_EXECUTOR = ThreadPoolExecutor(max_workers=4)

@lru_cache(maxsize=256)
def _wrap(text: str, width: int) -> tuple:
    """LRU-cached textwrap — same item names are wrapped repeatedly per render."""
    return tuple(textwrap.wrap(text, width=width))

def _show(disp, image):
    # Pass the 320×240 image directly — ShowImage()'s MADCTL branch already
    # handles landscape orientation (0x78) when it sees a (320, 240) image.
    # Eliminates image.rotate(90, expand=True) which allocated a full 230 KB
    # PIL Image copy on every render.
    if disp:
        disp.ShowImage(image)

def _border_rect(draw, box, fill=None, border_color=None, width=BORDER_W):
    if fill: draw.rectangle(box, fill=fill)
    draw.rectangle(box, outline=border_color or COL_BORDER, width=width)

def _center_text(draw, y, text, font, fill=COL_FG, area_width=L_WIDTH):
    w = draw.textlength(text, font=font)
    draw.text(((area_width - w) / 2, y), text, font=font, fill=fill)

# --- 1. LCD Configuration ---
def find_lib_path():
    for root_dir in ['.', 'lcd_assets', 'LCD_Module_code']:
        if not os.path.exists(root_dir): continue
        for config_name in ['lcdconfig.py', 'tp_config.py']:
            search_pattern = os.path.join(os.getcwd(), root_dir, '**', config_name)
            matches = glob.glob(search_pattern, recursive=True)
            if matches:
                lib_dir = os.path.dirname(matches[0])
                if os.path.basename(lib_dir) == 'lib': return os.path.dirname(lib_dir)
                return lib_dir
    return None

lib_path = find_lib_path()
HAS_LCD = False
if lib_path and os.path.exists(lib_path):
    sys.path.append(lib_path)
    try:
        from lib import lcdconfig as config
        try:
            from lib import LCD_2inch4 as LCD
        except ImportError:
            from lib import LCD_2in4 as LCD
        HAS_LCD = True
    except ImportError:
        pass

# --- Barcode Cache ---
class BarcodeCache:
    """Disk-backed barcode→part_detail cache with debounced SD writes.

    Writing to the SD card on every scan is slow and wears the card.
    Instead we mark the cache dirty and flush at most every 10 seconds,
    or immediately on process exit via atexit.
    """
    _DEBOUNCE_S = 10  # minimum seconds between disk writes

    def __init__(self, cache_file="barcode_cache.json"):
        self.cache_file = cache_file
        self.cache = {}
        self._dirty = False
        self._last_save = 0.0
        self._lock = threading.Lock()
        self.load()
        atexit.register(self.flush)  # always flush on clean exit

    def load(self):
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, "r") as f:
                    self.cache = json.load(f)
            except Exception:
                self.cache = {}

    def flush(self):
        """Write to disk immediately (called at exit or when debounce expires)."""
        with self._lock:
            if not self._dirty: return
            try:
                with open(self.cache_file, "w") as f:
                    json.dump(self.cache, f)
                self._dirty = False
                self._last_save = time.time()
            except Exception: pass

    def save(self):
        """Debounced save — flushes only if debounce period has passed."""
        self._dirty = True
        if time.time() - self._last_save >= self._DEBOUNCE_S:
            self.flush()

    def get(self, barcode): return self.cache.get(barcode)

    def set(self, barcode, part_detail):
        if part_detail:
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

# In-memory cache of already-resized 80×80 thumbnails keyed by part pk.
# Avoids repeated Pillow thumbnail() calls on every LCD refresh — important on Pi 3.
_THUMBNAIL_CACHE: dict = {}

# --- InvenTree API Functions ---
def fetch_part_details(part_id):
    if not part_id: return None
    url = f"{INVENTREE_URL}/api/part/{part_id}/"
    try:
        response = API_SESSION.get(url, timeout=5)
        if response.status_code == 200: return response.json()
    except Exception: pass
    return None

def get_item_by_barcode(barcode):
    cached_part = BARCODE_CACHE.get(barcode)
    if cached_part: return cached_part

    if not INVENTREE_TOKEN: return None
    
    url = f"{INVENTREE_URL}/api/barcode/"
    try:
        response = API_SESSION.post(url, data={"barcode": barcode}, timeout=5)
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
                if part and stock_item_pk: part["_stock_item_pk"] = stock_item_pk
            elif "part" in res:
                p_obj = res["part"]
                if isinstance(p_obj, dict):
                    if "instance" in p_obj: part = p_obj["instance"]
                    elif "name" in p_obj: part = p_obj
                if not part: part = fetch_part_details(p_obj)
            
            if part:
                if not part.get('_stock_item_pk'):
                    part['_stock_item_pk'] = find_stock_item_for_part(part.get('pk'))
                BARCODE_CACHE.set(barcode, part)
                return part
    except Exception: pass

    # --- Parallel fallback lookups ---
    # Fire all fallback endpoints simultaneously so the worst-case miss time is
    # one round-trip instead of four. Uses a thread pool of 4 workers (Pi 3 safe).
    variants = list(dict.fromkeys([barcode, barcode.lower(), barcode.upper()]))

    def _try_part_barcode(v):
        try:
            r = API_SESSION.get(f"{INVENTREE_URL}/api/part/?barcode={v}&category_detail=true", timeout=5)
            if r.status_code != 200: return None
            data = r.json()  # parse once — avoids double JSON decode
            results = data if isinstance(data, list) else data.get("results", [])
            for item in results:
                if item.get("barcode", "").lower() == v.lower() or item.get("IPN", "").lower() == v.lower():
                    return item
        except Exception: pass
        return None

    def _try_part_ipn(v):
        try:
            r = API_SESSION.get(f"{INVENTREE_URL}/api/part/?IPN={v}&category_detail=true", timeout=5)
            if r.status_code != 200: return None
            data = r.json()  # parse once
            results = data if isinstance(data, list) else data.get("results", [])
            for item in results:
                if item.get("IPN", "").lower() == v.lower():
                    return item
        except Exception: pass
        return None

    def _try_stock_barcode():
        try:
            r = API_SESSION.get(f"{INVENTREE_URL}/api/stock/?barcode={barcode}&part_detail=true", timeout=5)
            if r.status_code != 200: return None
            results = r.json() if isinstance(r.json(), list) else r.json().get("results", [])
            for item in results:
                if item.get("barcode") == barcode:
                    stock_item_pk = item.get("pk")
                    part = item.get("part_detail") or fetch_part_details(item.get("part"))
                    if part:
                        part["_stock_item_pk"] = stock_item_pk
                        return part
        except Exception: pass
        return None

    tasks = []
    # Use the module-level persistent executor — avoids spawning 4 threads per
    # cache-miss scan (thread creation costs ~5–20 ms each on Pi 3).
    for v in variants:
        tasks.append(_FALLBACK_EXECUTOR.submit(_try_part_barcode, v))
        tasks.append(_FALLBACK_EXECUTOR.submit(_try_part_ipn, v))
    tasks.append(_FALLBACK_EXECUTOR.submit(_try_stock_barcode))
    for future in as_completed(tasks):
        result = future.result()
        if result:
            # Cancel remaining futures (best-effort)
            for f in tasks: f.cancel()
            if not result.get('_stock_item_pk'):
                result['_stock_item_pk'] = find_stock_item_for_part(result.get('pk'))
            BARCODE_CACHE.set(barcode, result)
            return result

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
    if isinstance(cat_detail, dict) and cat_detail.get('name'): return cat_detail.get('name').lower()
    path = part_detail.get('category_path')
    if path and isinstance(path, str): return path.split('/')[-1].lower()
    if isinstance(part_detail.get('category_name'), str): return part_detail.get('category_name').lower()
    return "uncategorized"

def get_image(part_detail, size=(80, 80)):
    img_path = part_detail.get('thumbnail') or part_detail.get('image')
    if not img_path: return None

    part_id = part_detail.get('pk', 'unknown')
    cache_key = (part_id, size)

    # Return already-resized image from RAM — free on Pi 3
    if cache_key in _THUMBNAIL_CACHE:
        return _THUMBNAIL_CACHE[cache_key]

    os.makedirs("image_cache", exist_ok=True)
    ext = os.path.splitext(img_path)[1] or ".png"
    local_filename = f"image_cache/part_{part_id}{ext}"

    img = None
    if os.path.exists(local_filename):
        try: img = Image.open(local_filename)
        except Exception: pass

    if img is None:
        img_url = f"{INVENTREE_URL}{img_path}" if img_path.startswith('/') else img_path
        try:
            response = API_SESSION.get(img_url, timeout=5)
            if response.status_code == 200:
                with open(local_filename, "wb") as f: f.write(response.content)
                img = Image.open(BytesIO(response.content))
        except Exception: pass

    if img is None: return None

    img.thumbnail(size)
    # Cap cache at 64 entries to avoid unbounded RAM on Pi 3
    if len(_THUMBNAIL_CACHE) >= 64:
        _THUMBNAIL_CACHE.pop(next(iter(_THUMBNAIL_CACHE)))
    _THUMBNAIL_CACHE[cache_key] = img
    return img

def find_stock_item_for_part(part_id):
    if not part_id: return None
    url = f"{INVENTREE_URL}/api/stock/?part={part_id}&in_stock=true"
    try:
        response = API_SESSION.get(url, timeout=5)
        if response.status_code == 200:
            items = response.json()
            results = items if isinstance(items, list) else items.get("results", [])
            if results:
                results.sort(key=lambda x: float(x.get('quantity', 0)), reverse=True)
                return results[0].get('pk')
    except Exception: pass
    return None

def send_changelog_event(action, item_name, quantity, price=None):
    if not TV_PRESENTATION_URL: return
    def _send():
        try:
            payload = {"action": action, "source": "interface-stock", "item_name": item_name, "quantity": int(quantity)}
            if price is not None: payload["price"] = round(float(price), 2)
            # Use API_SESSION (keep-alive, connection pool) instead of bare requests.post
            API_SESSION.post(f"{TV_PRESENTATION_URL}/api/changelog", json=payload, timeout=3)
        except Exception: pass
    threading.Thread(target=_send, daemon=True).start()

def remove_stock_from_inventree(cart):
    if not INVENTREE_TOKEN: return False, "INVENTREE_TOKEN not configured"

    headers = {"Content-Type": "application/json"}
    url = f"{INVENTREE_URL}/api/stock/remove/"
    
    items_to_remove = []
    for part_detail, quantity in cart.items:
        stock_item_pk = part_detail.get('_stock_item_pk') or find_stock_item_for_part(part_detail.get('pk'))
        if stock_item_pk: items_to_remove.append({"pk": stock_item_pk, "quantity": float(quantity)})

    if not items_to_remove: return False, "No stock items found to remove"

    payload = {"items": items_to_remove, "notes": f"Purchased via Interface-stock ({HTL_NAME})"}

    try:
        response = API_SESSION.post(url, json=payload, headers=headers, timeout=10)
        if response.status_code in [200, 201]:
            for part_detail, quantity in cart.items:
                unit_price = extract_price(part_detail)
                total_price = unit_price * quantity if unit_price else None
                send_changelog_event("checkout", part_detail.get("name", "Unknown"), quantity, total_price)
            return True, ""
        else: return False, f"API Error: {response.status_code}"
    except Exception as e: return False, f"Exception: {str(e)}"

# --- Shopping Cart Management ---
class ShoppingCart:
    def __init__(self): self.items = []

    def add_item(self, part_detail):
        pk = part_detail.get('pk')
        spk = part_detail.get('_stock_item_pk')
        for i, (item, qty) in enumerate(self.items):
            if item.get('pk') == pk and item.get('_stock_item_pk') == spk:
                self.items[i] = (item, qty + 1)
                return
        self.items.append((part_detail, 1))

    def remove_last_item(self):
        if not self.items: return None
        part, qty = self.items[-1]
        if qty > 1: self.items[-1] = (part, qty - 1)
        else: self.items.pop()
        return part
        
    def get_total(self): return sum(extract_price(p) * q for p, q in self.items)
    def get_categories(self): return sorted(set(extract_category(p) for p, _ in self.items))
    def get_description(self):
        cats = self.get_categories()
        return f"{HTL_NAME}: " + ",".join(cats) if cats else f"{HTL_NAME} - Purchase"
    def clear(self): self.items = []
    def is_empty(self): return len(self.items) == 0

class CartSnapshot:
    def __init__(self, cart):
        self.items = list(cart.items)
        self._total = cart.get_total()
        self._cats = cart.get_categories()
        self._desc = cart.get_description()
        self._empty = cart.is_empty()
    def get_total(self): return self._total
    def get_categories(self): return self._cats
    def get_description(self): return self._desc
    def is_empty(self): return self._empty

# --- LCD / Output Formatting ---
def show_message_screen(disp, title, message, color=None):
    if not disp: return
    image, draw = _new_frame()
    accent = color or COL_ACCENT
    _border_rect(draw, [0, 0, L_WIDTH - 1, L_HEIGHT - 1])
    draw.rectangle([BORDER_W, BORDER_W, L_WIDTH - BORDER_W - 1, 50], fill=accent)
    _center_text(draw, 14, title.upper(), FONT_LG, fill=COL_FG)
    draw.rectangle([BORDER_W, 53, L_WIDTH - BORDER_W - 1, 55], fill=COL_BORDER)
    
    lines = textwrap.fill(message, width=28).split('\n')
    y = 80
    for line in lines:
        _center_text(draw, y, line.upper(), FONT_MD, fill=COL_FG)
        y += 22
    _show(disp, image)

def show_warning_screen(disp, title, message):
    show_message_screen(disp, title, message, color=COL_DANGER)

def show_idle_screen(disp):
    if not disp: return
    image, draw = _new_frame()
    _border_rect(draw, [0, 0, L_WIDTH - 1, L_HEIGHT - 1])
    _border_rect(draw, [20, 40, L_WIDTH - 21, L_HEIGHT - 60], fill=COL_BLOCK)
    _center_text(draw, 65, "SCAN", FONT_XL, fill=COL_ACCENT)
    draw.rectangle([50, 105, L_WIDTH - 50, 107], fill=COL_BORDER)
    _center_text(draw, 120, "READY", FONT_LG, fill=COL_MUTED)
    _center_text(draw, L_HEIGHT - 45, HTL_NAME[:30].upper(), FONT_SM, fill=COL_MUTED)
    _show(disp, image)

def show_item_on_lcd(disp, part_detail, cart):
    if not disp: return
    image, draw = _new_frame()
    SPLIT_X = 205
    _border_rect(draw, [0, 0, SPLIT_X - 1, L_HEIGHT - 1])

    if part_detail:
        name = part_detail.get('name', 'UNKNOWN')
        price = extract_price(part_detail)
        draw.rectangle([BORDER_W, BORDER_W, SPLIT_X - BORDER_W - 1, 32], fill=COL_ACCENT)
        draw.text((8, 8), "SCANNED", font=FONT_LG, fill=COL_FG)
        img = get_image(part_detail, size=(80, 80))  # returns cached thumbnail
        if img:
            image.paste(img, (10, 42))
        else:
            _border_rect(draw, [10, 42, 90, 122], fill=COL_BLOCK)
            draw.text((30, 72), "—", font=FONT_LG, fill=COL_MUTED)

        lines = list(_wrap(name.upper(), 16))  # lru_cache: same name wrapped once
        y_text = 44 if len(lines) > 1 else 50
        for line in lines[:2]:
            draw.text((100, y_text), line, font=FONT_MD, fill=COL_FG)
            y_text += 20
        draw.rectangle([BORDER_W, 130, SPLIT_X - BORDER_W - 1, 132], fill=COL_BORDER)
        draw.text((10, 140), "PRICE", font=FONT_SM, fill=COL_MUTED)
        draw.text((10, 154), format_price(price), font=FONT_XL, fill=COL_ACCENT)
    else:
        _center_text(draw, 70, "NOT", FONT_XL, fill=COL_ACCENT, area_width=SPLIT_X)
        _center_text(draw, 105, "FOUND", FONT_XL, fill=COL_ACCENT, area_width=SPLIT_X)

    _border_rect(draw, [SPLIT_X, 0, L_WIDTH - 1, L_HEIGHT - 1], fill=COL_BLOCK)
    draw.rectangle([SPLIT_X + BORDER_W, BORDER_W, L_WIDTH - BORDER_W - 1, 28], fill=COL_BORDER)
    draw.text((SPLIT_X + 10, 6), "CART", font=FONT_MD, fill=COL_BG)

    if cart.is_empty():
        draw.text((SPLIT_X + 20, 100), "EMPTY", font=FONT_SM, fill=COL_MUTED)
    else:
        y = 36
        for part, qty in cart.items[:4]:
            full_name = part.get('name', '?').upper()
            draw.text((SPLIT_X + 6, y), f"{qty}×", font=FONT_MD, fill=COL_ACCENT)
            lines = list(_wrap(full_name, 14))  # lru_cache hit on repeat renders
            if lines:
                draw.text((SPLIT_X + 30, y), lines[0], font=FONT_SM, fill=COL_FG)
                if len(lines) > 1:
                    draw.text((SPLIT_X + 30, y + 14), lines[1], font=FONT_SM, fill=COL_FG)
            y += 40
        if len(cart.items) > 4:
            draw.text((SPLIT_X + 6, y), f"+{len(cart.items)-4}", font=FONT_SM, fill=COL_MUTED)

        draw.rectangle([SPLIT_X + BORDER_W, L_HEIGHT - 40, L_WIDTH - BORDER_W - 1, L_HEIGHT - BORDER_W - 1], fill=COL_BG)
        draw.rectangle([SPLIT_X, L_HEIGHT - 42, L_WIDTH - 1, L_HEIGHT - 42 + 2], fill=COL_BORDER)
        draw.text((SPLIT_X + 6, L_HEIGHT - 36), format_price(cart.get_total()), font=FONT_LG, fill=COL_ACCENT2)
    _show(disp, image)

def show_confirmation_screen(disp, cart):
    if not disp: return
    image, draw = _new_frame()
    _border_rect(draw, [0, 0, L_WIDTH - 1, L_HEIGHT - 1])
    draw.rectangle([BORDER_W, BORDER_W, L_WIDTH - BORDER_W - 1, 34], fill=COL_ACCENT2)
    _center_text(draw, 8, "CHECKOUT", FONT_LG, fill=COL_BG)
    draw.rectangle([BORDER_W, 37, L_WIDTH - BORDER_W - 1, 39], fill=COL_BORDER)

    y = 46
    for part, qty in cart.items[:4]:
        full_name = part.get('name', 'ITEM').upper()
        price = extract_price(part) * qty
        lines = list(_wrap(full_name, 24))  # lru_cache: cached per item name
        draw.text((10, y), f"{qty}×", font=FONT_MD, fill=COL_ACCENT)
        draw.text((38, y + 2), lines[0], font=FONT_SM, fill=COL_FG)
        price_str = format_price(price)
        pw = draw.textlength(price_str, font=FONT_SM)
        draw.text((L_WIDTH - pw - 10, y + 2), price_str, font=FONT_SM, fill=COL_ACCENT)
        if len(lines) > 1:
            draw.text((38, y + 16), lines[1], font=FONT_SM, fill=COL_FG)
            y += 34
        else: y += 26

    if len(cart.items) > 4:
        draw.text((10, y), f"+ {len(cart.items) - 4} MORE...", font=FONT_SM, fill=COL_MUTED)

    draw.rectangle([BORDER_W, L_HEIGHT - 80, L_WIDTH - BORDER_W - 1, L_HEIGHT - 43], fill=COL_BLOCK)
    draw.rectangle([BORDER_W, L_HEIGHT - 82, L_WIDTH - BORDER_W - 1, L_HEIGHT - 80], fill=COL_BORDER)
    draw.text((10, L_HEIGHT - 78), "TOTAL", font=FONT_LG, fill=COL_FG)
    total_str = format_price(cart.get_total())
    tw = draw.textlength(total_str, font=FONT_XL)
    draw.text((L_WIDTH - tw - 10, L_HEIGHT - 79), total_str, font=FONT_XL, fill=COL_ACCENT2)

    _center_text(draw, L_HEIGHT - 55, "SCANNING CONFIRM WILL REMOVE FROM STOCK", FONT_SM, fill=COL_ACCENT)
    draw.rectangle([BORDER_W, L_HEIGHT - 40, L_WIDTH - BORDER_W - 1, L_HEIGHT - BORDER_W - 1], fill=COL_ACCENT)
    _center_text(draw, L_HEIGHT - 34, "SCAN CONFIRM TO FINALIZE", FONT_SM, fill=COL_FG)
    _show(disp, image)

def generate_wero_qr(amount, description):
    epc_data = [
        "BCD", "002", "1", "SCT", "",
        HTL_NAME, HTL_IBAN, f"EUR{amount:.2f}", "", "", description
    ]
    qr_content = "\n".join(epc_data)
    qr = qrcode.QRCode(version=1, box_size=4, border=2)
    qr.add_data(qr_content)
    qr.make(fit=True)
    return qr.make_image(fill_color="black", back_color="white")

def show_payment_qr(disp, cart):
    if not disp: return
    try:
        image, draw = _new_frame()
        _border_rect(draw, [0, 0, L_WIDTH - 1, L_HEIGHT - 1])
        draw.rectangle([BORDER_W, BORDER_W, L_WIDTH - BORDER_W - 1, 30], fill=COL_SUCCESS)
        _center_text(draw, 6, "WERO PAYMENT", FONT_LG, fill=COL_BG)
        draw.rectangle([BORDER_W, 33, L_WIDTH - BORDER_W - 1, 35], fill=COL_BORDER)

        total = cart.get_total()
        description = cart.get_description()
        qr_img = generate_wero_qr(total, description)
        qr_size = 150
        qr_img = qr_img.resize((qr_size, qr_size))
        qr_x = (L_WIDTH - qr_size) // 2
        qr_y = 42
        image.paste(qr_img, (qr_x, qr_y))
        _border_rect(draw, [qr_x - 4, qr_y - 4, qr_x + qr_size + 3, qr_y + qr_size + 3])
        _center_text(draw, 200, format_price(total), FONT_XL, fill=COL_ACCENT2)
        cats = " / ".join(cart.get_categories())[:35].upper()
        _center_text(draw, L_HEIGHT - 18, cats, FONT_SM, fill=COL_MUTED)
        _show(disp, image)
    except Exception as e:
        show_warning_screen(disp, "QR ERROR", "Could not generate payment QR.")

def _do_lcd_render(disp, state, cart, message, item_name, item_price, last_part):
    if state == AppState.IDLE:
        show_idle_screen(disp)
    elif state == AppState.SHOPPING:
        if message and "ERROR" in message.upper():
            show_warning_screen(disp, "ERROR", message)
            # Don't block the worker with sleep — schedule the follow-up render
            def _delayed_cart_view(d, c, p):
                time.sleep(1.4)
                LCD_QUEUE.put((d, AppState.SHOPPING, CartSnapshot(c) if hasattr(c, 'items') else c,
                               None, None, None, p))
            threading.Thread(target=_delayed_cart_view, args=(disp, cart, last_part), daemon=True).start()
        elif message and ("Removed" in message or "Aborted" in message):
            show_message_screen(disp, "INFO", message, color=COL_DANGER)
            def _delayed_cart_view(d, c, p):
                time.sleep(0.9)
                LCD_QUEUE.put((d, AppState.SHOPPING, CartSnapshot(c) if hasattr(c, 'items') else c,
                               None, None, None, p))
            threading.Thread(target=_delayed_cart_view, args=(disp, cart, last_part), daemon=True).start()
        else:
            show_item_on_lcd(disp, last_part, cart)
    elif state == AppState.CANCEL_CONFIRM:
        show_warning_screen(disp, "CANCEL?",
                            "Scan CANCEL again to discard cart. Scan anything else to resume.")
    elif state == AppState.CHECKOUT_CONFIRM:
        if message and "Unknown Barcode" in message:
            show_warning_screen(disp, "UNKNOWN", message)
            def _delayed_confirm(d, c):
                time.sleep(1.4)
                LCD_QUEUE.put((d, AppState.CHECKOUT_CONFIRM, c, None, None, None, None))
            threading.Thread(target=_delayed_confirm, args=(disp, cart), daemon=True).start()
        else:
            show_confirmation_screen(disp, cart)
    elif state == AppState.PROCESSING:
        show_message_screen(disp, "PROCESSING", "Removing items from InvenTree stock...", color=COL_ACCENT2)
    elif state == AppState.QR_DISPLAY:
        show_payment_qr(disp, cart)

def _drain_lcd_queue():
    """Discard all pending LCD render tasks so only the latest is shown."""
    while not LCD_QUEUE.empty():
        try:
            LCD_QUEUE.get_nowait()
            LCD_QUEUE.task_done()
        except queue.Empty:
            break

def lcd_worker():
    while True:
        task = LCD_QUEUE.get()
        try:
            disp, state, cart_snap, message, item_name, item_price, last_part = task
            _do_lcd_render(disp, state, cart_snap, message, item_name, item_price, last_part)
        except Exception as e:
            print(f"LCD Error: {e}")
        LCD_QUEUE.task_done()

threading.Thread(target=lcd_worker, daemon=True).start()

def render(disp, state, cart, message=None, item_name=None, item_price=None, last_part=None):
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
        print("Commands: [CONFIRM] to checkout | [REMOVE] to undo | [CANCEL] to confirm cancel")
        
    elif state == AppState.CANCEL_CONFIRM:
        print("!!! CANCEL TRANSACTION !!!")
        print(f"Cart has {len(cart.items)} item(s).")
        print("Scan [CANCEL] again to discard cart, or scan anything else to resume.")
        
    elif state == AppState.CHECKOUT_CONFIRM:
        print("--- CHECKOUT ---")
        if message: print(f"*** {message} ***")
        for part, qty in cart.items:
            print(f"{qty}x {part.get('name', 'Unknown')}")
        print("-" * 20)
        print(f"GRAND TOTAL: {format_price(cart.get_total())}")
        print("Scan [CONFIRM] to finalize | [CANCEL] to confirm cancel | [REMOVE] to go back.")
        
    elif state == AppState.PROCESSING:
        print("Processing checkout...")
        
    elif state == AppState.QR_DISPLAY:
        print("--- PAYMENT SUCCESS ---")
        print("Stock successfully removed from InvenTree.")
        print(f"Total Due: {format_price(cart.get_total())}")
        if message: print(f"\n{message}")
        print("\nScan [CONFIRM], [CANCEL], or [REMOVE] to start a new transaction.")

    print("="*40)
    
    if disp:
        cart_snap = CartSnapshot(cart)
        # Drain stale frames so only the latest render hits the display
        _drain_lcd_queue()
        LCD_QUEUE.put((disp, state, cart_snap, message, item_name, item_price, last_part))

# --- Main App Logic ---
def handle_barcode(state, barcode, cart):
    """Returns (new_state, message, item_name, item_price, last_part)"""
    bc = barcode.upper()
    
    if state == AppState.IDLE:
        if bc in (CONFIRM_BARCODE, CANCEL_BARCODE, REMOVE_BARCODE):
            return AppState.IDLE, "Cart is empty.", None, None, None
        part = get_item_by_barcode(barcode)
        if part:
            cart.add_item(part)
            return AppState.SHOPPING, None, part.get('name'), extract_price(part), part
        return AppState.IDLE, f"Unknown Barcode: {barcode}", None, None, None

    if state == AppState.QR_DISPLAY:
        if bc in (CONFIRM_BARCODE, CANCEL_BARCODE, REMOVE_BARCODE):
            cart.clear()
            return AppState.IDLE, "Transaction Complete. Ready.", None, None, None
        return AppState.QR_DISPLAY, "Please finish payment. Scan CONFIRM to start new transaction.", None, None, None

    if state == AppState.SHOPPING:
        if bc == CANCEL_BARCODE: return AppState.CANCEL_CONFIRM, None, None, None, None
        elif bc == CONFIRM_BARCODE: return AppState.CHECKOUT_CONFIRM, None, None, None, None
        elif bc == REMOVE_BARCODE:
            removed = cart.remove_last_item()
            if cart.is_empty(): return AppState.IDLE, "Cart is now empty.", None, None, None
            msg = f"Removed {removed.get('name')}" if removed else "Nothing to remove."
            return AppState.SHOPPING, msg, None, None, None
        else:
            part = get_item_by_barcode(barcode)
            if part:
                cart.add_item(part)
                return AppState.SHOPPING, None, part.get('name'), extract_price(part), part
            return AppState.SHOPPING, f"Unknown Barcode: {barcode}", None, None, None

    if state == AppState.CANCEL_CONFIRM:
        if bc == CANCEL_BARCODE:
            cart.clear()
            return AppState.IDLE, "Transaction cancelled.", None, None, None
        elif bc in (CONFIRM_BARCODE, REMOVE_BARCODE):
            return AppState.SHOPPING, "Cancellation aborted. Cart unchanged.", None, None, None
        else:
            # Any product scan also resumes shopping
            part = get_item_by_barcode(barcode)
            if part:
                cart.add_item(part)
                return AppState.SHOPPING, "Cancellation aborted.", part.get('name'), extract_price(part), part
            return AppState.SHOPPING, "Cancellation aborted. Cart unchanged.", None, None, None

    if state == AppState.CHECKOUT_CONFIRM:
        if bc == CONFIRM_BARCODE:
            return AppState.PROCESSING, None, None, None, None
        elif bc == CANCEL_BARCODE:
            # Require confirmation before discarding a cart that was ready for checkout
            return AppState.CANCEL_CONFIRM, None, None, None, None
        elif bc == REMOVE_BARCODE:
            return AppState.SHOPPING, "Checkout aborted. Continue scanning.", None, None, None
        else:
            part = get_item_by_barcode(barcode)
            if part:
                cart.add_item(part)
                return AppState.SHOPPING, "Item added. Re-scan CONFIRM when ready.", part.get('name'), extract_price(part), part
            return AppState.CHECKOUT_CONFIRM, f"Unknown Barcode: {barcode}", None, None, None

    return state, "Unexpected State", None, None, None

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
    """Read one complete barcode from the evdev device.
    Uses a short 50 ms poll interval so the main loop stays responsive
    (timeout check, etc.) without busy-waiting.
    """
    barcode = ""
    try:
        while True:
            r, _, _ = select.select([device], [], [], 0.05)  # 50 ms poll
            if not r:
                # Nothing ready — return empty so the caller can do housekeeping
                return ""
            for event in device.read():
                if event.type == ecodes.EV_KEY:
                    data = evdev.categorize(event)
                    if data.keystate == 1:  # key-down only
                        if data.scancode == ecodes.KEY_ENTER:
                            res = barcode.strip()
                            barcode = ""
                            if res: return res
                        else:
                            char = SCAN_CODES.get(data.scancode)
                            if char is not None: barcode += char
    except Exception:
        return None

def main():
    disp = None
    if HAS_LCD:
        try:
            if hasattr(LCD, 'LCD_2inch4'): disp = LCD.LCD_2inch4()
            else: disp = LCD.LCD_2in4()
            disp.Init()
            disp.clear()
        except: disp = None

    scanner = find_scanner()
    print("\n--- InvenTree Shopping System (Terminal Mode) ---")
    print(f"Makerspace: {HTL_NAME}")

    # Pre-warm barcode cache in the background so the first scan of any
    # known part is an instant cache hit with zero network latency.
    def _prewarm_cache():
        if not INVENTREE_TOKEN: return
        try:
            page, loaded = 1, 0
            while True:
                r = API_SESSION.get(
                    f"{INVENTREE_URL}/api/part/?limit=100&offset={(page-1)*100}&category_detail=true",
                    timeout=10,
                )
                if r.status_code != 200: break
                data = r.json()
                results = data if isinstance(data, list) else data.get("results", [])
                for part in results:
                    bc = part.get("barcode") or part.get("IPN")
                    if bc and not BARCODE_CACHE.get(bc):
                        BARCODE_CACHE.cache[bc] = {
                            "pk": part.get("pk"),
                            "name": part.get("name"),
                            "pricing_min": part.get("pricing_min"),
                            "pricing_max": part.get("pricing_max"),
                            "sell_price": part.get("sell_price"),
                            "thumbnail": part.get("thumbnail"),
                            "image": part.get("image"),
                            "category_detail": part.get("category_detail"),
                            "category": part.get("category"),
                            "_stock_item_pk": None,  # fetched lazily on first scan
                        }
                        loaded += 1
                if isinstance(data, list) or len(results) < 100: break
                page += 1
            if loaded:
                BARCODE_CACHE.flush()  # persist the bulk load
                print(f"[cache] Pre-warmed {loaded} parts from InvenTree")
        except Exception as e:
            print(f"[cache] Pre-warm failed: {e}")

    threading.Thread(target=_prewarm_cache, daemon=True).start()

    cart = ShoppingCart()
    state = AppState.IDLE
    last_interaction = time.time()
    last_scan_time = 0
    TIMEOUT_SECONDS = 300
    last_part = None

    render(disp, state, cart)
    
    try:
        while True:
            if state not in (AppState.IDLE, AppState.QR_DISPLAY):
                if time.time() - last_interaction > TIMEOUT_SECONDS:
                    cart.clear()
                    state = AppState.IDLE
                    last_part = None
                    render(disp, state, cart, "Timeout: Cart Cleared")
                    last_interaction = time.time()

            if scanner:
                barcode = read_scancode(scanner)
                if barcode is None:
                    # Device disconnected — wait then try to reconnect
                    time.sleep(2)
                    scanner = find_scanner()
                    continue
            else:
                try:
                    raw = input("Scan: ").strip()
                    barcode = decode_manual_input(raw) if raw else ""
                except EOFError: break

            if not barcode: continue

            current_time = time.time()
            if (current_time - last_scan_time) < 0.15: continue  # debounce
                
            last_interaction = current_time
            last_scan_time = current_time

            bc_upper = barcode.upper()
            is_command = bc_upper in (CONFIRM_BARCODE, CANCEL_BARCODE, REMOVE_BARCODE)

            # Show immediate "SEARCHING" feedback on LCD while the API call happens,
            # but only for product scans (not commands which are handled instantly).
            if disp and not is_command:
                _drain_lcd_queue()
                _searching_img, _searching_draw = _new_frame()
                _border_rect(_searching_draw, [0, 0, L_WIDTH - 1, L_HEIGHT - 1])
                _searching_draw.rectangle([BORDER_W, BORDER_W, L_WIDTH - BORDER_W - 1, 34], fill=COL_ACCENT)
                _center_text(_searching_draw, 8, "SEARCHING...", FONT_LG, fill=COL_FG)
                _center_text(_searching_draw, 100, barcode[:20].upper(), FONT_MD, fill=COL_MUTED)
                _show(disp, _searching_img)

            new_state, msg, item_name, item_price, scanned_part = handle_barcode(state, barcode, cart)
            
            # Special fast-path for CHECKOUT_CONFIRM -> PROCESSING -> QR_DISPLAY
            if new_state == AppState.PROCESSING:
                render(disp, AppState.PROCESSING, cart)
                success, err_msg = remove_stock_from_inventree(cart)
                if success:
                    new_state = AppState.QR_DISPLAY
                    msg = "Stock removed!"
                else:
                    new_state = AppState.SHOPPING
                    msg = f"Checkout Failed: {err_msg}"
            
            state = new_state
            if scanned_part: last_part = scanned_part
            
            render(disp, state, cart, msg, item_name, item_price, last_part)

    except KeyboardInterrupt:
        print("\nExiting...")
    finally:
        if HAS_LCD and 'config' in globals() and hasattr(config, 'module_exit'):
            config.module_exit()

if __name__ == "__main__":
    main()
