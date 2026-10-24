#!/bin/bash
# Pi deploy step, run by scripts/autodeploy.sh after it moved to a new master:
# newest kiosk build, refreshed watchdog, then check the kiosk and the TV run.
# Rollback note: the kiosk build always comes from release kiosk-latest, so a
# rollback restores the scripts but not an older kiosk binary.
set -e
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
"$DIR/pi/fetch-kiosk.sh"
bash "$DIR/scripts/install-watchdog.sh"
sleep 20   # labwc's autostart loop restarts a replaced kiosk within seconds
pgrep -x chromium > /dev/null || { echo "TV browser not running"; exit 1; }
# The kiosk only runs when the touch display is connected (pi/labwc/autostart).
if grep -qs '^connected' /sys/class/drm/card*-DSI-1/status; then
    pgrep -x interface-stock > /dev/null || { echo "kiosk not running"; exit 1; }
fi
