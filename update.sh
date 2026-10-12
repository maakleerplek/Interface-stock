#!/bin/bash
# Nightly (interface-stock-update.timer): newest scripts from git, newest kiosk build.
set -e
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
git -C "$DIR" pull --ff-only
"$DIR/pi/fetch-kiosk.sh"
bash "$DIR/scripts/install-watchdog.sh"
