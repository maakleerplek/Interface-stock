#!/bin/bash
# Nightly (interface-stock-update.timer): newest scripts from git, newest kiosk build.
set -e
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
git -C "$DIR" pull --ff-only
"$DIR/pi/fetch-kiosk.sh"
bash "$DIR/scripts/install-watchdog.sh"
# The apt timers are off (pi/setup.sh): OS updates once a month, at night.
if [ "$(date +%d)" = 01 ]; then
    sudo apt-get update -q && sudo DEBIAN_FRONTEND=noninteractive apt-get -y -q upgrade
fi
