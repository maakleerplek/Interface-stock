#!/bin/bash
# Every 5 minutes (interface-stock-update.timer): deploy master once CI on it is
# green (scripts/autodeploy.sh, which runs deploy.sh), and OS updates once a month.
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
"$DIR/scripts/autodeploy.sh"
# The apt timers are off (pi/setup.sh): OS updates once a month, at 01:00 on the 1st.
stamp=/var/tmp/apt-upgrade-$(date +%Y-%m)
if [ "$(date +%d%H)" = 0101 ] && [ ! -e "$stamp" ]; then
    sudo apt-get update -q && sudo DEBIAN_FRONTEND=noninteractive apt-get -y -q upgrade && touch "$stamp"
fi
