# Interface-stock

The self-service till at the High Tech Lab: a Raspberry Pi 3A+ with the 7" Touch
Display 2 (touch kiosk, `kiosk/`, Rust + Slint) and the TV over HDMI (the TV page
in Chromium). Raspberry Pi OS Lite (Trixie, 32-bit) with labwc; set up by
`pi/setup.sh`.

## Kiosk Pi: setup and recovery

Everything to rebuild the Pi is in this repo. The only things that are not, the
`.env` (InvenTree token, payment details) and `barcode_cache.json`, are fetched
**weekly** by htl-server (`pi-kiosk-backup.timer`) and kept a year on SharePoint:
`HTL-shared/Stock-keeping/Pi-kiosk-backup/config/<date>/pi-kiosk-config-<date>.tar.gz`.

**New SD card or new Pi (about 20 minutes):**
1. Flash *Raspberry Pi OS Lite (32-bit)* with Raspberry Pi Imager. In its settings
   only set the Wi-Fi (the HTL network), or put `pi/user-data` on the boot
   partition afterwards (hostname `interface-stock`, user `pi`, SSH with key only;
   add your own key to it). Wi-Fi then goes in `network-config` next to it
   (cloud-init, `wifis: wlan0: access-points: "<ssid>": password: ...`).
2. Boot it with the Touch Display on DSI and the TV on HDMI, then over SSH:
   `sudo apt-get install -y git && git clone https://github.com/maakleerplek/Interface-stock ~/Interface-stock && ~/Interface-stock/pi/setup.sh`
3. Put back the settings from the newest backup: unpack the tar.gz and copy
   `home/pi/Interface-stock/.env` and `barcode_cache.json` to `~/Interface-stock/`.
4. Turn the read-only SD card on (setup.sh does it itself if the `.env` was already there):
   `~/Interface-stock/pi/overlay.sh on`, then `sudo reboot`. The kiosk starts on the touch screen,
   the TV page on the TV.
5. On htl-server, forget the old host key so the weekly backup accepts the new Pi:
   `sudo ssh-keygen -R 10.72.3.105 -f /etc/pi-kiosk-backup/known_hosts`.

A new Pi (same card) needs nothing: the card boots on any Pi 3/4. Only its IP
can change (new Wi-Fi MAC); the backup and SSH use 10.72.3.105.

**How it runs:** labwc starts on tty1 (autologin `pi`) and runs `pi/labwc/autostart`:
the kiosk (`kiosk/interface-stock`) on DSI-1 and `scripts/tv-browser.sh` on
HDMI-A-1, each restarted when it exits. The kiosk binary is built by GitHub Actions
(`.github/workflows/kiosk.yml`, release `kiosk-latest`); every 5 minutes `update.sh`
runs `scripts/autodeploy.sh`: once CI on a new master commit is green it pulls and runs
`deploy.sh` (newest build, watchdog, check that kiosk and TV run), rolling back if that
fails. Log: `.git/autodeploy.log`. `scripts/kiosk-watchdog.sh` (every 2 min)
restarts a blank TV page, reloads Wi-Fi and keeps the disk from filling up.

**Read-only SD card:** the root filesystem is an overlay (`pi/overlay.sh`): after boot every
write goes to RAM and is gone at the next boot, so a power cut cannot corrupt the card (it did
on 2026-10-24). Deploys still work: they live in RAM until the next boot, and after a boot
`update.sh` deploys master again within 5 minutes. To change something for good (`.env`, an
`apt upgrade`): `pi/overlay.sh off`, reboot, change it, `pi/overlay.sh on`, reboot.
`/boot/firmware` stays read-only; for `config.txt`: `sudo mount -o remount,rw /boot/firmware`.

**Look at the UI on a PC:** `kiosk/preview.sh` opens the kiosk with the demo shop
(type a barcode + Enter in the terminal to scan, e.g. `CONFIRM`); `kiosk/preview.sh shots`
writes a PNG of every screen to `kiosk/target/screens/`.

