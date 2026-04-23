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

# Special barcodes for checkout confirmation and cancellation
CONFIRM_BARCODE = "CONFIRM"
CANCEL_BARCODE = "CANCEL"

# AZERTY Scan Code Map (for evdev)
# ... (rest of SCAN_CODES and AZERTY_MAP)
SCAN_CODES = {
    2: '1', 3: '2', 4: '3', 5: '4', 6: '5', 7: '6', 8: '7', 9: '8', 10: '9', 11: '0',
    ecodes.KEY_ENTER: '\n',
}

# Fallback character map for manual terminal input
AZERTY_MAP = {
    '&': '1', 'é': '2', '"': '3', "'": '4', '(': '5',
    '§': '6', 'è': '7', '!': '8', 'ç': '9', 'à': '0',
}

def decode_manual_input(scanned_text):
    return "".join(AZERTY_MAP.get(c, c) for c in scanned_text)

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
                "sell_price": part_detail.get("sell_price"),
                "thumbnail": part_detail.get("thumbnail"),
                "image": part_detail.get("image"),
                "category_detail": part_detail.get("category_detail"),
                "category": part_detail.get("category"),
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
                part = extract_from_obj(res["stockitem"])
            
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
        # Using ?barcode= is an exact filter in InvenTree
        url = f"{INVENTREE_URL}/api/part/?barcode={barcode}"
        response = requests.get(url, headers=headers, timeout=5, verify=False)
        if response.status_code == 200:
            items = response.json()
            results = items if isinstance(items, list) else items.get("results", [])
            for item in results:
                # Extra safety check for exact match
                if item.get("barcode") == barcode or item.get("IPN") == barcode:
                    print(f"DEBUG: Found exact match: {item.get('name')}")
                    BARCODE_CACHE.set(barcode, item)
                    return item
    except Exception as e:
        print(f"DEBUG: Part Field Search Error: {e}")

    # --- Attempt 3: Search StockItem by EXACT Barcode field ---
    print(f"DEBUG: Searching StockItem barcode field for '{barcode}'...")
    try:
        url = f"{INVENTREE_URL}/api/stock/?barcode={barcode}"
        response = requests.get(url, headers=headers, timeout=5, verify=False)
        if response.status_code == 200:
            items = response.json()
            results = items if isinstance(items, list) else items.get("results", [])
            for item in results:
                if item.get("barcode") == barcode:
                    print(f"DEBUG: Found StockItem match, fetching part details")
                    part = item.get("part_detail") or fetch_part_details(item.get("part"))
                    if part:
                        BARCODE_CACHE.set(barcode, part)
                        return part
    except Exception as e:
        print(f"DEBUG: StockItem Field Search Error: {e}")

    return None

def extract_price(part_detail):
    if not part_detail: return 0.0
    # pricing_min is usually the most reliable field
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
    
    # Try to get category_detail first (more detailed)
    cat_detail = part_detail.get('category_detail')
    if cat_detail and isinstance(cat_detail, dict):
        return cat_detail.get('name', 'uncategorized').lower()
    
    # Fall back to category field (might just be an ID)
    category = part_detail.get('category')
    if category and isinstance(category, str):
        return category.lower()
    
    return "uncategorized"

def get_image(part_detail):
    img_path = part_detail.get('thumbnail') or part_detail.get('image')
    if not img_path: return None
    img_url = f"{INVENTREE_URL}{img_path}" if img_path.startswith('/') else img_path
    try:
        headers = {"Authorization": f"Token {INVENTREE_TOKEN}"}
        response = requests.get(img_url, headers=headers, timeout=5, verify=False)
        return Image.open(BytesIO(response.content))
    except: return None

# --- 3. Shopping Cart Management ---

class ShoppingCart:
    def __init__(self):
        self.items = []  # List of (part_detail, quantity)
        self.confirm_state = 0  # 0: normal, 1: awaiting final confirm, 2: show QR
        self.cancel_state = 0   # 0: normal, 1: awaiting final cancel
    
    def add_item(self, part_detail):
        """Add item to cart or increment quantity if already exists"""
        for i, (item, qty) in enumerate(self.items):
            if item.get('pk') == part_detail.get('pk'):
                self.items[i] = (item, qty + 1)
                return
        self.items.append((part_detail, 1))
    
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
        """Generate description for payment (category list)"""
        categories = self.get_categories()
        if not categories:
            return f"{HTL_NAME} - Purchase"
        return f"{HTL_NAME} - " + " - ".join(categories)
    
    def clear(self):
        """Clear the cart and reset states"""
        self.items = []
        self.confirm_state = 0
        self.cancel_state = 0
    
    def is_empty(self):
        return len(self.items) == 0

# --- 4. Display Logic ---

def show_message_screen(disp, title, message, color=(30, 50, 90)):
    """Display a full-screen message"""
    L_WIDTH, L_HEIGHT = 320, 240
    image = Image.new('RGB', (L_WIDTH, L_HEIGHT), (15, 15, 25))
    draw = ImageDraw.Draw(image)
    
    font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    header_f = ImageFont.truetype(font_path, 20) if os.path.exists(font_path) else ImageFont.load_default()
    body_f = ImageFont.truetype(font_path, 14) if os.path.exists(font_path) else ImageFont.load_default()

    # Header / Background
    draw.rectangle([0, 0, L_WIDTH, L_HEIGHT], fill=(15, 15, 25))
    draw.rectangle([0, 40, L_WIDTH, 90], fill=color)
    
    # Center title
    w = draw.textlength(title, font=header_f)
    draw.text(((L_WIDTH - w) / 2, 52), title, font=header_f, fill=(255, 255, 255))
    
    # Center message
    wrapped_msg = textwrap.fill(message, width=30)
    y = 120
    for line in wrapped_msg.split('\n'):
        w = draw.textlength(line, font=body_f)
        draw.text(((L_WIDTH - w) / 2, y), line, font=body_f, fill=(200, 200, 200))
        y += 25

    if HAS_LCD:
        disp.ShowImage(image.rotate(90, expand=True))
    else:
        print(f"\n[{title}] {message}")

def show_warning_screen(disp, title, message):
    show_message_screen(disp, title, message, color=(150, 50, 30))

def show_idle_screen(disp):
    """Display waiting screen"""
    L_WIDTH, L_HEIGHT = 320, 240
    image = Image.new('RGB', (L_WIDTH, L_HEIGHT), (15, 15, 25))
    draw = ImageDraw.Draw(image)
    
    font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    header_f = ImageFont.truetype(font_path, 18) if os.path.exists(font_path) else ImageFont.load_default()
    small_f = ImageFont.truetype(font_path, 12) if os.path.exists(font_path) else ImageFont.load_default()
    
    draw.text((60, 100), "Ready to Scan", font=header_f, fill=(100, 200, 100))
    draw.text((65, 130), f"Makerspace: {HTL_NAME}", font=small_f, fill=(150, 150, 150))
    
    if HAS_LCD:
        disp.ShowImage(image.rotate(90, expand=True))
    else:
        print("\n[IDLE] Ready to Scan")

def show_item_on_lcd(disp, part_detail, cart):
    """Display current scanned item with shopping cart on the side"""
    L_WIDTH, L_HEIGHT = 320, 240
    image = Image.new('RGB', (L_WIDTH, L_HEIGHT), (15, 15, 25))
    draw = ImageDraw.Draw(image)
    
    font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    header_f = ImageFont.truetype(font_path, 18) if os.path.exists(font_path) else ImageFont.load_default()
    body_f = ImageFont.truetype(font_path, 14) if os.path.exists(font_path) else ImageFont.load_default()
    small_f = ImageFont.truetype(font_path, 12) if os.path.exists(font_path) else ImageFont.load_default()
    price_f = ImageFont.truetype(font_path, 22) if os.path.exists(font_path) else ImageFont.load_default()

    # Split view: Left side (200px) for item, Right side (120px) for cart
    ITEM_WIDTH = 200
    CART_X = ITEM_WIDTH
    
    if part_detail:
        name = part_detail.get('name', 'Unknown Item')
        price = extract_price(part_detail)
        
        # Header
        draw.rectangle([0, 0, ITEM_WIDTH, 35], fill=(30, 50, 90))
        draw.text((8, 8), "ITEM SCANNED", font=header_f, fill=(255, 255, 255))
        
        # Item name (wrapped)
        wrapped_name = textwrap.fill(name, width=18)
        draw.text((105, 45), wrapped_name, font=body_f, fill=(255, 215, 0))
        
        # Price
        draw.text((105, 140), "PRICE:", font=small_f, fill=(200, 200, 200))
        draw.text((105, 160), format_price(price), font=price_f, fill=(50, 255, 50))
        
        # Image
        img = get_image(part_detail)
        if img:
            img.thumbnail((90, 90))
            image.paste(img, (8, 45))
        else:
            draw.rectangle([8, 45, 98, 135], outline=(80, 80, 80))
            draw.text((20, 80), "NO\nIMAGE", font=small_f, fill=(80, 80, 80))
    else:
        draw.text((10, 100), "BARCODE NOT\nRECOGNIZED", font=header_f, fill=(255, 80, 80))
    
    # Shopping Cart on the right
    draw.rectangle([CART_X, 0, L_WIDTH, L_HEIGHT], fill=(20, 20, 30))
    draw.rectangle([CART_X, 0, L_WIDTH, 30], fill=(50, 30, 90))
    draw.text((CART_X + 10, 7), "CART", font=body_f, fill=(255, 255, 255))
    
    y_offset = 35
    if cart.is_empty():
        draw.text((CART_X + 15, 100), "Empty", font=small_f, fill=(100, 100, 100))
    else:
        for part, qty in cart.items[:4]:  # Show max 4 items
            item_name = part.get('name', 'Item')
            if len(item_name) > 10:
                item_name = item_name[:10] + "."
            draw.text((CART_X + 5, y_offset), f"{qty}x", font=small_f, fill=(200, 200, 200))
            draw.text((CART_X + 5, y_offset + 15), item_name, font=small_f, fill=(150, 150, 150))
            y_offset += 40
        
        if len(cart.items) > 4:
            draw.text((CART_X + 5, y_offset), f"+{len(cart.items)-4} more", font=small_f, fill=(100, 100, 100))
        
        # Total at bottom
        draw.rectangle([CART_X, L_HEIGHT - 45, L_WIDTH, L_HEIGHT], fill=(30, 60, 30))
        draw.text((CART_X + 5, L_HEIGHT - 40), "TOTAL", font=small_f, fill=(200, 200, 200))
        draw.text((CART_X + 5, L_HEIGHT - 22), format_price(cart.get_total()), font=body_f, fill=(50, 255, 50))

    if HAS_LCD:
        disp.ShowImage(image.rotate(90, expand=True))
    else:
        print(f"\n[DISPLAY] {part_detail.get('name') if part_detail else 'N/A'} - {format_price(extract_price(part_detail))}")
        print(f"[CART] {len(cart.items)} items - Total: {format_price(cart.get_total())}")

def show_confirmation_screen(disp, cart):
    """Show checkout confirmation with all items"""
    L_WIDTH, L_HEIGHT = 320, 240
    image = Image.new('RGB', (L_WIDTH, L_HEIGHT), (15, 15, 25))
    draw = ImageDraw.Draw(image)
    
    font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    header_f = ImageFont.truetype(font_path, 18) if os.path.exists(font_path) else ImageFont.load_default()
    body_f = ImageFont.truetype(font_path, 14) if os.path.exists(font_path) else ImageFont.load_default()
    small_f = ImageFont.truetype(font_path, 12) if os.path.exists(font_path) else ImageFont.load_default()
    
    # Header
    draw.rectangle([0, 0, L_WIDTH, 35], fill=(90, 50, 30))
    draw.text((60, 8), "CHECKOUT", font=header_f, fill=(255, 255, 255))
    
    y_offset = 45
    
    # List items
    for part, qty in cart.items[:5]:  # Show max 5 items
        name = part.get('name', 'Item')
        if len(name) > 20:
            name = name[:20] + "..."
        price = extract_price(part)
        item_total = price * qty
        
        draw.text((10, y_offset), f"{qty}x {name}", font=small_f, fill=(200, 200, 200))
        draw.text((230, y_offset), format_price(item_total), font=small_f, fill=(200, 200, 200))
        y_offset += 25
    
    if len(cart.items) > 5:
        draw.text((10, y_offset), f"+ {len(cart.items) - 5} more items...", font=small_f, fill=(150, 150, 150))
        y_offset += 25
    
    # Total
    draw.rectangle([0, L_HEIGHT - 60, L_WIDTH, L_HEIGHT - 25], fill=(30, 60, 30))
    draw.text((10, L_HEIGHT - 55), "TOTAL:", font=header_f, fill=(255, 255, 255))
    draw.text((180, L_HEIGHT - 55), format_price(cart.get_total()), font=header_f, fill=(50, 255, 50))
    
    # Instruction
    draw.text((30, L_HEIGHT - 18), "Scan CONFIRM again to pay", font=small_f, fill=(255, 215, 0))
    
    if HAS_LCD:
        disp.ShowImage(image.rotate(90, expand=True))
    else:
        print("\n" + "="*40)
        print("CHECKOUT CONFIRMATION")
        print("="*40)
        for part, qty in cart.items:
            name = part.get('name', 'Item')
            price = extract_price(part)
            print(f"{qty}x {name} - {format_price(price * qty)}")
        print("-"*40)
        print(f"TOTAL: {format_price(cart.get_total())}")
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
    """Display Wero payment QR code"""
    L_WIDTH, L_HEIGHT = 320, 240
    image = Image.new('RGB', (L_WIDTH, L_HEIGHT), (15, 15, 25))
    draw = ImageDraw.Draw(image)
    
    font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    header_f = ImageFont.truetype(font_path, 18) if os.path.exists(font_path) else ImageFont.load_default()
    small_f = ImageFont.truetype(font_path, 12) if os.path.exists(font_path) else ImageFont.load_default()
    
    # Header
    draw.rectangle([0, 0, L_WIDTH, 35], fill=(30, 90, 50))
    draw.text((70, 8), "WERO PAYMENT", font=header_f, fill=(255, 255, 255))
    
    # Generate and display QR code
    total = cart.get_total()
    description = cart.get_description()
    qr_img = generate_wero_qr(total, description)
    
    # Resize QR to fit
    qr_img = qr_img.resize((160, 160))
    image.paste(qr_img, (80, 45))
    
    # Amount and description
    draw.text((100, 210), format_price(total), font=header_f, fill=(50, 255, 50))
    
    # Categories
    cats = " - ".join(cart.get_categories())
    if len(cats) > 30:
        cats = cats[:30] + "..."
    draw.text((10, 230), cats, font=small_f, fill=(200, 200, 200))
    
    if HAS_LCD:
        disp.ShowImage(image.rotate(90, expand=True))
    else:
        print("\n" + "="*40)
        print("WERO PAYMENT QR CODE")
        print("="*40)
        print(f"Amount: {format_price(total)}")
        print(f"Description: {description}")
        print("Scan QR code with your banking app")
        print("="*40)

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

def read_scancode(device):
    barcode = ""
    for event in device.read_loop():
        if event.type == ecodes.EV_KEY:
            data = evdev.categorize(event)
            if data.keystate == 1: # Key Down
                if data.scancode == ecodes.KEY_ENTER:
                    res = barcode
                    barcode = ""
                    if res: return res
                else:
                    char = SCAN_CODES.get(data.scancode, "")
                    barcode += char
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
    
    # Show initial screen
    show_idle_screen(disp)
    
    try:
        while True:
            if scanner:
                barcode = read_scancode(scanner)
            else:
                raw = input("Scan: ").strip()
                barcode = decode_manual_input(raw) if raw else ""
            
            # Debounce: Ignore identical scans within 0.5 seconds
            current_time = time.time()
            if not barcode or (current_time - last_scan_time) < 0.5:
                continue
            
            last_scan_time = current_time
            print(f"Scanned: {barcode}")
            
            # Handle CANCEL barcode
            if barcode == CANCEL_BARCODE:
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
            if barcode == CONFIRM_BARCODE:
                cart.cancel_state = 0 # Reset cancel state if confirming
                if cart.is_empty():
                    print("Cart is empty! Add items first.")
                    continue
                
                if cart.confirm_state == 0:
                    # First confirm - show checkout screen
                    cart.confirm_state = 1
                    show_confirmation_screen(disp, cart)
                    print("First CONFIRM received. Scan CONFIRM again to generate payment QR.")
                
                elif cart.confirm_state == 1:
                    # Second confirm - show payment QR
                    cart.confirm_state = 2
                    show_payment_qr(disp, cart)
                    print("Payment QR displayed. Scan CONFIRM/CANCEL/ITEM to start new session.")
                
                elif cart.confirm_state == 2:
                    # Third confirm (or any scan after QR) - clear cart and start over
                    print(f"Transaction complete! Cleared {len(cart.items)} items.")
                    cart.clear()
                    show_idle_screen(disp)
                
                continue
            
            # Normal item scan
            if cart.confirm_state != 0 or cart.cancel_state != 0:
                # Reset confirmation/cancellation if user scans item
                cart.confirm_state = 0
                cart.cancel_state = 0
                print("Action interrupted. Returning to shopping.")
            
            part = get_item_by_barcode(barcode)
            if part:
                cart.add_item(part)
                print(f"Added: {part.get('name')} - {format_price(extract_price(part))}")
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
