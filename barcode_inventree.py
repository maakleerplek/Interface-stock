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
    print("Warning: evdev library not found. Run 'pip install evdev'.")

# Load InvenTree credentials
load_dotenv()
INVENTREE_URL = os.getenv("INVENTREE_URL", "https://10.72.3.68:8443")
INVENTREE_TOKEN = os.getenv("INVENTREE_TOKEN")

# AZERTY Scan Code Map (for evdev)
# This maps Linux evdev scan codes to characters for a Belgian/French AZERTY layout
SCAN_CODES = {
    2: '1', 3: '2', 4: '3', 5: '4', 6: '5', 7: '6', 8: '7', 9: '8', 10: '9', 11: '0',
    ecodes.KEY_ENTER: '\n' if HAS_EVDEV else '\n',
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

    url = f"{INVENTREE_URL}/api/barcode/"
    headers = {"Authorization": f"Token {INVENTREE_TOKEN}"}
    data = {"barcode": barcode}
    
    try:
        response = requests.post(url, data=data, headers=headers, timeout=10, verify=False)
        response.raise_for_status()
        result = response.json()
        if "stockitem" in result: return result["stockitem"].get("part_detail")
        elif "part" in result: return result["part"]
        return None
    except Exception as e:
        print(f"Error fetching from InvenTree: {e}")
        return None

def extract_price(part_detail):
    if not part_detail: return "-"
    for key in ['pricing_min', 'pricing_min_string', 'sell_price']:
        val = part_detail.get(key)
        if val:
            try: return f"EUR {float(val):.2f}"
            except: return str(val)
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
    image = Image.new('RGB', (L_WIDTH, L_HEIGHT), (20, 20, 30))
    draw = ImageDraw.Draw(image)
    font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    header_f = ImageFont.truetype(font_path, 20) if os.path.exists(font_path) else ImageFont.load_default()
    body_f = ImageFont.truetype(font_path, 16) if os.path.exists(font_path) else ImageFont.load_default()
    price_f = ImageFont.truetype(font_path, 24) if os.path.exists(font_path) else ImageFont.load_default()

    if part_detail:
        name = part_detail.get('name', 'Unknown Item')
        price = extract_price(part_detail)
        draw.rectangle([0, 0, L_WIDTH, 40], fill=(40, 60, 100))
        draw.text((10, 8), "ITEM FOUND", font=header_f, fill=(255, 255, 255))
        draw.text((120, 50), textwrap.fill(name, width=25), font=header_f, fill=(255, 200, 0))
        draw.text((120, 140), "PRICE:", font=body_f, fill=(180, 180, 180))
        draw.text((120, 165), price, font=price_f, fill=(0, 255, 100))
        item_img = get_image(part_detail)
        if item_img:
            item_img.thumbnail((100, 100))
            image.paste(item_img, (10, 50))
    else:
        draw.text((50, 100), "ITEM NOT FOUND", font=header_f, fill=(255, 50, 50))

    if HAS_LCD:
        disp.ShowImage(image.rotate(90, expand=True))
    else:
        print(f"\n[DISPLAY] Name: {part_detail.get('name') if part_detail else 'N/A'}")
        print(f"[DISPLAY] Price: {extract_price(part_detail) if part_detail else 'N/A'}")

# --- 4. Scanner Logic ---

def find_scanner():
    if not HAS_EVDEV:
        print("DEBUG: evdev is not available.")
        return None
        
    try:
        device_paths = evdev.list_devices()
        if not device_paths:
            print("DEBUG: No input devices found at all. Are you running as sudo?")
            return None
            
        print(f"DEBUG: Found {len(device_paths)} input devices. Scanning names...")
        for path in device_paths:
            try:
                device = evdev.InputDevice(path)
                name = device.name.lower()
                print(f"DEBUG: Checking device at {path}: '{device.name}'")
                # Broaden search to include generic 'keyboard' if specialized names fail
                if any(x in name for x in ["scanner", "keyboard", "barcode", "hid"]):
                    print(f"MATCH: Using device '{device.name}'")
                    return device
            except PermissionError:
                print(f"DEBUG: Permission denied for device at {path}. Try running with 'sudo'.")
            except Exception as e:
                print(f"DEBUG: Error accessing device at {path}: {e}")
    except Exception as e:
        print(f"DEBUG: Critical error in find_scanner: {e}")
        
    return None

def read_from_evdev(device):
    barcode = ""
    print(f"Listening for hardware scans on {device.name}...")
    for event in device.read_loop():
        if event.type == ecodes.EV_KEY:
            data = evdev.categorize(event)
            if data.keystate == 1: # Key down
                if data.scancode == ecodes.KEY_ENTER:
                    ret = barcode
                    barcode = ""
                    return ret
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

    print("\n--- InvenTree Barcode Scanner (Diagnostics Mode) ---")
    scanner = find_scanner()
    
    if scanner:
        print(f"Auto-detected hardware scanner: {scanner.name}")
    else:
        print("No hardware scanner detected. Falling back to manual terminal input.")
        print("TIP: If your scanner is plugged in, ensure you are using 'sudo'.")

    try:
        while True:
            if scanner:
                try:
                    barcode = read_from_evdev(scanner)
                except Exception as e:
                    print(f"Scanner error: {e}. Falling back to terminal input.")
                    scanner = None
                    continue
            else:
                scanned = input("Scan (Terminal Mode): ").strip()
                if not scanned: continue
                barcode = decode_manual_input(scanned)
            
            if not barcode: continue
            print(f"Processing Barcode: {barcode}")
            part_detail = get_item_by_barcode(barcode)
            show_item_on_lcd(disp, part_detail)
            
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        if HAS_LCD and 'config' in globals() and hasattr(config, 'module_exit'):
            config.module_exit()

if __name__ == "__main__":
    main()
