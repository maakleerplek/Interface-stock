#!/bin/bash

# Waveshare 2.4inch LCD Setup Script
set -e

echo "--- Updating system ---"
sudo apt-get update
sudo apt-get install -y python3-venv python3-pip p7zip-full wget libopenjp2-7 libtiff6

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
mkdir -p lcd_assets
cd lcd_assets

if [ ! -f "LCD_Module_RPI_code.7z" ]; then
    echo "Downloading 7z archive..."
    wget -O LCD_Module_RPI_code.7z https://files.waveshare.com/upload/8/8d/LCD_Module_RPI_code.7z || { echo "Download failed!"; exit 1; }
fi

if [ ! -d "LCD_Module_code" ]; then
    echo "Extracting drivers..."
    7z x LCD_Module_RPI_code.7z -O./LCD_Module_code || { echo "Extraction failed!"; exit 1; }
else
    echo "Drivers already extracted."
fi
cd ..

echo ""
echo "--- Directory Check ---"
if [ -f "lcd_assets/LCD_Module_code/LCD_Module_RPI_code/RaspberryPi/python/lib/tp_config.py" ]; then
    echo "SUCCESS: Driver files found."
else
    echo "WARNING: Driver files NOT found in expected location."
    find lcd_assets -name "tp_config.py" || echo "tp_config.py not found anywhere in lcd_assets."
fi

echo ""
echo "--- Setup Complete ---"
echo "To run the hello world script, use:"
echo "source .venv/bin/activate && python hello_world.py"
