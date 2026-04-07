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
echo "--- Setting up Auto-Activate Virtual Environment ---"
# Add virtual environment auto-activation to .bashrc
BASHRC="$HOME/.bashrc"
VENV_MARKER="# Interface-stock auto-venv"

if ! grep -q "$VENV_MARKER" "$BASHRC" 2>/dev/null; then
    echo "Adding auto-activation to $BASHRC..."
    cat >> "$BASHRC" << 'EOF'

# Interface-stock auto-venv
# Auto-activate virtual environment for Interface-stock project
if [ -d "$HOME/Interface-stock/.venv" ] && [ -z "$VIRTUAL_ENV" ]; then
    source "$HOME/Interface-stock/.venv/bin/activate"
    echo "✓ Interface-stock virtual environment activated"
fi
EOF
    echo "Auto-activation added to .bashrc"
    echo "Run 'source ~/.bashrc' or open a new terminal to activate."
else
    echo "Auto-activation already configured in .bashrc"
fi

echo ""
echo "--- Setting up InvenTree Shopping System Auto-Start ---"
if [ -f "inventree-scanner.service" ]; then
    echo "Installing systemd service..."
    
    # Get the current directory and user
    INSTALL_DIR=$(pwd)
    CURRENT_USER=$(whoami)
    
    # Create a temporary service file with the correct paths
    sed -e "s|__INSTALL_DIR__|${INSTALL_DIR}|g" \
        -e "s|__USER__|${CURRENT_USER}|g" \
        inventree-scanner.service > /tmp/inventree-scanner.service
    
    # Install the service
    sudo cp /tmp/inventree-scanner.service /etc/systemd/system/inventree-scanner.service
    sudo systemctl daemon-reload
    sudo systemctl enable inventree-scanner.service
    
    # Ask if user wants to start now
    echo ""
    read -p "Do you want to start the shopping system now? (y/n) " -n 1 -r
    echo ""
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        sudo systemctl start inventree-scanner.service
        echo "Service started!"
        echo ""
        echo "View live logs with:"
        echo "sudo journalctl -u inventree-scanner.service -f"
    else
        echo "Service will start automatically on next boot."
        echo "To start manually: sudo systemctl start inventree-scanner.service"
    fi
    
    # Cleanup
    rm -f /tmp/inventree-scanner.service
    
    echo ""
    echo "Service installed and enabled for auto-start on boot."
else
    echo "WARNING: inventree-scanner.service not found. Skipping auto-start setup."
fi

echo ""
echo "--- Setup Complete ---"
echo ""
echo "==================================================================="
echo "  InvenTree Shopping System - Setup Complete"
echo "==================================================================="
echo ""
echo "🎉 Virtual environment will auto-activate on next login!"
echo "   To activate now: source ~/.bashrc"
echo ""
echo "📦 To test demo scripts:"
echo "   python fun/hello_world.py"
echo ""
echo "🛒 To run the shopping system manually:"
echo "   python barcode_inventree.py"
echo ""
echo "🔧 Service management commands:"
echo "   Start:   sudo systemctl start inventree-scanner.service"
echo "   Stop:    sudo systemctl stop inventree-scanner.service"
echo "   Restart: sudo systemctl restart inventree-scanner.service"
echo "   Status:  sudo systemctl status inventree-scanner.service"
echo "   Logs:    sudo journalctl -u inventree-scanner.service -f"
echo ""
echo "⚙️  Disable auto-start:"
echo "   sudo systemctl disable inventree-scanner.service"
echo ""
echo "==================================================================="
echo ""
echo "⚠️  IMPORTANT: Reload your shell to activate venv:"
echo "   source ~/.bashrc"
echo ""
