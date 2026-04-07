import os
import sys
import time
import glob
from PIL import Image, ImageSequence

# 1. Dynamically find the Waveshare library path
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
else:
    print("Error: Library path not found. Run ./install.sh first.")
    sys.exit(1)

# 2. Try importing the drivers
try:
    from lib import lcdconfig as config
    from lib import LCD_2inch4 as LCD
    print("Drivers loaded.")
except ImportError:
    try:
        from lib import tp_config as config
        from lib import LCD_2in4 as LCD
        print("Drivers loaded.")
    except ImportError:
        print("Error: Could not import configuration or driver.")
        sys.exit(1)

def main():
    try:
        # Initialize display
        if hasattr(LCD, 'LCD_2inch4'):
            disp = LCD.LCD_2inch4()
        else:
            disp = LCD.LCD_2in4()
            
        disp.Init()
        disp.clear()

        # Load GIF
        gif_path = "media/rickroll.gif"
        if not os.path.exists(gif_path):
            print(f"Error: {gif_path} not found. Run ./install.sh first.")
            return

        print(f"Opening GIF: {gif_path}")
        gif = Image.open(gif_path)
        
        width = disp.width
        height = disp.height
        print(f"LCD resolution: {width}x{height}")

        # Pre-process frames for performance
        print("Processing frames...")
        frames = []
        for frame in ImageSequence.Iterator(gif):
            # Convert to RGB and resize to fit LCD
            # Waveshare 2.4inch is typically 240x320
            # We use LANCZOS for better quality, or NEAREST for speed
            rgba_frame = frame.convert('RGBA')
            # Create a black background to handle transparency if any
            bg = Image.new('RGB', (width, height), (0, 0, 0))
            
            # Scale frame to fit either width or height while maintaining aspect ratio
            frame_aspect = rgba_frame.width / rgba_frame.height
            lcd_aspect = width / height
            
            if frame_aspect > lcd_aspect:
                # Frame is wider than LCD aspect
                new_w = width
                new_h = int(width / frame_aspect)
            else:
                # Frame is taller than LCD aspect
                new_h = height
                new_w = int(height * frame_aspect)
            
            resized_frame = rgba_frame.resize((new_w, new_h), Image.Resampling.LANCZOS)
            
            # Paste resized frame onto black background (centered)
            offset = ((width - new_w) // 2, (height - new_h) // 2)
            bg.paste(resized_frame, offset, resized_frame)
            
            frames.append(bg)

        print(f"Playing {len(frames)} frames. Press Ctrl+C to stop.")
        
        while True:
            for frame in frames:
                disp.ShowImage(frame)
                # Small delay to control speed. GIFs usually have frame duration info
                # but for simplicity on RPi SPI we just go as fast as possible or small sleep
                time.sleep(0.01) 

    except KeyboardInterrupt:
        print("\nStopping...")
    except Exception as e:
        print(f"An error occurred: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if 'config' in globals() and hasattr(config, 'module_exit'):
            config.module_exit()
        print("Cleanup done.")

if __name__ == "__main__":
    main()
