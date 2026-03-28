import os
import sys
import time
import json
import glob
import textwrap
import requests
import threading
from PIL import Image, ImageDraw, ImageFont

# --- Global State & Lock ---
display_lock = threading.Lock()
current_fact = "Fetching first fact..."
current_input = "Waiting for input..."

# --- 1. LCD Configuration (Waveshare 2.4inch) ---
def find_lib_path():
    # Search for 'lcdconfig.py' or 'tp_config.py' which are key parts of the driver
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

if lib_path and os.path.exists(lib_path):
    print(f"Using library base path: {lib_path}")
    sys.path.append(lib_path)
    # Try importing the drivers
    try:
        try:
            from lib import lcdconfig as config
        except ImportError:
            from lib import tp_config as config
        
        try:
            from lib import LCD_2inch4 as LCD
        except ImportError:
            from lib import LCD_2in4 as LCD
        
        HAS_LCD = True
        print("Drivers loaded successfully.")
    except ImportError:
        print("Warning: Drivers found but could not be imported.")
        HAS_LCD = False
else:
    print("Warning: Library path not found. Falling back to console mode.")
    HAS_LCD = False

# --- 2. Logic Functions ---

def fetch_random_info():
    """Fetches a random fact from the Useless Facts API."""
    url = "https://uselessfacts.jsph.pl/random.json?language=en"
    try:
        response = requests.get(url, timeout=5)
        response.raise_for_status()
        return response.json().get("text", "No fact found.")
    except Exception as e:
        return f"Error: {e}"

def update_display(disp):
    """Refreshes the LCD with the latest fact and input state."""
    with display_lock:
        try:
            # Physical display is 240x320 portrait. 
            # We draw as 320x240 landscape and rotate.
            L_WIDTH, L_HEIGHT = 320, 240 # Force logic dimensions
            image = Image.new('RGB', (L_WIDTH, L_HEIGHT), (10, 10, 20))
            draw = ImageDraw.Draw(image)
            
            font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
            header_f = ImageFont.truetype(font_path, 18) if os.path.exists(font_path) else ImageFont.load_default()
            
            # Dynamic font size for the fact to prevent overflow
            body_size = 15 if len(current_fact) < 100 else 13
            body_f = ImageFont.truetype(font_path, body_size) if os.path.exists(font_path) else ImageFont.load_default()
            
            # --- Top Section: Fact (Expanded) ---
            # Move the split line down from 115 to 145
            draw.rectangle([0, 0, L_WIDTH, 145], fill=(20, 30, 60))
            draw.text((15, 10), "RANDOM FACT:", font=header_f, fill=(255, 200, 0))
            
            # Wrap width 32 is safer for ~300 pixels of usable width
            fact_wrapped = textwrap.fill(current_fact, width=34 if body_size == 13 else 30)
            draw.text((15, 35), fact_wrapped, font=body_f, fill=(255, 255, 255))
            
            # --- Bottom Section: Input (Smaller) ---
            draw.rectangle([0, 147, L_WIDTH, L_HEIGHT], fill=(15, 15, 30))
            draw.text((15, 155), "YOUR LAST INPUT:", font=header_f, fill=(0, 255, 150))
            input_wrapped = textwrap.fill(current_input, width=32)
            draw.text((15, 185), input_wrapped, font=body_f, fill=(200, 255, 255))
            
            # Rotate back to the physical screen orientation (Portrait)
            rotated_image = image.rotate(90, expand=True)
            disp.ShowImage(rotated_image)
        except Exception as e:
            print(f"Display Error: {e}")

def fact_worker(disp):
    """Background thread to update facts every 5 seconds."""
    global current_fact
    while True:
        new_fact = fetch_random_info()
        current_fact = new_fact
        if disp:
            update_display(disp)
        time.sleep(5)

# --- 3. Main Loop ---

def main():
    global current_input
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
    
    # Start the fact-fetching thread
    t = threading.Thread(target=fact_worker, args=(disp,), daemon=True)
    t.start()

    print("\n--- Integrated Live Display ---")
    print("Facts update every 5s in background.")
    print("Type anything below to show it on the LCD.")
    print("Press Ctrl+C to exit.\n")

    try:
        while True:
            user_msg = input("Input > ").strip()
            if user_msg:
                current_input = user_msg
                if disp:
                    update_display(disp)
                print(f"[LCD Update] Input set to: {current_input}")
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        if 'config' in globals() and hasattr(config, 'module_exit'):
            config.module_exit()

if __name__ == "__main__":
    main()
