#!/bin/bash
# update.sh — Pull latest code and restart the scanner service

set -e
INSTALL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE="inventree-scanner"

echo ""
echo "=== Interface-stock Updater ==="
echo ""

# 1. Pull latest code
echo "--- Pulling latest code ---"
git -C "$INSTALL_DIR" pull

# 2. Install/sync Python dependencies (fast no-op if nothing changed)
echo ""
echo "--- Syncing Python dependencies ---"
"$INSTALL_DIR/.venv/bin/pip" install -q -r "$INSTALL_DIR/requirements.txt"
echo "Dependencies up to date."

# 3. Restart service if it's installed, otherwise do nothing
echo ""
if systemctl is-active --quiet "$SERVICE"; then
    echo "--- Restarting $SERVICE ---"
    sudo systemctl restart "$SERVICE"
    echo "Service restarted."
elif systemctl is-enabled --quiet "$SERVICE" 2>/dev/null; then
    echo "--- Starting $SERVICE (was stopped) ---"
    sudo systemctl start "$SERVICE"
    echo "Service started."
else
    echo "--- Service not installed, skipping restart ---"
    echo "Run install.sh first if you need the systemd service."
fi

echo ""
echo "=== Update complete ==="
echo ""
echo "View live logs with:"
echo "  journalctl -u $SERVICE -f"
echo ""
