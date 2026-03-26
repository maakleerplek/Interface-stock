import tkinter as tk
from PIL import Image, ImageTk, ImageSequence
import os
import sys

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

def show_rickroll():
    gif_path = "media/rickroll.gif"
    if not os.path.exists(gif_path):
        print("Error: media/rickroll.gif not found.")
        return

    root = tk.Tk()
    root.title("SYSTEM ALERT: BARCODE RECOGNIZED")
    # Make it fullscreen or big for effect
    root.attributes('-topmost', True)

    img = Image.open(gif_path)
    frames = [ImageTk.PhotoImage(f.copy().convert('RGBA')) for f in ImageSequence.Iterator(img)]

    label = tk.Label(root, bg="black")
    label.pack(expand=True, fill="both")

    def update(idx):
        label.configure(image=frames[idx])
        root.after(50, update, (idx + 1) % len(frames))

    update(0)
    root.bind('<Escape>', lambda e: root.destroy())
    print("\n[!] RICKROLL ACTIVATED. Press Esc to close.")
    root.mainloop()

def main():
    print("--- Barcode Scanner Decoder & Rickroll Trigger ---")
    print("Scan a barcode (it will be decoded automatically).")
    print("Trigger barcode: 541022814235")
    print("Press Ctrl+C to exit.")
    
    try:
        while True:
            # Barcode scanners usually end with a 'Return' key
            scanned = input("\nReady for scan: ").strip()
            if not scanned:
                continue
                
            decoded = decode_barcode(scanned)
            print(f"Scanned (Raw): {scanned}")
            print(f"Decoded:       {decoded}")

            if decoded == "541022814235":
                show_rickroll()
            else:
                print("Status: Barcode not recognized.")
    except KeyboardInterrupt:
        print("\nExiting...")

if __name__ == "__main__":
    main()
