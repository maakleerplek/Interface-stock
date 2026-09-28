#!/bin/bash
# kiosk-watchdog.sh — keeps the kiosk Pi healthy without anyone at the screen.
# Runs as root every 2 minutes (systemd/kiosk-watchdog.timer). Every action is
# logged: journalctl -t kiosk-watchdog
#
#  1. Disk: X once logged the TV's screen modes every second until the SD card
#     was full (22 GB Xorg.0.log, 2026-09-28). Empty that log above 100 MB;
#     above 90 % full also shrink the journal.
#  2. Wi-Fi: after a warm reboot the Wi-Fi chip sometimes fails to load its
#     firmware ("brcmfmac: Downloaded RAM image is corrupted") and wlan0 never
#     appears. Reloading the driver loads the firmware again.
#  3. TV power: Samsung's Auto Power Off puts the TV in standby after a few
#     hours without the remote, even with a picture coming in. During opening
#     hours (after tv-on.timer fired, before tv-off.timer) it is switched back
#     on over HDMI-CEC. Turning Auto Power Off off in the TV menu avoids it.
#  4. TV page: Chromium on the heavy TV page sometimes shows a plain white
#     screen (out of memory). A screenshot with no variation means the page is
#     gone: F5 first, then restart Chromium, then reboot (at most every 6 h).
#
# WATCHDOG_DRYRUN=1 only reports what it sees and would do.

STATE=/run/kiosk-watchdog            # counters, cleared on reboot
STAMPS=/var/lib/kiosk-watchdog       # last reboot, kept across reboots
mkdir -p "$STATE" "$STAMPS"
DRY=${WATCHDOG_DRYRUN:-0}

log() { logger -t kiosk-watchdog "$*"; [ "$DRY" = 1 ] && echo "$*"; }
act() { if [ "$DRY" = 1 ]; then echo "would: $*"; else "$@"; fi; }
# Seconds since the stamp file was written, or a large number if never.
age() { [ -f "$1" ] && echo $(( $(date +%s) - $(stat -c %Y "$1") )) || echo 999999; }

export DISPLAY=:0 XAUTHORITY=/home/pi/.Xauthority

# --- 1. Disk -----------------------------------------------------------------
for f in /var/log/Xorg.*.log; do
    [ -f "$f" ] || continue
    if [ "$(du -m "$f" | cut -f1)" -gt 100 ]; then
        log "$f over 100 MB: emptying it"
        act truncate -s 0 "$f"
    fi
done
use=$(df --output=pcent / | tail -1 | tr -dc '0-9')
if [ "$use" -ge 90 ]; then
    log "disk ${use}% full: shrinking the journal to 200 MB"
    act journalctl --vacuum-size=200M
fi

# --- 2. Wi-Fi ----------------------------------------------------------------
if [ ! -e /sys/class/net/wlan0 ]; then
    if [ "$(age "$STATE/wifi-reload")" -ge 600 ]; then
        log "wlan0 missing (Wi-Fi firmware not loaded): reloading brcmfmac"
        act touch "$STATE/wifi-reload"
        act modprobe -r brcmfmac_wcc brcmfmac
        act sleep 2
        act modprobe brcmfmac
    fi
    exit 0   # no network: the TV page cannot load anyway
fi

# --- 3. TV power ------------------------------------------------------------
# Wall-clock time of a timer's last run; Persistent= timers keep it across
# reboots. 0 if it never ran.
last() { local t; t=$(systemctl show -P LastTriggerUSec "$1" 2>/dev/null)
         [ -n "$t" ] && [ "$t" != n/a ] && date -d "$t" +%s 2>/dev/null || echo 0; }
on_at=$(last tv-on.timer); off_at=$(last tv-off.timer)
# Opening hours = tv-on ran more recently than tv-off.
if [ "${on_at:-0}" -gt "${off_at:-0}" ] && [ -e /dev/cec0 ]; then
    pwr=$(timeout 10 cec-ctl -d /dev/cec0 --playback --to 0 --give-device-power-status 2>/dev/null \
          | sed -n 's/.*pwr-state: \([a-z-]*\).*/\1/p')
    if [ "$pwr" = standby ]; then
        log "TV in standby during opening hours: switching it on over CEC"
        act "$(dirname "$0")/tv-on.sh"
    fi
fi

# --- 4. TV page --------------------------------------------------------------
URL=$(/opt/custompios/scripts/get_url 2>/dev/null)
if ! curl -sf -o /dev/null --max-time 15 "$URL"; then
    rm -f "$STATE/blank"   # server down: not something a refresh fixes
    exit 0
fi

# Mean brightness and spread of a tiny grayscale screenshot. The TV page has
# a spread around 0.18; a white (or black) screen is close to 0.
read -r mean sd < <(import -silent -window root -resize 64x36! -colorspace gray \
    -format '%[fx:mean] %[fx:standard_deviation]' info: 2>/dev/null)
[ -n "$sd" ] || exit 0   # X not up yet

if awk -v sd="$sd" 'BEGIN { exit !(sd < 0.02) }'; then
    n=$(( $(cat "$STATE/blank" 2>/dev/null || echo 0) + 1 ))
    [ "$DRY" = 1 ] || echo "$n" > "$STATE/blank"
    case $n in
        1)
            log "TV page blank (mean=$mean sd=$sd): pressing F5"
            win=$(xdotool search --onlyvisible --class chromium | head -1)
            [ -n "$win" ] && act xdotool windowactivate --sync "$win" key --window "$win" F5
            ;;
        2)
            # run_onepageos starts Chromium again as soon as it is gone.
            log "TV page still blank after F5: restarting Chromium"
            act pkill -x chromium
            ;;
        4)
            if [ "$(age "$STAMPS/reboot")" -ge 21600 ]; then
                log "TV page still blank after a Chromium restart: rebooting"
                act touch "$STAMPS/reboot"
                act systemctl reboot
            else
                log "TV page still blank; last watchdog reboot under 6 h ago, not rebooting again"
            fi
            ;;
    esac
else
    [ -f "$STATE/blank" ] && log "TV page back (mean=$mean sd=$sd)"
    [ "$DRY" = 1 ] && echo "TV page ok (mean=$mean sd=$sd)"
    rm -f "$STATE/blank"
fi
