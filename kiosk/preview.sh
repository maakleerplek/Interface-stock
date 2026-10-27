#!/bin/sh
# Look at the kiosk UI on a PC with the demo shop.
#   ./preview.sh        the live window: tap with the mouse, type a barcode + Enter here to scan
#   ./preview.sh shots  a PNG of every screen in target/screens/
cd "$(dirname "$0")" || exit 1
if [ "$1" = shots ]; then
    cargo test --test ui tap_through_every_screen && xdg-open target/screens/
else
    cargo run -- --demo --no-tv --fake-scanner
fi
