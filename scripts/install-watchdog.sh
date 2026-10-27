#!/bin/bash
# Installs (or refreshes) the kiosk watchdog timer. Called by pi/setup.sh and
# deploy.sh, so a change here reaches the Pi with the next deploy.
set -e
INSTALL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

chmod +x "$INSTALL_DIR/scripts/kiosk-watchdog.sh"
sed "s|/home/pi/Interface-stock|${INSTALL_DIR}|g" "$INSTALL_DIR/systemd/kiosk-watchdog.service" \
    | sudo tee /etc/systemd/system/kiosk-watchdog.service > /dev/null
sudo cp "$INSTALL_DIR/systemd/kiosk-watchdog.timer" /etc/systemd/system/kiosk-watchdog.timer

# Journal in RAM only: fewer SD writes (the card got corrupted at power loss,
# 2026-10-24). Logs are gone after a reboot; capped so it can't eat the 512 MB.
sudo mkdir -p /etc/systemd/journald.conf.d
printf '[Journal]\nStorage=volatile\nRuntimeMaxUse=30M\n' | sudo tee /etc/systemd/journald.conf.d/50-kiosk-size.conf > /dev/null
sudo systemctl restart systemd-journald

sudo systemctl daemon-reload
sudo systemctl enable --now kiosk-watchdog.timer
echo "Kiosk watchdog installed: runs every 2 minutes (journalctl -t kiosk-watchdog)."
