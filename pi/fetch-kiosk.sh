#!/bin/bash
# Downloads the newest kiosk build (GitHub release "kiosk-latest", built by
# .github/workflows/kiosk.yml) to kiosk/interface-stock. When it changed, the
# running kiosk is stopped and labwc's autostart starts the new one.
set -euo pipefail
DIR="$(cd "$(dirname "$0")/.." && pwd)"
BIN="$DIR/kiosk/interface-stock"
URL=https://github.com/maakleerplek/Interface-stock/releases/download/kiosk-latest/interface-stock
curl -fsSL --retry 3 -o "$BIN.new" "$URL"
chmod +x "$BIN.new"
if cmp -s "$BIN.new" "$BIN"; then
    rm "$BIN.new"; echo "Kiosk up to date."
else
    mv "$BIN.new" "$BIN"; echo "Kiosk updated."
    pkill -x interface-stock || true
fi
