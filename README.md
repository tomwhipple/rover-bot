# rover-cam

A floor rover with a pan camera, so Claude can watch the biped. Separate from the
`robot` repo on purpose.

Design: [docs/design.md](docs/design.md)
Wiring: [docs/wiring.svg](docs/wiring.svg)

Status: first draft, not yet run on hardware.

## Layout

- `firmware/rover/rover.ino` runs on the aLaMode. Needs the Adafruit Motor Shield
  (v1) library, `AFMotor`. Board type: Arduino Uno.
- `pi/rover.cpp` is `rover`, a C++ command-line tool for the Pi 4, run over SSH.
  No dependencies beyond the standard library.
  Snapshots use the stock `rpicam-still`.
- `pi/flash-alamode.sh` flashes the aLaMode from the Pi: it pulses the reset on
  GPIO18 (header pin 12), then runs `avrdude` over `/dev/serial0`. The Pi needs
  `avrdude` and `pinctrl`.

Everything builds on the Pi: the Makefile rsyncs `pi/` and `firmware/` to
`~/rover-cam` there. `make deploy` builds and installs `rover`. `make upload`
compiles the firmware with `arduino-cli` and flashes it (run `make firmware-deps`
once first). Arduino's avr-gcc runs on the Pi's arm64 but has no Apple Silicon
build. All targets use `PI_HOST`, default `rover.local`.

## Pins (aLaMode)

| Pin | Use |
|---|---|
| D0/D1 | serial to the Pi, 115200 |
| D4, D7, D8, D12 | motor shield latch |
| D5, D6 | PWM for M3 (left) and M4 (right) |
| D9 | pan servo signal |
| A0 | pack voltage, 10k over 3.3k divider |
| D2, D3 | reserved for wheel encoders (odometry, for self-driving and SLAM later) |
| A1 / A2 | ultrasonic trigger / echo (a 3-pin Ping))) uses A1 alone) |

## Use

```
ssh rover rover snap > look.jpg
ssh rover rover move 120 120 800
ssh rover rover pan 60
ssh rover rover status
```

## Limits (in firmware)

Moves end within 2 s, PWM is capped at 150, forward motion is refused inside
80 cm, and nothing drives below 10.5 V on the pack. With no divider on A0 the
pack check fails, so wire it (or lower `MIN_PACK_MV`) for bench tests.
