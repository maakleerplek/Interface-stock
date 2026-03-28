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

## InvenTree Integration

The `barcode_inventree.py` script allows you to scan barcodes and fetch item details (name, price, and image) directly from your InvenTree instance.

1.  **Configure `.env`**:
    The setup automatically creates a `.env` file in the `Interface-stock` directory. Ensure the following variables are set correctly:
    ```env
    INVENTREE_URL=https://10.72.3.141:8443
    INVENTREE_TOKEN=your-api-token
    ```

2.  **Run the scanner**:
    ```bash
    source .venv/bin/activate
    python barcode_inventree.py
    ```
    Scan any barcode assigned to a Part or StockItem in InvenTree. The display will show the item's thumbnail, name, and current price.

## Troubleshooting

### Missing `libopenblas.so.0`
If you see an error about `libopenblas.so.0`, ensure you have run `./install.sh`, which now includes:
```bash
sudo apt-get install libatlas-base-dev libopenblas-dev
```

### SPI Not Enabled
Ensure SPI is enabled in `raspi-config` or that `dtparam=spi=on` is in `/boot/config.txt`.

### Bookworm / Pi 5 Compatibility
For Raspberry Pi OS Bookworm, we use `lgpio`. The `install.sh` and `requirements.txt` are configured to handle this.
