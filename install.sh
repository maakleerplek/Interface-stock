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

echo "--- Checking SPI Status in /boot/config.txt ---"
if grep -q "dtparam=spi=on" /boot/config.txt; then
    echo "SPI is already enabled."
else
    echo "Enabling SPI..."
    echo "dtparam=spi=on" | sudo tee -a /boot/config.txt
    echo "SPI enabled. Please reboot after the script finishes."
fi

echo "--- Downloading Waveshare LCD Drivers ---"
if [ ! -d "lcd_assets" ]; then
    mkdir -p lcd_assets && cd lcd_assets
    wget -O LCD_Module_RPI_code.7z https://files.waveshare.com/upload/8/8d/LCD_Module_RPI_code.7z
    7z x LCD_Module_RPI_code.7z -O./LCD_Module_code
    cd ..
else
    echo "Drivers already downloaded."
fi

echo ""
echo "--- Setup Complete ---"
echo "To run the hello world script, use:"
echo "source .venv/bin/activate && python hello_world.py"
