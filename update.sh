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

# 4. Register the daily auto-update timer if not already installed
if ! systemctl is-enabled --quiet interface-stock-update.timer 2>/dev/null; then
    echo ""
    echo "--- Installing daily auto-update timer (01:00) ---"
    CURRENT_USER=$(whoami)
    sed -e "s|__INSTALL_DIR__|${INSTALL_DIR}|g" \
        -e "s|__USER__|${CURRENT_USER}|g" \
        "$INSTALL_DIR/systemd/auto-update.service" \
        | sudo tee /etc/systemd/system/interface-stock-update.service > /dev/null
    sudo cp "$INSTALL_DIR/systemd/auto-update.timer" /etc/systemd/system/interface-stock-update.timer
    sudo systemctl daemon-reload
    sudo systemctl enable --now interface-stock-update.timer
    echo "Timer installed: will auto-update daily at 01:00."
fi

# 5. Chromium's start script (flags) lives in /opt; copy it when it changed and
#    restart Chromium so the flags apply (FullPageOS starts it again).
SRC="$INSTALL_DIR/scripts/start_chromium_browser"
DST=/opt/custompios/scripts/start_chromium_browser
if [ -f "$DST" ] && ! cmp -s "$SRC" "$DST"; then
    echo ""
    echo "--- Updating Chromium start script ---"
    sudo cp "$SRC" "$DST" && sudo chmod +x "$DST"
    pkill -x chromium || true
fi

# 6. Install or refresh the kiosk watchdog (blank TV page, Wi-Fi, full disk)
echo ""
echo "--- Kiosk watchdog ---"
bash "$INSTALL_DIR/scripts/install-watchdog.sh"

echo ""
echo "=== Update complete ==="
echo ""
echo "View live logs with:"
echo "  journalctl -u $SERVICE -f"
echo ""
