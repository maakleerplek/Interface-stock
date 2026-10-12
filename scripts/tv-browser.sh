#!/bin/bash
# The TV page in Chromium on the TV (HDMI-A-1), from pi/labwc/autostart.
# URL: TV_PRESENTATION_URL in ~/Interface-stock/.env.

flags=(
   --kiosk
   --touch-events=enabled
   --disable-pinch
   --noerrdialogs
   --disable-session-crashed-bubble
   --simulate-outdated-no-au='Tue, 31 Dec 2099 23:59:59 GMT'
   --disable-component-update
   --overscroll-history-navigation=0
   # LocalNetworkAccessChecks: the TV page (served from the server) talks to
   # the touch kiosk on ws://localhost:8765; newer Chromium would otherwise
   # show a permission prompt that nobody can answer in kiosk mode.
   --disable-features=TranslateUI,LocalNetworkAccessChecks
   --disable-infobars
   --force-device-scale-factor=1
   --autoplay-policy=no-user-gesture-required
   # 512 MB Pi: let Chromium save memory instead of switching that off, and
   # keep it to one renderer (the TV page is the only tab).
   --enable-low-end-device-mode
   --renderer-process-limit=1
   --disable-background-networking
   --disable-sync
   --metrics-recording-only
   --no-first-run
)

URL=$(sed -n 's/^TV_PRESENTATION_URL=//p' "$(dirname "$0")/../.env" | tr -d '"')
exec chromium --ozone-platform=wayland "${flags[@]}" --app="${URL:-http://10.72.1.246:8083}"
