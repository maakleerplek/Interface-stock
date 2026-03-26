import os
import sys
import time
from PIL import Image, ImageDraw, ImageFont

# Path to the Waveshare libraries
lib_path = os.path.join(os.getcwd(), 'lcd_assets/LCD_Module_code/LCD_Module_RPI_code/RaspberryPi/python/lib')
if os.path.exists(lib_path):
    sys.path.append(lib_path)
else:
    print(f"Error: Library path {lib_path} not found. Run install.sh first.")
    sys.exit(1)

try:
    from tp_config import config
    import LCD_2inch4
except ImportError:
    print("Error: Could not import Waveshare drivers from the lib folder.")
    sys.exit(1)

def main():
    try:
        # 1. Initialize the display
        disp = LCD_2inch4.LCD_2inch4()
        disp.Init()
        disp.clear()

        # 2. Create a blank image (RGB)
        # Dimensions for 2.4inch LCD are 240x320
        width = disp.width
        height = disp.height
        image = Image.new('RGB', (width, height), (0, 0, 0)) # Black background
        draw = ImageDraw.Draw(image)

        # 3. Draw text
        # Use default font or try to load a system font
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 32)
        except:
            font = ImageFont.load_default()

        text = "Hello World"
        # Calculate text size to center it
        bbox = draw.textbbox((0, 0), text, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        
        x = (width - text_width) // 2
        y = (height - text_height) // 2

        draw.text((x, y), text, font=font, fill=(255, 255, 255)) # White text

        # 4. Display the image
        print("Updating display...")
        disp.ShowImage(image)
        
        # Keep image on screen for a while
        time.sleep(5)
        
    except IOError as e:
        print(f"IO Error: {e}")
    except KeyboardInterrupt:
        print("Stopping...")
    finally:
        # Module exit (cleanup GPIO/SPI)
        config.module_exit()
        print("Cleanup done.")

if __name__ == "__main__":
    main()
