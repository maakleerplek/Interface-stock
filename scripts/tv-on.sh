#!/bin/bash
cec-ctl -d /dev/cec0 --playback --to 0 --image-view-on
cec-ctl -d /dev/cec0 --playback --active-source phys-addr=1.0.0.0
