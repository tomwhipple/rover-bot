#!/bin/sh
# Flash the aLaMode from the Pi it sits on. Usage: flash-alamode.sh FILE.hex
#
# The ATmega's reset is capacitor-coupled to Pi GPIO18 (header pin 12), like
# an Uno's DTR line. Pulsing it low restarts Optiboot, which then listens on
# the UART at 115200 for about a second. The pulse timing copies WyoLum's
# avrdude-alamode (alamode.c), which only knows the pre-Pi 4 GPIO address.
set -eu

hex=${1:?usage: flash-alamode.sh FILE.hex}
port=${SERIAL:-/dev/serial0}

command -v avrdude >/dev/null || { echo "flash-alamode: avrdude not found (sudo apt install avrdude)" >&2; exit 1; }
command -v pinctrl >/dev/null || { echo "flash-alamode: pinctrl not found (ships with Raspberry Pi OS Bookworm and later)" >&2; exit 1; }

pinctrl set 18 op dl
sleep 0.05
pinctrl set 18 op dh
sleep 0.05
exec avrdude -p atmega328p -c arduino -P "$port" -b 115200 -D -U "flash:w:$hex:i"
