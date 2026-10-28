#!/bin/sh
# SD card read-only (overlay): after boot every write goes to RAM and is gone at
# the next boot, so a power cut can no longer corrupt the card (it did on
# 2026-10-24). Updates still work: update.sh redeploys master after every boot.
#
#   pi/overlay.sh status   is it on now / at the next boot?
#   pi/overlay.sh on       from the next boot (also /boot/firmware read-only)
#   pi/overlay.sh off      card writable again from the next boot, to change
#                          something for good (.env, setup.sh, apt upgrade).
#                          /boot/firmware stays read-only: to edit config.txt,
#                          sudo mount -o remount,rw /boot/firmware
set -e
case "$1" in
    status)
        grep -q ' / overlay' /proc/mounts && echo "now: on" || echo "now: off"
        grep -q overlayroot=tmpfs /boot/firmware/cmdline.txt && echo "next boot: on" || echo "next boot: off" ;;
    on)
        if grep -q ' / overlay' /proc/mounts; then echo "Already on."; exit 0; fi
        sudo raspi-config nonint enable_bootro      # only possible while the overlay is off
        sudo raspi-config nonint enable_overlayfs
        echo "On from the next boot: sudo reboot" ;;
    off)
        sudo raspi-config nonint disable_overlayfs
        echo "Off from the next boot: sudo reboot. Afterwards: pi/overlay.sh on + reboot." ;;
    *)
        echo "usage: $0 status|on|off   (on/off take effect at the next boot)"; exit 1 ;;
esac
