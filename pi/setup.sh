#!/bin/bash
# Turns a fresh Raspberry Pi OS Lite (Trixie, 32-bit) card into the Interface-stock
# kiosk: touch kiosk on the Touch Display, TV page in Chromium on the TV.
# Flash with pi/user-data on the boot partition (see README, Recovery), then on the Pi:
#   git clone https://github.com/maakleerplek/Interface-stock ~/Interface-stock && ~/Interface-stock/pi/setup.sh
# and put back .env and barcode_cache.json from the backup. Safe to run again.
set -euo pipefail
DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "--- Packages"
sudo apt-get update
sudo apt-get install -y --no-install-recommends labwc wlr-randr chromium grim imagemagick cec-utils curl git fonts-dejavu-core

echo "--- Boot config: Touch Display overlay"
grep -q ili9881 /boot/firmware/config.txt || sudo tee -a /boot/firmware/config.txt < "$DIR/pi/config.txt" > /dev/null

echo "--- Kiosk program"
"$DIR/pi/fetch-kiosk.sh"

echo "--- labwc on tty1 for pi"
sudo mkdir -p /etc/systemd/system/getty@tty1.service.d
printf '[Service]\nExecStart=\nExecStart=-/sbin/agetty --autologin pi --noclear %%I $TERM\n' \
    | sudo tee /etc/systemd/system/getty@tty1.service.d/autologin.conf > /dev/null
grep -q 'exec labwc' ~/.profile || echo '[ "$(tty)" = /dev/tty1 ] && exec labwc' >> ~/.profile
mkdir -p ~/.config && ln -sfn "$DIR/pi/labwc" ~/.config/labwc

echo "--- Timers: TV on/off, watchdog, nightly update"
for unit in tv-on tv-off; do
    sed "s|/home/pi/Interface-stock|$DIR|g" "$DIR/systemd/$unit.service" | sudo tee /etc/systemd/system/$unit.service > /dev/null
    sudo cp "$DIR/systemd/$unit.timer" /etc/systemd/system/
done
sed -e "s|__INSTALL_DIR__|$DIR|g" -e "s|__USER__|$(whoami)|g" "$DIR/systemd/auto-update.service" \
    | sudo tee /etc/systemd/system/interface-stock-update.service > /dev/null
sudo cp "$DIR/systemd/auto-update.timer" /etc/systemd/system/interface-stock-update.timer
sudo systemctl daemon-reload
sudo systemctl enable --now tv-on.timer tv-off.timer interface-stock-update.timer
bash "$DIR/scripts/install-watchdog.sh"

echo "--- Settings backup: htl-server may fetch these files, nothing else (pi-kiosk-backup)"
mkdir -p ~/.ssh
grep -q pi-kiosk-backup ~/.ssh/authorized_keys 2>/dev/null || echo 'restrict,command="tar czf - --ignore-failed-read -C / home/pi/Interface-stock/.env home/pi/Interface-stock/barcode_cache.json boot/firmware/config.txt boot/firmware/cmdline.txt" ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIK8uz0G1zaRxhi9JklTnuC5gb4WU8hm4VwX1dM3gG3DV htl-server pi-kiosk-backup' >> ~/.ssh/authorized_keys

[ -f "$DIR/.env" ] || echo "!!! No .env yet: copy it from the backup to $DIR/.env (see README, Recovery)"
echo "--- Done. Reboot to start the kiosk: sudo reboot"
