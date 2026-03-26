#!/bin/bash

# Waveshare 2.4inch LCD Setup Script
set -e

echo "--- Updating system ---"
sudo apt-get update

echo "--- Installing Python dependencies ---"
sudo apt-get install -y python3-pip python3-pil python3-numpy p7zip-full wget
sudo pip3 install RPi.GPIO spidev --break-system-packages || sudo pip3 install RPi.GPIO spidev

echo "--- Checking SPI Status in /boot/config.txt ---"
if grep -q "dtparam=spi=on" /boot/config.txt; then
    echo "SPI is already enabled."
else
    echo "Enabling SPI..."
    sudo sed -i 's/#dtparam=spi=on/dtparam=spi=on/' /boot/config.txt
    if ! grep -q "dtparam=spi=on" /boot/config.txt; then
        echo "dtparam=spi=on" | sudo tee -a /boot/config.txt
    fi
    echo "SPI enabled. Please reboot after the script finishes."
fi

echo "--- Downloading Waveshare LCD Examples ---"
mkdir -p lcd_assets && cd lcd_assets
wget -O LCD_Module_RPI_code.7z https://files.waveshare.com/upload/8/8d/LCD_Module_RPI_code.7z
7z x LCD_Module_RPI_code.7z -O./LCD_Module_code

echo "--- Setup Complete ---"
echo "Drivers are located in: $(pwd)/LCD_Module_code/LCD_Module_RPI_code/RaspberryPi/python/lib"
