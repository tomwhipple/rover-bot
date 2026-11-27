# rover-cam

A floor rover with a pan camera, so Claude can watch the biped. Separate from the
`robot` repo on purpose.

Design: [docs/design.md](docs/design.md)
Wiring: [docs/wiring.svg](docs/wiring.svg) (PDF: [docs/wiring.pdf](docs/wiring.pdf)), exported from the KiCad 10
project in `hardware/` (`hardware/gen_schematic.py` regenerates it).

Status: runs in simulation; not yet run on hardware. The sketch has not been compiled for the ATmega
since the protocol change.

## Layout

- `firmware/rover/` runs on the aLaMode (board type: Arduino Uno). `rover_core.{h,cpp}` holds the
  serial protocol and every limit, with no Arduino dependency; `rover.ino` is the glue to the motor
  shield (`AFMotor`), the pan servo and the range sensor. Protocol: [docs/serial-protocol.md](docs/serial-protocol.md).
- `pi/roverd/` is `roverd`, the service for the Pi 4: it owns the serial port, relays commands from the
  [robo-control](../robo-control) bus to the firmware one for one, and publishes the rover's state.
  It needs robo-control checked out beside this repo. Without `ROBO_ZENOH_LISTEN` it listens on
  every interface on TCP port 7447 (a free port, with a warning, if another adapter on the machine has
  7447), so a console can reach it by address (`rover.local` in the console's Setup tab) as well as by
  multicast; its `Info` carries the machine's hostname and those endpoints.
- `sim/` is the simulated twin: a MuJoCo rover in a room, running `rover_core` as its firmware behind
  a pseudo serial port, so `roverd` drives it unchanged. See [sim/README.md](sim/README.md).
- `robo/robot.json` describes the rover to the robo-control console.
- `pi/rover.cpp` is the earlier SSH command-line tool. It speaks the previous serial protocol and does
  not work with the current firmware.
  `rover snap` takes its camera settings (rotation, size, warm-up time) from `/etc/rover.conf` on the
  Pi; `pi/rover.conf` is the copy `make deploy` installs when the Pi has none.
- `pi/flash-alamode.sh` flashes the aLaMode from the Pi: it pulses the reset on
  GPIO18 (header pin 12), then runs `avrdude` over `/dev/serial0`. The Pi needs
  `avrdude` and `pinctrl`.

The firmware builds on the Pi: the Makefile rsyncs `pi/` and `firmware/` to
`~/rover-cam` there, and `make upload` compiles the sketch with `arduino-cli` and flashes it (run
`make firmware-deps` once first). Arduino's avr-gcc runs on the Pi's arm64 but has no Apple Silicon
build. Those targets use `PI_HOST`, default `rover.local`. Building and installing `roverd` on the Pi
is not set up yet.

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

Drive the simulated rover from the console, in the robo-control repo:

```
make run-console ROBOT=rover SIM=1
```

Here: `make robo-control` builds `roverd` and the twin's firmware library, `make robo-test` runs the
host tests (firmware core, twin, `roverd`), `make twin` runs the twin alone.

## Limits (in firmware)

PWM is capped at 150, a drive command lasts 300 ms unless refreshed, forward motion is refused inside
80 cm, and nothing drives below 10.5 V on the pack. With no divider on A0 the pack check fails, so wire
it (or lower `MIN_PACK_MV`) for bench tests.
