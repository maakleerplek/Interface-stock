import os
import sys
import time
import glob
import textwrap
import requests
import json
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont, ImageSequence
from dotenv import load_dotenv

# Load InvenTree credentials
load_dotenv()
INVENTREE_URL = os.getenv("INVENTREE_URL", "https://10.72.3.141:8443")
INVENTREE_TOKEN = os.getenv("INVENTREE_TOKEN")

# Mapping for Belgian/French AZERTY characters to digits (Lower & Upper case)
AZERTY_MAP = {
    '&': '1', 'é': '2', '"': '3', "'": '4', '(': '5',
    '§': '6', 'è': '7', '!': '8', 'ç': '9', 'à': '0',
    '1': '1', '2': '2', '3': '3', '4': '4', '5': '5',
    '6': '6', '7': '7', '8': '8', '9': '9', '0': '0',
    'À': '0', 'É': '2', 'È': '7', 'Ç': '9', '§': '6',
}

def decode_barcode(scanned_text):
    return "".join(AZERTY_MAP.get(c, c) for c in scanned_text)

# --- 1. LCD Configuration (Waveshare 2.4inch) ---
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
    print(f"Using library base path: {lib_path}")
    sys.path.append(lib_path)
    try:
        from lib import lcdconfig as config
        try:
            from lib import LCD_2inch4 as LCD
        except ImportError:
            from lib import LCD_2in4 as LCD
        HAS_LCD = True
        print("Drivers loaded successfully.")
    except ImportError:
        print("Warning: Drivers found but could not be imported.")
else:
    print("Warning: Library path not found. Console mode only.")

# --- 2. InvenTree API Functions ---

def get_item_by_barcode(barcode):
    if not INVENTREE_TOKEN:
        print("Error: INVENTREE_TOKEN not configured in .env")
        return None

    url = f"{INVENTREE_URL}/api/barcode/"
    headers = {"Authorization": f"Token {INVENTREE_TOKEN}"}
    data = {"barcode": barcode}
    
    try:
        # verify=False due to self-signed certs often used in local InvenTree
        response = requests.post(url, data=data, headers=headers, timeout=10, verify=False)
        response.raise_for_status()
        result = response.json()
        
        # InvenTree returns part, stockitem, location, or stockbin
        if "stockitem" in result:
            return result["stockitem"].get("part_detail")
        elif "part" in result:
            return result["part"]
        else:
            print(f"No part or stockitem found for barcode {barcode}")
            return None
    except Exception as e:
        print(f"Error fetching from InvenTree: {e}")
        return None

def extract_price(part_detail):
    if not part_detail: return "-"
    pricing_min = part_detail.get('pricing_min')
    if pricing_min:
        try:
            return f"EUR {float(pricing_min):.2f}"
        except: pass
    pricing_min_string = part_detail.get('pricing_min_string')
    if pricing_min_string: return pricing_min_string
    sell_price = part_detail.get('sell_price')
    if sell_price:
        try:
            return f"EUR {float(sell_price):.2f}"
        except: pass
    description = part_detail.get('description', '')
    name = part_detail.get('name', '')
    if description and description.lower() != name.lower():
        return description
    return "-"

def get_image(part_detail):
    img_path = part_detail.get('thumbnail') or part_detail.get('image')
    if not img_path: return None
    
    if img_path.startswith('/'):
        img_url = f"{INVENTREE_URL}{img_path}"
    else:
        img_url = img_path
        
    try:
        headers = {"Authorization": f"Token {INVENTREE_TOKEN}"}
        response = requests.get(img_url, headers=headers, timeout=5, verify=False)
        response.raise_for_status()
        return Image.open(BytesIO(response.content))
    except Exception as e:
        print(f"Error fetching image: {e}")
        return None

# --- 3. Display Logic ---

def show_item_on_lcd(disp, part_detail):
    # 320x240 landscape (physically 240x320 portrait)
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
        
        # Draw Backgrounds
        draw.rectangle([0, 0, L_WIDTH, 40], fill=(40, 60, 100)) # Header
        draw.text((10, 8), "ITEM FOUND", font=header_f, fill=(255, 255, 255))
        
        # Name
        name_wrapped = textwrap.fill(name, width=25)
        draw.text((120, 50), name_wrapped, font=header_f, fill=(255, 200, 0))
        
        # Price
        draw.text((120, 140), "PRICE:", font=body_f, fill=(180, 180, 180))
        draw.text((120, 165), price, font=price_f, fill=(0, 255, 100))
        
        # Image (Thumbnail)
        item_img = get_image(part_detail)
        if item_img:
            item_img.thumbnail((100, 100))
            image.paste(item_img, (10, 50))
        else:
            draw.rectangle([10, 50, 110, 150], outline=(100, 100, 100))
            draw.text((25, 90), "NO IMG", font=body_f, fill=(100, 100, 100))
    else:
        draw.text((50, 100), "ITEM NOT FOUND", font=header_f, fill=(255, 50, 50))

    if HAS_LCD:
        rotated_image = image.rotate(90, expand=True)
        disp.ShowImage(rotated_image)
    else:
        print(f"\n[DISPLAY] Name: {part_detail.get('name') if part_detail else 'N/A'}")
        print(f"[DISPLAY] Price: {extract_price(part_detail) if part_detail else 'N/A'}")

def main():
    disp = None
    if HAS_LCD:
        try:
            if hasattr(LCD, 'LCD_2inch4'):
                disp = LCD.LCD_2inch4()
            else:
                disp = LCD.LCD_2in4()
            disp.Init()
            disp.clear()
            print("LCD initialized.")
        except Exception as e:
            print(f"LCD Init failed: {e}")
            disp = None

    print("\n--- InvenTree Barcode Scanner ---")
    print("Ready for scan. Scan a barcode to fetch item details.")
    print("Press Ctrl+C to exit.\n")

    # Suppress InsecureRequestWarning for self-signed certs
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    try:
        while True:
            scanned = input("Scan: ").strip()
            if not scanned: continue
            
            barcode = decode_barcode(scanned)
            print(f"Scanned: {barcode}")
            
            part_detail = get_item_by_barcode(barcode)
            show_item_on_lcd(disp, part_detail)
            
            if part_detail:
                print(f"Success: {part_detail.get('name')} found.")
            else:
                print("Error: Barcode not recognized by InvenTree.")
                
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        if HAS_LCD and 'config' in globals() and hasattr(config, 'module_exit'):
            config.module_exit()

if __name__ == "__main__":
    main()
