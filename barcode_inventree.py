import os
import sys
import time
import glob
import textwrap
import requests
import json
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont
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

# AZERTY Scan Code Map (for evdev)
# Maps Linux scancodes (1-0 row) to digits for AZERTY layout
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

# --- 2. InvenTree API Functions ---

def get_item_by_barcode(barcode):
    if not INVENTREE_TOKEN:
        print("Error: INVENTREE_TOKEN not configured")
        return None

    headers = {"Authorization": f"Token {INVENTREE_TOKEN}"}
    
    # --- Attempt 1: Barcode API (Direct Match) ---
    print(f"DEBUG: Checking Barcode API for '{barcode}'...")
    url = f"{INVENTREE_URL}/api/barcode/"
    try:
        response = requests.post(url, data={"barcode": barcode}, headers=headers, timeout=5, verify=False)
        if response.status_code == 200:
            res = response.json()
            if "stockitem" in res:
                print("DEBUG: Found StockItem via Barcode API")
                return res["stockitem"].get("part_detail")
            if "part" in res:
                print("DEBUG: Found Part via Barcode API")
                return res["part"]
    except Exception as e:
        print(f"DEBUG: Barcode API Error: {e}")

    # --- Attempt 2: Search Part by Barcode field ---
    print(f"DEBUG: Searching Part field for '{barcode}'...")
    try:
        url = f"{INVENTREE_URL}/api/part/?barcode={barcode}"
        response = requests.get(url, headers=headers, timeout=5, verify=False)
        if response.status_code == 200:
            items = response.json()
            results = items if isinstance(items, list) else items.get("results", [])
            if results:
                print("DEBUG: Found Part via Barcode field search")
                return results[0]
    except Exception as e:
        print(f"DEBUG: Part Field Search Error: {e}")

    # --- Attempt 3: General Search (Tv-Presentation style) ---
    # Sometimes barcodes are in 'IPN' or 'keywords'
    print(f"DEBUG: Trying General Part search for '{barcode}'...")
    try:
        url = f"{INVENTREE_URL}/api/part/?search={barcode}"
        response = requests.get(url, headers=headers, timeout=5, verify=False)
        if response.status_code == 200:
            items = response.json()
            results = items if isinstance(items, list) else items.get("results", [])
            if results:
                print("DEBUG: Found Part via General search")
                return results[0]
    except Exception as e:
        print(f"DEBUG: General Search Error: {e}")

    return None

def extract_price(part_detail):
    if not part_detail: return "-"
    # Match Tv-Presentation logic
    if part_detail.get('pricing_min'):
        return f"EUR {float(part_detail['pricing_min']):.2f}"
    if part_detail.get('pricing_min_string'):
        return part_detail['pricing_min_string']
    if part_detail.get('sell_price'):
        return f"EUR {float(part_detail['sell_price']):.2f}"
    return "-"

def get_image(part_detail):
    img_path = part_detail.get('thumbnail') or part_detail.get('image')
    if not img_path: return None
    img_url = f"{INVENTREE_URL}{img_path}" if img_path.startswith('/') else img_path
    try:
        headers = {"Authorization": f"Token {INVENTREE_TOKEN}"}
        response = requests.get(img_url, headers=headers, timeout=5, verify=False)
        return Image.open(BytesIO(response.content))
    except: return None

# --- 3. Display Logic ---

def show_item_on_lcd(disp, part_detail):
    L_WIDTH, L_HEIGHT = 320, 240
    image = Image.new('RGB', (L_WIDTH, L_HEIGHT), (15, 15, 25))
    draw = ImageDraw.Draw(image)
    
    font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    header_f = ImageFont.truetype(font_path, 20) if os.path.exists(font_path) else ImageFont.load_default()
    body_f = ImageFont.truetype(font_path, 16) if os.path.exists(font_path) else ImageFont.load_default()
    price_f = ImageFont.truetype(font_path, 26) if os.path.exists(font_path) else ImageFont.load_default()

    if part_detail:
        name = part_detail.get('name', 'Unknown Item')
        price = extract_price(part_detail)
        
        draw.rectangle([0, 0, L_WIDTH, 45], fill=(30, 50, 90))
        draw.text((12, 10), "ITEM IDENTIFIED", font=header_f, fill=(255, 255, 255))
        
        draw.text((120, 60), textwrap.fill(name, width=22), font=header_f, fill=(255, 215, 0))
        draw.text((120, 150), "UNIT PRICE:", font=body_f, fill=(200, 200, 200))
        draw.text((120, 175), price, font=price_f, fill=(50, 255, 50))
        
        img = get_image(part_detail)
        if img:
            img.thumbnail((100, 100))
            image.paste(img, (10, 60))
        else:
            draw.rectangle([10, 60, 110, 160], outline=(80, 80, 80))
            draw.text((25, 100), "NO IMAGE", font=body_f, fill=(80, 80, 80))
    else:
        draw.text((60, 110), "BARCODE NOT RECOGNIZED", font=header_f, fill=(255, 80, 80))

    if HAS_LCD:
        disp.ShowImage(image.rotate(90, expand=True))
    else:
        print(f"\n[DISPLAY] {part_detail.get('name') if part_detail else 'N/A'} - {extract_price(part_detail)}")

# --- 4. Main ---

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
            if data.keystate == 1:
                if data.scancode == ecodes.KEY_ENTER: return barcode
                barcode += SCAN_CODES.get(data.scancode, "")
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
    print("\n--- InvenTree Scanner ---")
    if scanner: print(f"Hardware: {scanner.name}")
    else: print("Mode: Terminal Input")

    try:
        while True:
            if scanner:
                barcode = read_scancode(scanner)
            else:
                raw = input("Scan: ").strip()
                barcode = decode_manual_input(raw) if raw else ""
            
            if barcode:
                print(f"Querying: {barcode}")
                part = get_item_by_barcode(barcode)
                show_item_on_lcd(disp, part)
    except KeyboardInterrupt:
        print("\nExiting...")
    finally:
        if HAS_LCD and 'config' in globals() and hasattr(config, 'module_exit'):
            config.module_exit()

if __name__ == "__main__":
    main()
