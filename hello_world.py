import os
import sys
import time
import glob
from PIL import Image, ImageDraw, ImageFont

# 1. Dynamically find the Waveshare library path
def find_lib_path():
    # Search for 'tp_config.py' which is a key part of the driver
    search_pattern = os.path.join(os.getcwd(), 'lcd_assets', '**', 'tp_config.py')
    matches = glob.glob(search_pattern, recursive=True)
    
    if matches:
        return os.path.dirname(matches[0])
    return None

lib_path = find_lib_path()

if lib_path and os.path.exists(lib_path):
    print(f"Using library path: {lib_path}")
    sys.path.append(lib_path)
else:
    print("Error: Library path not found. Please ensure you ran ./install.sh successfully.")
    sys.exit(1)

# 2. Try importing the drivers (handling common naming variations)
try:
    from tp_config import config
    try:
        import LCD_2inch4 as LCD
    except ImportError:
        import LCD_2in4 as LCD
    
    print("Drivers loaded successfully.")
except ImportError as e:
    print(f"Error: Could not import Waveshare drivers. Details: {e}")
    sys.exit(1)

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
    finally:
        config.module_exit()
        print("Cleanup done.")

if __name__ == "__main__":
    main()
