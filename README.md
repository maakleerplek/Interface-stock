# Waveshare 2.4inch LCD Setup

This repository contains scripts to set up and run a Waveshare 2.4inch LCD module on a Raspberry Pi (verified on Pi 4B and Pi 5 / Bookworm).

## Quick Start

1.  **Clone the repository** (if you haven't already).
2.  **Run the setup script**:
    ```bash
    chmod +x install.sh
    ./install.sh
    ```
    *Note: This script will:*
    - Install system dependencies
    - Create a virtual environment
    - Download Waveshare drivers
    - Set up auto-start service for the shopping system
    - Configure auto-activation of virtual environment
    - Ask if you want to start the service immediately

3.  **Reload your shell** to activate the virtual environment:
    ```bash
    source ~/.bashrc
    # Or simply close and reopen your terminal
    ```
    
    You should see: `✓ Interface-stock virtual environment activated`

4.  **Reboot** (if SPI was not already enabled):
    ```bash
    sudo reboot
    ```
    After reboot, the shopping system will start automatically!

4.  **Run the InvenTree Shopping System**:
    ```bash
    # No need to activate venv - it's automatic!
    python barcode_inventree.py
    ```

5.  **Try the demo scripts** (optional):
    ```bash
    python fun/hello_world.py
    ```
    See `fun/README.md` for more demo scripts!

## Virtual Environment Auto-Activation

The install script automatically configures your shell to activate the virtual environment when you log in. This means you don't need to run `source .venv/bin/activate` every time!

### How it works:
- When you open a terminal, the virtual environment activates automatically
- You'll see: `✓ Interface-stock virtual environment activated`
- You can immediately run Python scripts without manual activation

### Manual control:
```bash
# Deactivate if needed
deactivate

# Reactivate manually
source ~/Interface-stock/.venv/bin/activate
```

### Disable auto-activation:
If you prefer to activate manually, edit `~/.bashrc` and remove the "Interface-stock auto-venv" section.

## InvenTree Shopping System

The `barcode_inventree.py` script is a complete shopping cart system with InvenTree integration and Wero payment support.

### Features

1. **Item Scanning**: Scan barcodes/QR codes to add items to cart
2. **Live Shopping Cart**: See items and running total on the right side of the display
3. **Checkout Flow**: Three-step confirmation process
4. **Wero Payment**: Generate payment QR codes with automatic category detection
5. **Multi-Customer Support**: Automatic cart clearing after payment

### Setup

1. **Configure `.env`**:
   
   The project uses environment variables for sensitive configuration. These are stored as GitHub secrets.
   
   **Option A: Use the setup script** (creates template):
   ```bash
   ./setup_env.sh
   ```
   This creates a `.env` file with placeholders. You'll need to fill in the actual values.
   
   **Option B: Manual setup**:
   ```bash
   cp .env.example .env
   # Edit .env with your credentials
   nano .env
   ```
   
   **Option C: Retrieve from GitHub** (requires access):
   
   If you have repository access, view secrets at:
   ```
   https://github.com/maakleerplek/Interface-stock/settings/secrets/actions
   ```
   
   Or use GitHub CLI:
   ```bash
   # List available secrets
   gh secret list
   
   # Note: GitHub secrets cannot be read for security reasons
   # Contact repository admin for values
   ```
   
   Required variables:
   - `INVENTREE_URL` - InvenTree instance URL
   - `INVENTREE_TOKEN` - API token for authentication
   - `VITE_PAYMENT_NAME` - Makerspace/organization name for payments
   - `VITE_PAYMENT_IBAN` - IBAN for payment QR codes

   Optional variables:
   - `TV_PRESENTATION_URL` - URL of the tv-presentation server (e.g. `http://10.72.3.141:8083`). When set, each successful checkout sends a `POST /api/changelog` event so the TV display shows a live "Recent activity" feed. Leave empty to disable.
     The scanner also drives the TV's inventory pages, which run in Chromium on this same Pi: `PAGE-PREV` and `PAGE-NEXT` (QR codes on the TV) press PageUp/PageDown in that browser with `xdotool`, and every switch between idle and shopping presses F14/F13 (repeated every 30 s as a heartbeat). No server round trip, so the page switches at once. The TV cycles through its pages only while the kiosk is idle. Needs `xdotool` (`sudo apt install xdotool`).

2. **Install dependencies**:
   ```bash
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

3. **Create a CONFIRM barcode**:
   Generate a barcode with the text "CONFIRM" for checkout

### Workflow

1. **Scan items**: Scan product barcodes to add them to the cart
   - Items appear on the left with image and price
   - Shopping cart updates on the right side
   - Items automatically increment if scanned multiple times

2. **First CONFIRM scan**: Shows checkout confirmation screen
   - Displays all items in cart
   - Shows total price
   - Lists each item with quantity and price

3. **Second CONFIRM scan**: Displays Wero payment QR code
   - QR code contains total amount
   - Description includes HTL name and product categories
   - Customer scans with their banking app

4. **Third CONFIRM scan**: Clears cart
   - Transaction complete
   - Ready for next customer
   - Cart resets to empty

### Category-Based Descriptions

The system automatically extracts categories from InvenTree and generates a description for the payment:
- Example: "HTL Makerspace - drink - wood - electronics"
- This helps identify what was purchased in bank statements

## Planned: volunteer badge on an NFC reader

Idea: a volunteer taps their badge at the scanner instead of scanning the shared VOLUNTEER code, so every volunteer drink is booked on a name. Not built yet; no reader has been bought. This is what we found out first (September 2026).

### What works and what doesn't

| Credential | Works with our own reader? | Why |
|---|---|---|
| Salto KS Keychain app on a phone | **No** | The app opens doors over Bluetooth LE, and over NFC only on Android (beta), with Salto's own encrypted protocol that only Salto locks understand. A normal NFC reader gets nothing it can tie to a person, and iPhones don't answer at all. |
| Salto fob or card (MIFARE DESFire) | **Maybe** | Any 13.56 MHz reader can read the chip's serial number (UID) without Salto's keys. That only works if the installation does not use Salto's "random UID" option, which gives a new number on every read. Test with one real fob before buying anything. |
| Our own NFC tags (NTAG215 stickers or cards, a few cents each) | **Yes** | Fixed UID. We hand one to each volunteer and register it once. Works on a keyring or as a sticker on the phone case. |
| Salto KS cloud API (who opened which door) | Not usable here | Only for Salto partners, and it reports door openings, not who is standing at the scanner. |

A UID is an identifier, not a password: anyone who knows it can copy it onto a blank tag. That is fine for "which volunteer took a free drink", not for anything that costs real money.

### How it would plug in

- **Reader:** a USB 13.56 MHz reader in *keyboard mode* (about €15–40, e.g. ACR122U-style or the generic "USB RFID reader 13.56 MHz, keyboard emulation" kind). It types the UID followed by Enter, exactly like the barcode scanner, so `barcode_inventree.py` already receives it through `read_scancode()`. Check that the model reads **DESFire** and **NTAG**, not only MIFARE Classic, and outputs hex.
- **Who is who:** a small table from UID to volunteer name, kept on the server (not in git), for example `volunteers.json` next to `.env`.
- **In the scan loop:** a scanned UID that is in the table works like `VOLUNTEER` (the whole cart is free), and the stock removal note gets the name: `Volunteer drink via Interface-stock (HTL) - <name>`. The analytics already recognise that note; the name would make a "per volunteer" view possible.
- **Unknown UID:** show "Badge not registered" on the LCD and log the UID, so registering a new volunteer is: tap once, copy the UID from the log into the table.
- **Test first:** before building anything, plug the reader into a laptop, open a text editor and tap a Salto fob twice. Two identical hex strings = usable. Different strings or nothing = only our own tags will work.

## Auto-Start on Boot

The installation script automatically sets up a systemd service that runs the shopping system on boot.

### Service Management

```bash
# Start the service
sudo systemctl start inventree-scanner.service

# Stop the service
sudo systemctl stop inventree-scanner.service

# Restart the service
sudo systemctl restart inventree-scanner.service

# Check service status
sudo systemctl status inventree-scanner.service

# View live logs
sudo journalctl -u inventree-scanner.service -f

# Disable auto-start
sudo systemctl disable inventree-scanner.service

# Re-enable auto-start
sudo systemctl enable inventree-scanner.service
```

### Manual Operation

If you prefer to run the system manually without auto-start:

```bash
# Disable the service
sudo systemctl disable inventree-scanner.service
sudo systemctl stop inventree-scanner.service

# Run manually
source .venv/bin/activate
python barcode_inventree.py
```

## Security

### Secrets Management

Sensitive credentials are stored as GitHub repository secrets and should never be committed to the repository. The `.env` file is in `.gitignore` to prevent accidental commits.

**For administrators**: Set secrets using GitHub CLI:
```bash
gh secret set INVENTREE_TOKEN --body "your-token-here"
gh secret set INVENTREE_URL --body "https://your-server:8443"
gh secret set VITE_PAYMENT_NAME --body "Your Organization Name"
gh secret set VITE_PAYMENT_IBAN --body "BE00000000000000"
```

**For users**: Contact repository administrators for access to credentials, or use the setup script which creates a template `.env` file.

### Troubleshooting

### Missing `libopenblas.so.0`
If you see an error about `libopenblas.so.0`, ensure you have run `./install.sh`, which now includes:
```bash
sudo apt-get install libatlas-base-dev libopenblas-dev
```

### SPI Not Enabled
Ensure SPI is enabled in `raspi-config` or that `dtparam=spi=on` is in `/boot/config.txt`.

### Bookworm / Pi 5 Compatibility
For Raspberry Pi OS Bookworm, we use `lgpio`. The `install.sh` and `requirements.txt` are configured to handle this.
