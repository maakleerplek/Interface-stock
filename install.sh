#!/bin/bash

# Waveshare 2.4inch LCD Setup Script
set -e

echo "--- Updating system ---"
sudo apt-get update
sudo apt-get install -y python3-venv python3-pip p7zip-full wget libopenjp2-7 libtiff6 libatlas-base-dev libopenblas-dev python3-lgpio python3-tk

echo "--- Creating Virtual Environment ---"
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
    echo "Virtual environment created."
fi

echo "--- Installing Python dependencies ---"
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

echo "--- Checking SPI Status ---"
if grep -q "dtparam=spi=on" /boot/config.txt; then
    echo "SPI is already enabled in /boot/config.txt."
else
    echo "Enabling SPI in /boot/config.txt..."
    echo "dtparam=spi=on" | sudo tee -a /boot/config.txt
    echo "IMPORTANT: SPI enabled. You MUST reboot after this script finishes."
fi

echo "--- Downloading Waveshare LCD Drivers ---"
# We'll keep the archive in the root or a dedicated folder, but extract to lcd_assets
if [ ! -f "LCD_Module_RPI_code.7z" ]; then
    echo "Downloading 7z archive..."
    wget -O LCD_Module_RPI_code.7z https://files.waveshare.com/upload/8/8d/LCD_Module_RPI_code.7z || { echo "Download failed!"; exit 1; }
fi

if [ ! -d "lcd_assets/LCD_Module_code" ]; then
    echo "Extracting drivers to lcd_assets/LCD_Module_code..."
    mkdir -p lcd_assets
    7z x LCD_Module_RPI_code.7z -O./lcd_assets/LCD_Module_code || { echo "Extraction failed!"; exit 1; }
else
    echo "Drivers already extracted in lcd_assets/LCD_Module_code."
fi

echo "--- Downloading Media ---"
mkdir -p media


echo ""
echo "--- Directory Check ---"
# Updated check path to match extraction
if find lcd_assets -name "LCD_2inch4.py" | grep -q "."; then
    echo "SUCCESS: Driver files found."
else
    echo "WARNING: Driver files NOT found in expected location."
    find lcd_assets -name "*.py" | head -n 5 || echo "No python files found in lcd_assets."
fi

echo ""
echo "--- Setting up InvenTree Scanner Auto-Start ---"
if [ -f "inventree-scanner.service" ]; then
    echo "Installing systemd service..."
    # Ensure paths in the service file are correct for the current directory
    # (assuming we are in /home/pi/Interface-stock)
    sudo cp inventree-scanner.service /etc/systemd/system/
    sudo systemctl daemon-reload
    sudo systemctl enable inventree-scanner.service
    echo "Service enabled. It will start on boot."
else
    echo "WARNING: inventree-scanner.service not found. Skipping auto-start setup."
fi

echo ""
echo "--- Setup Complete ---"
echo "To run the hello world script, use:"
echo "source .venv/bin/activate && python hello_world.py"
echo ""
echo "To manually start the InvenTree scanner service:"
echo "sudo systemctl start inventree-scanner.service"
echo ""
echo "To check scanner logs:"
echo "sudo journalctl -u inventree-scanner.service -f"
