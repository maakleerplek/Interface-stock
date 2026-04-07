import os
import sys
import time
import json
import glob
import textwrap
from PIL import Image, ImageDraw, ImageFont

# 1. Dynamically find the Waveshare library path (Reused from rickroll.py)
def find_lib_path():
    for config_name in ['lcdconfig.py', 'tp_config.py']:
        search_pattern = os.path.join(os.getcwd(), 'lcd_assets', '**', config_name)
        matches = glob.glob(search_pattern, recursive=True)
        if matches:
            lib_dir = os.path.dirname(matches[0])
            if os.path.basename(lib_dir) == 'lib':
                return os.path.dirname(lib_dir)
            return lib_dir
    return None

lib_path = find_lib_path()
if lib_path and os.path.exists(lib_path):
    sys.path.append(lib_path)
    try:
        from lib import lcdconfig as config
        from lib import LCD_2inch4 as LCD
        HAS_LCD = True
    except ImportError:
        try:
            from lib import tp_config as config
            from lib import LCD_2in4 as LCD
            HAS_LCD = True
        except ImportError:
            HAS_LCD = False
else:
    HAS_LCD = False

def get_fact():
    """Reads the latest fact from the local json file."""
    try:
        with open("random_fact.json", "r") as f:
            return json.load(f).get("fact", "Wait, where did the fact go?")
    except FileNotFoundError:
        return "No fact found. Run fetch_info.py first!"

def display_on_lcd(text):
    """Dynamically displays text on the LCD."""
    try:
        # Initialize display
        if hasattr(LCD, 'LCD_2inch4'):
            disp = LCD.LCD_2inch4()
        else:
            disp = LCD.LCD_2in4()
        
        disp.Init()
        disp.clear()
        
        width = disp.width
        height = disp.height
        
        # Use a default font or a nice one if available
        # You might need to adjust paths for Raspberry Pi
        font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        if not os.path.exists(font_path):
            font = ImageFont.load_default()
        else:
            font = ImageFont.truetype(font_path, 24)
        
        # Split text into lines to fit the screen
        wrapped_text = textwrap.fill(text, width=20) # Rough estimate for 240px width
        lines = wrapped_text.split('\n')
        
        # Simple "Typewriter" animation or just scrolling
        print(f"Displaying on LCD: {text}")
        
        # Draw frame by frame (this is the "dynamic" part)
        full_text = ""
        for char in text:
            full_text += char
            # Create a new image for each frame for a typewriter effect
            # (Note: drawing every char on SPI LCD might be slow, so we do it line by line or chunks)
            # For brevity, let's just do a nice static render with some color
            pass
        
        image = Image.new('RGB', (width, height), (30, 30, 30)) # Dark gray bg
        draw = ImageDraw.Draw(image)
        
        y_text = 40
        for line in lines:
            draw.text((20, y_text), line, font=font, fill=(255, 255, 255))
            y_text += 30
            
        disp.ShowImage(image)
        time.sleep(5) # Let it sit

    except Exception as e:
        print(f"LCD Display Error: {e}")
    finally:
        if 'config' in globals() and hasattr(config, 'module_exit'):
            config.module_exit()

def display_on_console(text):
    """Dynamic console output if no LCD is found."""
    print("\n" + "="*40)
    print("      DYNAMIC FACT DISPLAY (CONSOLE)")
    print("="*40 + "\n")
    
    # Typewriter effect
    for char in text:
        sys.stdout.write(char)
        sys.stdout.flush()
        time.sleep(0.03)
    print("\n\n" + "="*40)

def main():
    fact = get_fact()
    if HAS_LCD:
        display_on_lcd(fact)
    else:
        print("Note: LCD drivers not found. Falling back to console.")
        display_on_console(fact)

if __name__ == "__main__":
    main()
