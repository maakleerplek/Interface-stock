import os
import sys
import time
import glob
import textwrap
import requests
import json
import qrcode
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont
from dotenv import load_dotenv
from collections import defaultdict

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

# Special barcodes for checkout confirmation and cancellation
CONFIRM_BARCODE = "CONFIRM"
CANCEL_BARCODE = "CANCEL"
REMOVE_BARCODE = "REMOVE"

# AZERTY Scan Code Map (for evdev)
SCAN_CODES = {
    2: '1', 3: '2', 4: '3', 5: '4', 6: '5', 7: '6', 8: '7', 9: '8', 10: '9', 11: '0',
    12: '-', 13: '=', 
    16: 'Q', 17: 'W', 18: 'E', 19: 'R', 20: 'T', 21: 'Y', 22: 'U', 23: 'I', 24: 'O', 25: 'P',
    30: 'A', 31: 'S', 32: 'D', 33: 'F', 34: 'G', 35: 'H', 36: 'J', 37: 'K', 38: 'L',
    44: 'Z', 45: 'X', 46: 'C', 47: 'V', 48: 'B', 49: 'N', 50: 'M',
    51: ',', 52: '.', 53: '/', 57: ' ', 
    12: '_', # Common mapping for underscores in some scanners
    ecodes.KEY_ENTER: '\n',
}

# Fallback character map for manual terminal input
AZERTY_MAP = {
    '&': '1', 'é': '2', '"': '3', "'": '4', '(': '5',
    '§': '6', 'è': '7', '!': '8', 'ç': '9', 'à': '0',
    ')': '-', '_': '_', '=': '=', '+': '+',
}

def decode_manual_input(scanned_text):
    return "".join(AZERTY_MAP.get(c, c) for c in scanned_text)

# --- BRUTALISM DESIGN SYSTEM ---
# Colors
COL_BG      = (10, 10, 10)       # Near-black background
COL_FG      = (240, 240, 240)    # Primary text
COL_ACCENT  = (255, 60, 20)      # Vermillion red — action, prices
COL_ACCENT2 = (255, 220, 0)      # Yellow — warnings, totals
COL_MUTED   = (100, 100, 100)    # Secondary text
COL_BLOCK   = (30, 30, 30)       # Panel backgrounds
COL_SUCCESS = (0, 200, 80)       # Confirmation, QR
COL_BORDER  = (240, 240, 240)    # Thick white borders
COL_DANGER  = (180, 30, 20)      # Cancel / error

# Screen dimensions
L_WIDTH, L_HEIGHT = 320, 240
BORDER_W = 3  # Brutalist thick border width

# Fonts — loaded ONCE at startup (major speed win)
_FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
_HAS_FONT = os.path.exists(_FONT_PATH)
FONT_XL = ImageFont.truetype(_FONT_PATH, 26) if _HAS_FONT else ImageFont.load_default()
FONT_LG = ImageFont.truetype(_FONT_PATH, 18) if _HAS_FONT else ImageFont.load_default()
FONT_MD = ImageFont.truetype(_FONT_PATH, 14) if _HAS_FONT else ImageFont.load_default()
FONT_SM = ImageFont.truetype(_FONT_PATH, 11) if _HAS_FONT else ImageFont.load_default()

def _new_frame(bg=None):
    """Create a fresh canvas + draw context."""
    img = Image.new('RGB', (L_WIDTH, L_HEIGHT), bg or COL_BG)
    return img, ImageDraw.Draw(img)

def _show(disp, image):
    """Push image to LCD (handles rotation)."""
    if HAS_LCD:
        disp.ShowImage(image.rotate(90, expand=True))

def _border_rect(draw, box, fill=None, border_color=None, width=BORDER_W):
    """Draw a rectangle with a thick brutalist border."""
    if fill:
        draw.rectangle(box, fill=fill)
    draw.rectangle(box, outline=border_color or COL_BORDER, width=width)

def _center_text(draw, y, text, font, fill=COL_FG, area_width=L_WIDTH):
    """Draw horizontally centered text."""
    w = draw.textlength(text, font=font)
    draw.text(((area_width - w) / 2, y), text, font=font, fill=fill)

# --- 1. LCD Configuration ---
# ... (rest of find_lib_path and LCD setup)
def find_lib_path():
    for root_dir in ['.', 'lcd_assets', 'LCD_Module_code']:
        if not os.path.exists(root_dir): continue
        for config_name in ['lcdconfig.py', 'tp_config.py']:
            search_pattern = os.path.join(os.getcwd(), root_dir, '**', config_name)
            matches = glob.glob(search_pattern, recursive=True)
            if matches:
                lib_dir = os.path.dirname(matches[0])
                if os.path.basename(lib_dir) == 'lib':
                    return os.path.dirname(lib_dir)
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

# --- 2. Barcode Cache ---

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

# Initialize global cache
BARCODE_CACHE = BarcodeCache()

# --- 3. InvenTree API Functions ---

def fetch_part_details(part_id):
    """Fetch full part details by ID."""
    if not part_id: return None
    headers = {"Authorization": f"Token {INVENTREE_TOKEN}"}
    url = f"{INVENTREE_URL}/api/part/{part_id}/"
    try:
        print(f"DEBUG: Fetching full details for part ID {part_id}...")
        response = requests.get(url, headers=headers, timeout=5, verify=False)
        if response.status_code == 200:
            return response.json()
    except Exception as e:
        print(f"DEBUG: Error fetching part {part_id}: {e}")
    return None

def get_item_by_barcode(barcode):
    # 1. Check Cache First
    cached_part = BARCODE_CACHE.get(barcode)
    if cached_part:
        print(f"DEBUG: Cache HIT for '{barcode}'")
        return cached_part

    if not INVENTREE_TOKEN:
        print("Error: INVENTREE_TOKEN not configured")
        return None

    headers = {"Authorization": f"Token {INVENTREE_TOKEN}"}
    
    # --- Attempt 1: Barcode API (Direct Match) ---
    print(f"DEBUG: Cache MISS. Checking Barcode API for '{barcode}'...")
    url = f"{INVENTREE_URL}/api/barcode/"
    try:
        response = requests.post(url, data={"barcode": barcode}, headers=headers, timeout=5, verify=False)
        if response.status_code == 200:
            res = response.json()
            
            # Helper to extract part detail from various response structures
            def extract_from_obj(obj):
                if not isinstance(obj, dict): return None
                # Check instance wrapper first
                instance = obj.get("instance")
                if isinstance(instance, dict):
                    return instance.get("part_detail") or fetch_part_details(instance.get("part"))
                # Then check direct fields
                return obj.get("part_detail") or fetch_part_details(obj.get("part"))

            part = None
            if "stockitem" in res:
                print("DEBUG: Processing StockItem match")
                s_obj = res["stockitem"]
                # Extract stock item PK
                stock_item_pk = None
                if isinstance(s_obj, dict):
                    stock_item_pk = s_obj.get("pk") or (s_obj.get("instance", {}).get("pk") if isinstance(s_obj.get("instance"), dict) else None)
                
                part = extract_from_obj(s_obj)
                if part and stock_item_pk:
                    part["_stock_item_pk"] = stock_item_pk
            
            elif "part" in res:
                print("DEBUG: Processing Part match")
                p_obj = res["part"]
                if isinstance(p_obj, dict):
                    if "instance" in p_obj: part = p_obj["instance"]
                    elif "name" in p_obj: part = p_obj
                if not part:
                    part = fetch_part_details(p_obj)
            
            if part:
                BARCODE_CACHE.set(barcode, part)
                return part
                    
    except Exception as e:
        print(f"DEBUG: Barcode API Error: {e}")

    # --- Attempt 2: Search Part by EXACT Barcode field ---
    print(f"DEBUG: Searching Part barcode field for '{barcode}'...")
    try:
        # Check original, lowercase, and uppercase variants
        variants = list(dict.fromkeys([barcode, barcode.lower(), barcode.upper()]))
        for v in variants:
            url = f"{INVENTREE_URL}/api/part/?barcode={v}&category_detail=true"
            response = requests.get(url, headers=headers, timeout=5, verify=False)
            if response.status_code == 200:
                items = response.json()
                results = items if isinstance(items, list) else items.get("results", [])
                for item in results:
                    if item.get("barcode", "").lower() == v.lower() or item.get("IPN", "").lower() == v.lower():
                        print(f"DEBUG: Found exact match (variant {v}): {item.get('name')}")
                        BARCODE_CACHE.set(barcode, item)
                        return item

        # --- Attempt 3: Search Part by EXACT IPN field ---
        print(f"DEBUG: Searching Part IPN field for variants of '{barcode}'...")
        for v in variants:
            url = f"{INVENTREE_URL}/api/part/?IPN={v}&category_detail=true"
            response = requests.get(url, headers=headers, timeout=5, verify=False)
            if response.status_code == 200:
                items = response.json()
                results = items if isinstance(items, list) else items.get("results", [])
                for item in results:
                    if item.get("IPN", "").lower() == v.lower():
                        print(f"DEBUG: Found exact IPN match (variant {v}): {item.get('name')}")
                        BARCODE_CACHE.set(barcode, item)
                        return item
    except Exception as e:
        print(f"DEBUG: Part Search Error: {e}")

    # --- Attempt 4: Search StockItem by EXACT Barcode field ---
    print(f"DEBUG: Searching StockItem barcode field for '{barcode}'...")
    try:
        url = f"{INVENTREE_URL}/api/stock/?barcode={barcode}&part_detail=true"
        response = requests.get(url, headers=headers, timeout=5, verify=False)
        if response.status_code == 200:
            items = response.json()
            results = items if isinstance(items, list) else items.get("results", [])
            for item in results:
                if item.get("barcode") == barcode:
                    print(f"DEBUG: Found StockItem match, fetching part details")
                    stock_item_pk = item.get("pk")
                    part = item.get("part_detail") or fetch_part_details(item.get("part"))
                    if part:
                        part["_stock_item_pk"] = stock_item_pk
                        BARCODE_CACHE.set(barcode, part)
                        return part
    except Exception as e:
        print(f"DEBUG: StockItem Field Search Error: {e}")

    return None

def extract_price(part_detail):
    if not part_detail: return 0.0
    # Prefer pricing_max for the "maximum price" requirement, fall back to min or sell price
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
    """Format price as string with EUR symbol"""
    if price == 0.0: return "-"
    return f"€{price:.2f}"

def extract_category(part_detail):
    """Extract category name from part detail"""
    if not part_detail: return "uncategorized"
    
    # Try to get category_detail first
    cat_detail = part_detail.get('category_detail')
    if isinstance(cat_detail, dict) and cat_detail.get('name'):
        return cat_detail.get('name').lower()
    
    # Check if category path is available
    path = part_detail.get('category_path')
    if path and isinstance(path, str):
        return path.split('/')[-1].lower()

    # Fall back to category name if it's directly in the object
    if isinstance(part_detail.get('category_name'), str):
        return part_detail.get('category_name').lower()
    
    return "uncategorized"

def get_image(part_detail):
    img_path = part_detail.get('thumbnail') or part_detail.get('image')
    if not img_path: 
        print("DEBUG: No image path found in part_detail")
        return None

    # Generate a local filename based on the part ID and filename
    part_id = part_detail.get('pk', 'unknown')
    ext = os.path.splitext(img_path)[1] or ".png"
    local_filename = f"image_cache/part_{part_id}{ext}"

    # 1. Check if we have it locally already
    if os.path.exists(local_filename):
        try:
            return Image.open(local_filename)
        except Exception as e:
            print(f"DEBUG: Local image error ({local_filename}): {e}")
            pass # If file is corrupt, try re-downloading

    # 2. Download and cache
    img_url = f"{INVENTREE_URL}{img_path}" if img_path.startswith('/') else img_path
    print(f"DEBUG: Fetching image from {img_url}")
    try:
        headers = {"Authorization": f"Token {INVENTREE_TOKEN}"}
        response = requests.get(img_url, headers=headers, timeout=5, verify=False)
        if response.status_code == 200:
            img_data = response.content
            # Save for next time
            with open(local_filename, "wb") as f:
                f.write(img_data)
            print(f"DEBUG: Saved image to {local_filename}")
            return Image.open(BytesIO(img_data))
        else:
            print(f"DEBUG: Image download failed with status {response.status_code}")
    except Exception as e:
        print(f"DEBUG: Image fetch error: {e}")
    return None
# --- 3. Shopping Cart Management ---

def find_stock_item_for_part(part_id):
    """Find a suitable stock item for a part (one with most quantity)"""
    if not part_id: return None
    headers = {"Authorization": f"Token {INVENTREE_TOKEN}"}
    url = f"{INVENTREE_URL}/api/stock/?part={part_id}&in_stock=true"
    try:
        # We need verify=False because the URL is an IP with self-signed cert
        response = requests.get(url, headers=headers, timeout=5, verify=False)
        if response.status_code == 200:
            items = response.json()
            results = items if isinstance(items, list) else items.get("results", [])
            if results:
                # Sort by quantity descending to pick the one with most stock
                results.sort(key=lambda x: float(x.get('quantity', 0)), reverse=True)
                return results[0].get('pk')
    except Exception as e:
        print(f"DEBUG: Error finding stock for part {part_id}: {e}")
    return None

def send_changelog_event(action, item_name, quantity, price=None):
    """Fire-and-forget: notify tv-presentation changelog endpoint."""
    if not TV_PRESENTATION_URL:
        return
    try:
        payload = {"action": action, "source": "interface-stock", "item_name": item_name, "quantity": int(quantity)}
        if price is not None:
            payload["price"] = round(float(price), 2)
        requests.post(f"{TV_PRESENTATION_URL}/api/changelog", json=payload, timeout=3)
    except Exception:
        pass  # Non-critical — never block the main checkout flow

def remove_stock_from_inventree(cart):
    """Remove items in cart from InvenTree stock"""
    if not INVENTREE_TOKEN:
        print("Error: INVENTREE_TOKEN not configured. Stock not removed.")
        return False

    headers = {
        "Authorization": f"Token {INVENTREE_TOKEN}",
        "Content-Type": "application/json"
    }
    url = f"{INVENTREE_URL}/api/stock/remove/"
    
    items_to_remove = []
    
    for part_detail, quantity in cart.items:
        # Use specific stock item if we have it (from barcode scan), otherwise find one
        stock_item_pk = part_detail.get('_stock_item_pk') or find_stock_item_for_part(part_detail.get('pk'))
        
        if stock_item_pk:
            items_to_remove.append({
                "pk": stock_item_pk,
                "quantity": float(quantity)
            })
        else:
            print(f"WARNING: Could not find any stock for part {part_detail.get('name')} (ID: {part_detail.get('pk')})")

    if not items_to_remove:
        print("No stock items found to remove.")
        return False

    payload = {
        "items": items_to_remove,
        "notes": f"Purchased via Interface-stock ({HTL_NAME})"
    }

    try:
        print(f"DEBUG: Removing stock for {len(items_to_remove)} items...")
        response = requests.post(url, json=payload, headers=headers, timeout=10, verify=False)
        if response.status_code in [200, 201]:
            print("SUCCESS: Stock removed from InvenTree.")
            for part_detail, quantity in cart.items:
                unit_price = extract_price(part_detail)
                total_price = unit_price * quantity if unit_price else None
                send_changelog_event("checkout", part_detail.get("name", "Unknown"), quantity, total_price)
            return True
        else:
            print(f"ERROR: Failed to remove stock. Status: {response.status_code}")
            print(f"Response: {response.text}")
            return False
    except Exception as e:
        print(f"ERROR: Exception during stock removal: {e}")
        return False

class ShoppingCart:
    def __init__(self):
        self.items = []  # List of (part_detail, quantity)
        self.confirm_state = 0  # 0: normal, 1: awaiting final confirm, 2: show QR
        self.cancel_state = 0   # 0: normal, 1: awaiting final cancel

    def add_item(self, part_detail):
        """Add item to cart or increment quantity if already exists"""
        pk = part_detail.get('pk')
        spk = part_detail.get('_stock_item_pk')
        
        for i, (item, qty) in enumerate(self.items):
            # Same part AND same stock item (if applicable)
            if item.get('pk') == pk and item.get('_stock_item_pk') == spk:
                self.items[i] = (item, qty + 1)
                return
        self.items.append((part_detail, 1))

    def remove_last_item(self):
        """Undo: Remove the most recently added item (or decrease its quantity)"""
        if not self.items:
            return None
        
        part, qty = self.items[-1]
        if qty > 1:
            self.items[-1] = (part, qty - 1)
        else:
            self.items.pop()
        return part
    def get_total(self):
        """Calculate total price of all items in cart"""
        total = 0.0
        for part_detail, qty in self.items:
            total += extract_price(part_detail) * qty
        return total
    
    def get_categories(self):
        """Get unique categories from cart items"""
        categories = set()
        for part_detail, _ in self.items:
            categories.add(extract_category(part_detail))
        return sorted(categories)
    
    def get_description(self):
        """Generate description for payment (comma separated category list)"""
        categories = self.get_categories()
        if not categories:
            return f"{HTL_NAME} - Purchase"
        # Join unique categories with commas
        return f"{HTL_NAME}: " + ",".join(categories)
    
    def clear(self):
        """Clear the cart and reset states"""
        self.items = []
        self.confirm_state = 0
        self.cancel_state = 0
    
    def is_empty(self):
        return len(self.items) == 0

# --- 4. Display Logic (Brutalist) ---

def show_message_screen(disp, title, message, color=None):
    """Full-screen message — brutalist block style."""
    image, draw = _new_frame()
    accent = color or COL_ACCENT

    # Outer border
    _border_rect(draw, [0, 0, L_WIDTH - 1, L_HEIGHT - 1])

    # Title block — solid accent band
    draw.rectangle([BORDER_W, BORDER_W, L_WIDTH - BORDER_W - 1, 50], fill=accent)
    _center_text(draw, 14, title.upper(), FONT_LG, fill=COL_FG)

    # Horizontal rule
    draw.rectangle([BORDER_W, 53, L_WIDTH - BORDER_W - 1, 55], fill=COL_BORDER)

    # Message body
    lines = textwrap.fill(message, width=28).split('\n')
    y = 80
    for line in lines:
        _center_text(draw, y, line.upper(), FONT_MD, fill=COL_FG)
        y += 22

    _show(disp, image)
    if not HAS_LCD:
        print(f"\n[{title}] {message}")

def show_warning_screen(disp, title, message):
    show_message_screen(disp, title, message, color=COL_DANGER)

def show_idle_screen(disp):
    """Idle screen — stark brutalist waiting state."""
    image, draw = _new_frame()

    # Outer border
    _border_rect(draw, [0, 0, L_WIDTH - 1, L_HEIGHT - 1])

    # Inner bordered box
    _border_rect(draw, [20, 40, L_WIDTH - 21, L_HEIGHT - 60], fill=COL_BLOCK)

    # Large SCAN text
    _center_text(draw, 65, "SCAN", FONT_XL, fill=COL_ACCENT)

    # Separator line
    draw.rectangle([50, 105, L_WIDTH - 50, 107], fill=COL_BORDER)

    # Subtitle
    _center_text(draw, 120, "READY", FONT_LG, fill=COL_MUTED)

    # Makerspace name at bottom
    name = HTL_NAME[:30].upper()
    _center_text(draw, L_HEIGHT - 45, name, FONT_SM, fill=COL_MUTED)

    _show(disp, image)
    if not HAS_LCD:
        print("\n[IDLE] Ready to Scan")

def show_item_on_lcd(disp, part_detail, cart):
    """Item view — brutalist split layout."""
    image, draw = _new_frame()
    SPLIT_X = 205  # Left: item | Right: cart

    # === LEFT PANEL — Item ===
    _border_rect(draw, [0, 0, SPLIT_X - 1, L_HEIGHT - 1])

    if part_detail:
        name = part_detail.get('name', 'UNKNOWN')
        price = extract_price(part_detail)

        # Header bar
        draw.rectangle([BORDER_W, BORDER_W, SPLIT_X - BORDER_W - 1, 32], fill=COL_ACCENT)
        draw.text((8, 8), "SCANNED", font=FONT_LG, fill=COL_FG)

        # Item image
        img = get_image(part_detail)
        if img:
            img.thumbnail((80, 80))
            image.paste(img, (10, 42))
        else:
            _border_rect(draw, [10, 42, 90, 122], fill=COL_BLOCK)
            draw.text((30, 72), "—", font=FONT_LG, fill=COL_MUTED)

        # Item name (right of image)
        lines = textwrap.wrap(name.upper(), width=16)
        y_text = 44 if len(lines) > 1 else 50
        for line in lines[:2]:
            draw.text((100, y_text), line, font=FONT_MD, fill=COL_FG)
            y_text += 18

        # Separator
        draw.rectangle([BORDER_W, 130, SPLIT_X - BORDER_W - 1, 132], fill=COL_BORDER)

        # Price — large, vermillion
        draw.text((10, 142), "PRICE", font=FONT_SM, fill=COL_MUTED)
        draw.text((10, 158), format_price(price), font=FONT_XL, fill=COL_ACCENT)

    else:
        # Not found — big error
        _center_text(draw, 70, "NOT", FONT_XL, fill=COL_ACCENT, area_width=SPLIT_X)
        _center_text(draw, 105, "FOUND", FONT_XL, fill=COL_ACCENT, area_width=SPLIT_X)

    # === RIGHT PANEL — Cart ===
    _border_rect(draw, [SPLIT_X, 0, L_WIDTH - 1, L_HEIGHT - 1], fill=COL_BLOCK)

    # Cart header
    draw.rectangle([SPLIT_X + BORDER_W, BORDER_W, L_WIDTH - BORDER_W - 1, 28], fill=COL_BORDER)
    draw.text((SPLIT_X + 10, 6), "CART", font=FONT_MD, fill=COL_BG)

    if cart.is_empty():
        draw.text((SPLIT_X + 20, 100), "EMPTY", font=FONT_SM, fill=COL_MUTED)
    else:
        y = 36
        for part, qty in cart.items[:4]:
            full_name = part.get('name', '?').upper()
            draw.text((SPLIT_X + 6, y), f"{qty}×", font=FONT_SM, fill=COL_ACCENT)
            
            # Wrap name into 2 lines
            lines = textwrap.wrap(full_name, width=12)
            if lines:
                draw.text((SPLIT_X + 6, y + 13), lines[0], font=FONT_SM, fill=COL_FG)
                if len(lines) > 1:
                    draw.text((SPLIT_X + 6, y + 25), lines[1], font=FONT_SM, fill=COL_FG)
            y += 40

        if len(cart.items) > 4:
            draw.text((SPLIT_X + 6, y), f"+{len(cart.items)-4}", font=FONT_SM, fill=COL_MUTED)

        # Total bar at bottom
        draw.rectangle([SPLIT_X + BORDER_W, L_HEIGHT - 40, L_WIDTH - BORDER_W - 1, L_HEIGHT - BORDER_W - 1], fill=COL_BG)
        draw.rectangle([SPLIT_X, L_HEIGHT - 42, L_WIDTH - 1, L_HEIGHT - 42 + 2], fill=COL_BORDER)
        draw.text((SPLIT_X + 6, L_HEIGHT - 36), format_price(cart.get_total()), font=FONT_LG, fill=COL_ACCENT2)

    _show(disp, image)
    if not HAS_LCD:
        print(f"\n[DISPLAY] {part_detail.get('name') if part_detail else 'N/A'} - {format_price(extract_price(part_detail))}")
        print(f"[CART] {len(cart.items)} items - Total: {format_price(cart.get_total())}")

def show_confirmation_screen(disp, cart):
    """Checkout confirmation — brutalist item grid."""
    image, draw = _new_frame()

    # Outer border
    _border_rect(draw, [0, 0, L_WIDTH - 1, L_HEIGHT - 1])

    # Header block
    draw.rectangle([BORDER_W, BORDER_W, L_WIDTH - BORDER_W - 1, 34], fill=COL_ACCENT2)
    _center_text(draw, 8, "CHECKOUT", FONT_LG, fill=COL_BG)

    # Separator
    draw.rectangle([BORDER_W, 37, L_WIDTH - BORDER_W - 1, 39], fill=COL_BORDER)

    # Item list
    y = 46
    for part, qty in cart.items[:4]:
        full_name = part.get('name', 'ITEM').upper()
        price = extract_price(part) * qty

        # Draw Qty and name with wrapping
        lines = textwrap.wrap(full_name, width=22)
        draw.text((10, y), f"{qty}×  {lines[0]}", font=FONT_SM, fill=COL_FG)
        
        # Price on the same line as the first line of name
        price_str = format_price(price)
        pw = draw.textlength(price_str, font=FONT_SM)
        draw.text((L_WIDTH - pw - 10, y), price_str, font=FONT_SM, fill=COL_ACCENT)
        
        # Draw second line if it exists
        if len(lines) > 1:
            draw.text((42, y + 14), lines[1], font=FONT_SM, fill=COL_FG)
            y += 30
        else:
            y += 22

    if len(cart.items) > 4:
        draw.text((10, y), f"+ {len(cart.items) - 4} MORE...", font=FONT_SM, fill=COL_MUTED)

    # Total block at bottom
    draw.rectangle([BORDER_W, L_HEIGHT - 80, L_WIDTH - BORDER_W - 1, L_HEIGHT - 43], fill=COL_BLOCK)
    draw.rectangle([BORDER_W, L_HEIGHT - 82, L_WIDTH - BORDER_W - 1, L_HEIGHT - 80], fill=COL_BORDER)
    draw.text((10, L_HEIGHT - 78), "TOTAL", font=FONT_LG, fill=COL_FG)
    total_str = format_price(cart.get_total())
    tw = draw.textlength(total_str, font=FONT_XL)
    draw.text((L_WIDTH - tw - 10, L_HEIGHT - 79), total_str, font=FONT_XL, fill=COL_ACCENT2)

    # Stock warning
    _center_text(draw, L_HEIGHT - 55, "SCANNING CONFIRM WILL REMOVE FROM STOCK", FONT_SM, fill=COL_ACCENT)

    # Instruction bar
    draw.rectangle([BORDER_W, L_HEIGHT - 40, L_WIDTH - BORDER_W - 1, L_HEIGHT - BORDER_W - 1], fill=COL_ACCENT)
    _center_text(draw, L_HEIGHT - 34, "SCAN CONFIRM TO FINALIZE", FONT_SM, fill=COL_FG)

    _show(disp, image)
    if not HAS_LCD:
        print("\n" + "="*40)
        print("CHECKOUT CONFIRMATION")
        print("="*40)
        for part, qty in cart.items:
            name = part.get('name', 'Item')
            price = extract_price(part)
            print(f"{qty}x {name} - {format_price(price * qty)}")
        print("-"*40)
        print(f"TOTAL: {format_price(cart.get_total())}")
        print("WARNING: NEXT CONFIRM REMOVES ITEMS FROM STOCK!")
        print("="*40)
        print("Scan CONFIRM again to proceed")

def generate_wero_qr(amount, description):
    """Generate Wero payment QR code
    
    Wero uses EPC QR codes (European Payments Council standard)
    Format: https://www.europeanpaymentscouncil.eu/document-library/guidance-documents/quick-response-code-guidelines-enable-data-capture-initiation
    """
    # EPC QR Code format for SEPA Credit Transfer
    # This is a standard format that most European banking apps support
    epc_data = [
        "BCD",  # Service tag
        "002",  # Version
        "1",    # Character set (1 = UTF-8)
        "SCT",  # Identification (SEPA Credit Transfer)
        "",     # BIC (optional, can be empty)
        HTL_NAME,  # Beneficiary name
        HTL_IBAN,  # Beneficiary account (IBAN)
        f"EUR{amount:.2f}",  # Amount
        "",     # Purpose (optional)
        "",     # Structured reference (optional)
        description  # Unstructured remittance information
    ]
    
    qr_content = "\n".join(epc_data)
    
    # Generate QR code
    qr = qrcode.QRCode(version=1, box_size=4, border=2)
    qr.add_data(qr_content)
    qr.make(fit=True)
    
    return qr.make_image(fill_color="black", back_color="white")

def show_payment_qr(disp, cart):
    """Payment QR — brutalist framed QR code."""
    try:
        image, draw = _new_frame()

        # Outer border
        _border_rect(draw, [0, 0, L_WIDTH - 1, L_HEIGHT - 1])

        # Header block
        draw.rectangle([BORDER_W, BORDER_W, L_WIDTH - BORDER_W - 1, 30], fill=COL_SUCCESS)
        _center_text(draw, 6, "WERO PAYMENT", FONT_LG, fill=COL_BG)

        # Separator
        draw.rectangle([BORDER_W, 33, L_WIDTH - BORDER_W - 1, 35], fill=COL_BORDER)

        # Generate and display QR code
        total = cart.get_total()
        description = cart.get_description()
        qr_img = generate_wero_qr(total, description)

        # QR in a bordered box
        qr_size = 150
        qr_img = qr_img.resize((qr_size, qr_size))
        qr_x = (L_WIDTH - qr_size) // 2
        qr_y = 42
        image.paste(qr_img, (qr_x, qr_y))
        _border_rect(draw, [qr_x - 4, qr_y - 4, qr_x + qr_size + 3, qr_y + qr_size + 3])

        # Amount — large, below QR
        _center_text(draw, 200, format_price(total), FONT_XL, fill=COL_ACCENT2)

        # Categories at very bottom
        cats = " / ".join(cart.get_categories())[:35].upper()
        _center_text(draw, L_HEIGHT - 18, cats, FONT_SM, fill=COL_MUTED)

        _show(disp, image)
    except Exception as e:
        print(f"ERROR: Payment QR display failed: {e}")
        show_warning_screen(disp, "QR ERROR", "Could not generate payment QR.")
    
    if not HAS_LCD:
        print("\n" + "="*40)
        print("WERO PAYMENT QR CODE")
        print("="*40)
        print(f"Amount: {format_price(total)}")
        print(f"Description: {description}")
        print("Scan QR code with your banking app")
        print("="*40)

def check_inventree_connection():
    """Check if the InvenTree server is reachable"""
    if not INVENTREE_URL: return False
    try:
        # Check the base API endpoint
        url = f"{INVENTREE_URL}/api/"
        response = requests.get(url, timeout=3, verify=False)
        return response.status_code == 200
    except:
        return False

# --- 5. Main ---

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
            # Use select for a non-blocking read with a 1-second timeout
            r, w, x = select.select([device], [], [], 1.0)
            if not r:
                return "" # No data within 1 second, return empty to let main loop check timeout

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
        print(f"Scanner Error: {e}")
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

    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    scanner = find_scanner()
    print("\n--- InvenTree Shopping System ---")
    print(f"Makerspace: {HTL_NAME}")
    if scanner: print(f"Hardware: {scanner.name}")
    else: print("Mode: Terminal Input")
    print(f"Scan items to add to cart. Scan '{CONFIRM_BARCODE}' to checkout or '{CANCEL_BARCODE}' to cancel.\n")

    cart = ShoppingCart()
    last_scan_time = 0
    last_interaction_time = time.time()
    TIMEOUT_SECONDS = 300 # 5 minutes
    
    # Show initial screen
    show_idle_screen(disp)
    
    try:
        while True:
            # Check for inactivity timeout (only if cart is not empty or not on idle screen)
            if not cart.is_empty() or cart.confirm_state != 0:
                if (time.time() - last_interaction_time) > TIMEOUT_SECONDS:
                    print("Inactivity timeout. Clearing cart.")
                    cart.clear()
                    show_message_screen(disp, "TIMEOUT", "Inactivity timeout. Cart cleared.", color=COL_MUTED)
                    time.sleep(2)
                    show_idle_screen(disp)
                    last_interaction_time = time.time()

            if scanner:
                # Use a small timeout on scancode reading to allow the main loop to check for inactivity
                # This depends on how read_scancode is implemented
                barcode = read_scancode(scanner)
            else:
                # For manual input, we can't easily do a non-blocking timeout here without refactoring
                # so we'll just wait for input
                raw = input("Scan: ").strip()
                barcode = decode_manual_input(raw) if raw else ""
            
            # If no barcode was scanned in this loop, just continue
            if not barcode:
                continue

            last_interaction_time = time.time() # Reset timeout on any scan
            
            # Debounce: Ignore identical scans within 0.2 seconds
            current_time = time.time()
            if not barcode or (current_time - last_scan_time) < 0.2:
                continue
            
            last_scan_time = current_time
            print(f"Scanned: {barcode}")
            
            # Handle CANCEL barcode
            if barcode.upper() == CANCEL_BARCODE:
                cart.confirm_state = 0 # Reset confirm state if cancelling
                if cart.is_empty():
                    print("Cart is already empty.")
                    continue
                    
                if cart.cancel_state == 0:
                    cart.cancel_state = 1
                    show_warning_screen(disp, "CANCEL?", "Scan CANCEL again to stop transaction and clear cart")
                    print("First CANCEL received. Scan CANCEL again to stop.")
                else:
                    print("Transaction stopped. Clearing cart.")
                    cart.clear()
                    show_message_screen(disp, "CANCELLED", "Transaction stopped. Cart cleared.", color=(150, 50, 30))
                    time.sleep(2)
                    show_idle_screen(disp)
                continue

            # Handle CONFIRM barcode
            if barcode.upper() == CONFIRM_BARCODE:
                cart.cancel_state = 0 # Reset cancel state if confirming
                if cart.is_empty():
                    print("Cart is empty! Add items first.")
                    continue
                
                if cart.confirm_state == 0:
                    # CHECK CONNECTION BEFORE PROCEEDING
                    print("DEBUG: Checking InvenTree connection...")
                    if not check_inventree_connection():
                        print("ERROR: InvenTree unreachable. Blocking checkout.")
                        show_warning_screen(disp, "OFFLINE", "InvenTree server is not reachable. Cannot checkout.")
                        continue

                    # First confirm - show checkout screen
                    cart.confirm_state = 1
                    show_confirmation_screen(disp, cart)
                    print("First CONFIRM received. Scan CONFIRM again to generate payment QR.")
                
                elif cart.confirm_state == 1:
                    # Second confirm - remove stock and show payment QR
                    print(f"Transaction locked in! Finalizing {len(cart.items)} items...")
                    
                    # Show "PROCESSING" message
                    show_message_screen(disp, "PROCESSING", "Removing items from InvenTree stock...", color=COL_ACCENT2)
                    
                    # Call stock removal API
                    remove_stock_from_inventree(cart)
                    
                    # Show payment QR
                    cart.confirm_state = 2
                    show_payment_qr(disp, cart)
                    print("Payment QR displayed. Scan CONFIRM/CANCEL/ITEM to finish.")
                
                elif cart.confirm_state == 2:
                    # Third confirm (or any scan after QR) - just clear cart and start over
                    print("Session finished. Clearing cart.")
                    cart.clear()
                    show_idle_screen(disp)
                
                continue

            # Handle REMOVE barcode (Undo)
            if barcode.upper() == REMOVE_BARCODE:
                cart.confirm_state = 0
                cart.cancel_state = 0
                if cart.is_empty():
                    print("Cart is already empty.")
                    continue
                
                removed_part = cart.remove_last_item()
                if removed_part:
                    cat = extract_category(removed_part)
                    name = removed_part.get('name', 'Item')
                    print(f"UNDO: Removed {name} [{cat}]")
                    show_message_screen(disp, "UNDO", f"Removed {name}", color=COL_DANGER)
                    time.sleep(1.5)
                    show_item_on_lcd(disp, None, cart) # Return to cart summary
                continue
            
            # Normal item scan
            if cart.confirm_state != 0 or cart.cancel_state != 0:
                # Reset confirmation/cancellation if user scans item
                cart.confirm_state = 0
                cart.cancel_state = 0
                print("Action interrupted. Returning to shopping.")
            
            part = get_item_by_barcode(barcode)
            if part:
                category = extract_category(part)
                cart.add_item(part)
                print(f"Added: {part.get('name')} [{category}] - {format_price(extract_price(part))}")
                print(f"Cart total: {format_price(cart.get_total())}")
                show_item_on_lcd(disp, part, cart)
            else:
                show_item_on_lcd(disp, None, cart)
            
    except KeyboardInterrupt:
        print("\nExiting...")
    finally:
        if HAS_LCD and 'config' in globals() and hasattr(config, 'module_exit'):
            config.module_exit()

if __name__ == "__main__":
    main()
