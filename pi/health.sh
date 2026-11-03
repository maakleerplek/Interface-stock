#!/bin/bash
# One status line for htl-server's pi-health log (cron every 5 min, forced command of its key, see setup.sh).
# Looking for the cause of the SD card corruptions: undervoltage (throttled is sticky until reboot),
# card/filesystem errors, heat. Prints only; the Pi stores nothing (read-only SD card).
up=$(awk '{printf "%dh%02dm", $1/3600, $1%3600/60}' /proc/uptime)
err=$(journalctl -k -b --no-pager 2>/dev/null | grep -ciE 'EXT4-fs (error|warn)|mmc0: (error|timeout)|I/O error')
echo "up=$up $(vcgencmd get_throttled) $(vcgencmd measure_temp) $(vcgencmd measure_volts core | tr -d '\n') sd_errors=$err load=$(cut -d' ' -f1 /proc/loadavg)"
