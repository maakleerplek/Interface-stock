import os
import sys
import time
import json
import glob
import textwrap
import requests
from PIL import Image, ImageDraw, ImageFont

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
        return f"Error fetching: {e}"

def display_on_lcd(disp, text):
    """Renders text to the LCD."""
    try:
        width, height = disp.width, disp.height
        image = Image.new('RGB', (width, height), (20, 20, 40)) # Dark blue bg
        draw = ImageDraw.Draw(image)
        
        # Font selection
        font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        font = ImageFont.truetype(font_path, 20) if os.path.exists(font_path) else ImageFont.load_default()
        
        wrapped_text = textwrap.fill(text, width=22)
        draw.text((10, 40), "FACT UPDATE:", font=font, fill=(255, 200, 0))
        draw.text((10, 80), wrapped_text, font=font, fill=(255, 255, 255))
        
        disp.ShowImage(image)
    except Exception as e:
        print(f"LCD Error: {e}")

def display_on_console(text):
    """Dynamic console output."""
    os.system('clear' if os.name == 'posix' else 'cls')
    print("\n" + "="*50)
    print("      LIVE INFO FEED (Updating every 5s)")
    print("="*50 + "\n")
    print(text)
    print("\n" + "="*50)

# --- 3. Main Loop ---

def main():
    disp = None
    if HAS_LCD:
        if hasattr(LCD, 'LCD_2inch4'):
            disp = LCD.LCD_2inch4()
        else:
            disp = LCD.LCD_2in4()
        disp.Init()
        disp.clear()
        print("LCD initialized.")
    else:
        print("LCD not found, using console mode.")

    print("Starting loop. Press Ctrl+C to stop.")
    try:
        while True:
            fact = fetch_random_info()
            
            if HAS_LCD and disp:
                display_on_lcd(disp, fact)
            
            display_on_console(fact)
            
            time.sleep(5)
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        if 'config' in globals() and hasattr(config, 'module_exit'):
            config.module_exit()

if __name__ == "__main__":
    main()
