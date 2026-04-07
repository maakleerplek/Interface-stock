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
