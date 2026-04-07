# Waveshare 2.4inch LCD Setup

This repository contains scripts to set up and run a Waveshare 2.4inch LCD module on a Raspberry Pi (verified on Pi 4B and Pi 5 / Bookworm).

## Quick Start

1.  **Clone the repository** (if you haven't already).
2.  **Run the setup script**:
    ```bash
    chmod +x install.sh
    ./install.sh
    ```
    *Note: This script will install system dependencies, create a virtual environment, and download the necessary Waveshare drivers.*

3.  **Reboot** (if SPI was not already enabled):
    ```bash
    sudo reboot
    ```

4.  **Run the Hello World example**:
    ```bash
    source .venv/bin/activate
    python hello_world.py
    ```

5.  **Run the InvenTree Barcode Scanner (New!)**:
    ```bash
    source .venv/bin/activate
    python barcode_inventree.py
    ```

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
