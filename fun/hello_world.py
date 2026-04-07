import os
import sys
import time
import glob
from PIL import Image, ImageDraw, ImageFont

# 1. Dynamically find the Waveshare library path
def find_lib_path():
    # Search for 'lcdconfig.py' or 'tp_config.py' which are key parts of the driver
    for config_name in ['lcdconfig.py', 'tp_config.py']:
        search_pattern = os.path.join(os.getcwd(), 'lcd_assets', '**', config_name)
        matches = glob.glob(search_pattern, recursive=True)
        if matches:
            # We want the parent of 'lib' to be in sys.path so we can do 'from lib import ...'
            lib_dir = os.path.dirname(matches[0])
            if os.path.basename(lib_dir) == 'lib':
                return os.path.dirname(lib_dir) # Return the 'python' directory
            return lib_dir
    
    # Check root for LCD_Module_code (as seen in user's manual navigation)
    for config_name in ['lcdconfig.py', 'tp_config.py']:
        search_pattern = os.path.join(os.getcwd(), 'LCD_Module_code', '**', config_name)
        matches = glob.glob(search_pattern, recursive=True)
        if matches:
            lib_dir = os.path.dirname(matches[0])
            if os.path.basename(lib_dir) == 'lib':
                return os.path.dirname(lib_dir)
            return lib_dir
    
    # Debug: If not found, list what IS in lcd_assets
    print("Debug: Listing contents of current directory and 'lcd_assets' to find path...")
    for root_dir in ['.', 'lcd_assets', 'LCD_Module_code']:
        if os.path.exists(root_dir):
            print(f"Scanning {root_dir}:")
            for root, dirs, files in os.walk(root_dir):
                level = root.replace(root_dir, '').count(os.sep)
                if level > 4: continue
                if 'lib' in dirs:
                    print(f"  Found 'lib' in {root}")
                if 'lcdconfig.py' in files or 'tp_config.py' in files:
                    print(f"  Found config in {root}")
        
    return None

lib_path = find_lib_path()

if lib_path and os.path.exists(lib_path):
    print(f"Using library base path: {lib_path}")
    sys.path.append(lib_path)
else:
    print("Error: Library path not found. Please ensure you ran ./install.sh successfully.")
    sys.exit(1)

# 2. Try importing the drivers
try:
    from lib import lcdconfig as config
except ImportError:
    try:
        from lib import tp_config as config
    except ImportError:
        print("Error: Could not import configuration (lcdconfig or tp_config).")
        sys.exit(1)

try:
    from lib import LCD_2inch4 as LCD
except ImportError:
    try:
        from lib import LCD_2in4 as LCD
    except ImportError:
        print("Error: Could not import LCD driver (LCD_2inch4 or LCD_2in4).")
        sys.exit(1)

print("Drivers loaded successfully.")

def main():
    try:
        # 3. Initialize the display
        # Note: Depending on the driver version, it might be LCD_2inch4() or LCD_2in4()
        # We use the 'LCD' alias from our import logic above.
        if hasattr(LCD, 'LCD_2inch4'):
            disp = LCD.LCD_2inch4()
        else:
            disp = LCD.LCD_2in4()
            
        disp.Init()
        disp.clear()

        # 4. Create a blank image (RGB)
        width = disp.width
        height = disp.height
        image = Image.new('RGB', (width, height), (0, 0, 0)) # Black background
        draw = ImageDraw.Draw(image)

        # 5. Draw text
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 32)
        except:
            font = ImageFont.load_default()

        text = "Hello World"
        bbox = draw.textbbox((0, 0), text, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        
        x = (width - text_width) // 2
        y = (height - text_height) // 2

        draw.text((x, y), text, font=font, fill=(255, 255, 255)) # White text

        # 6. Display the image
        print("Updating display...")
        disp.ShowImage(image)
        
        time.sleep(5)
        
    except Exception as e:
        print(f"An error occurred: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # 7. Robust cleanup
        if 'config' in globals() or 'config' in locals():
            if hasattr(config, 'module_exit'):
                print("Running config.module_exit()...")
                config.module_exit()
            else:
                print("Warning: config has no 'module_exit' attribute.")
                # Debug: Show available attributes to find the real cleanup
                print(f"Available config attributes: {[attr for attr in dir(config) if not attr.startswith('__')]}")
        
        print("Cleanup done.")

if __name__ == "__main__":
    main()
