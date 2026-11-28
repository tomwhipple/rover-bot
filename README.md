# rover-cam

A floor rover with a pan camera, so Claude can watch the biped. Separate from the
`robot` repo on purpose.

Design: [docs/design.md](docs/design.md)
Wiring: [docs/wiring.svg](docs/wiring.svg) (PDF: [docs/wiring.pdf](docs/wiring.pdf)), exported from the KiCad 10
project in `hardware/` (`hardware/gen_schematic.py` regenerates it).

Status: runs in simulation. On hardware the sketch runs on the aLaMode and `roverd` on the Pi, and the
console receives the rover's state (2026-11-27); it has not been driven. `roverd` and `rovercam` run there as services.

## Layout

- `firmware/rover/` runs on the aLaMode (board type: Arduino Uno). `rover_core.{h,cpp}` holds the
  serial protocol and every limit, with no Arduino dependency; `rover.ino` is the glue to the motor
  shield (`AFMotor`), the pan servo and the range sensor. Protocol: [docs/serial-protocol.md](docs/serial-protocol.md).
- `pi/roverd/` is `roverd`, the service for the Pi 4: it owns the serial port, relays commands from the
  [robo-control](../robo-control) bus to the firmware one for one, and publishes the rover's state.
  It needs robo-control checked out beside this repo. Without `ROBO_ZENOH_LISTEN` it listens on
  every interface on TCP port 7447 (a free port, with a warning, if another adapter on the machine has
  7447), so a console can reach it by address (`rover.local` in the console's Setup tab) as well as by
  multicast; its `Info` carries the machine's hostname and those endpoints. The rover's HOME state is
  motors stopped and the pan straight ahead (0): on the console's HOME, `roverd` sends `S` and then
  `P` with the home pan (`--home pan=DEG`, default 0; the launch recipe in `robo/robot.json` passes the
  description's `home`).
- `sim/` is the simulated twin: a MuJoCo rover in a room, running `rover_core` as its firmware behind
  a pseudo serial port, so `roverd` drives it unchanged. See [sim/README.md](sim/README.md).
- `robo/robot.json` describes the rover to the robo-control console.
- `pi/rover.cpp` is the earlier SSH command-line tool. It speaks the previous serial protocol and does
  not work with the current firmware.
  Its `rover snap` still works: it takes the picture from `rovercam` when that is running.
- `pi/rovercam.cpp` is `rovercam`, the live camera: it runs `rpicam-vid` and serves its frames as MJPEG
  over HTTP (port 8080; `/snap.jpg` gives one frame), which `roverd` announces to the console as the
  `mast` camera. It holds the camera while it runs.
- `/etc/rover.conf` on the Pi holds the camera settings for both (rotation, sizes, frame rate, port);
  `pi/rover.conf` is the copy `make deploy` installs when the Pi has none. `pi/roverd.service` and
  `pi/rovercam.service` start `roverd` and `rovercam` at boot.
- `pi/flash-alamode.sh` flashes the aLaMode from the Pi: it pulses the reset on
  GPIO18 (header pin 12), then runs `avrdude` over `/dev/serial0`. The Pi needs
  `avrdude` and `pinctrl`.

The firmware builds on the Pi: the Makefile rsyncs `pi/` and `firmware/` to
`~/rover-cam` there, and `make upload` compiles the sketch with `arduino-cli` and flashes it (run
`make firmware-deps` once first). Arduino's avr-gcc runs on the Pi's arm64 but has no Apple Silicon
build. Those targets use `PI_HOST`, default `rover.local`. `roverd` builds on the Pi by hand,
with no make target yet: it needs `cmake`, `libprotobuf-dev`, `protobuf-compiler` and the zenoh-c and
zenoh-cpp Debian packages of the version robo-control uses, and robo-control's `core/` and `schemas/`
at `-DROBO_CONTROL_DIR`. It owns the serial port: stop it before flashing.

## Pre-push hook

`make hooks` once per clone turns on `.githooks/pre-push` (`git config core.hooksPath .githooks`; it
holds in every worktree). Before a push goes out it runs `make prepush` (`tools/prepush.sh`), which
builds step 1 one after another, then runs the gates of steps 2 and 3 side by side, each with its
log in `build/prepush-logs/<stamp>-<pid>/` (`PREPUSH_SERIAL=1`: one at a time), and fails the push if
any failed:

1. Compile: `pi/rover` and `pi/rovercam` (`-Wall -Wextra -Werror`), the firmware core host library and
   host test (`-Wall -Wextra -Werror`), `roverd` (`-Wall -Wextra -Wshadow -Wconversion -Werror`). The
   Arduino sketch too, if `arduino-cli` is on PATH; otherwise it prints `skipped: arduino-cli not
   installed` (the sketch builds on the Pi).
2. Tests: `make test` (the firmware core's host test, the twin, `roverd`, the Pi programs); the
   Python suites run in `PYTEST_WORKERS` pytest-xdist workers (default 8, 0 = one process).
3. Static analysis: `cppcheck` (warning and portability findings, `firmware/` and `pi/`), `clang-tidy`
   (`bugprone-*` and narrowing conversions, set in `.clang-tidy`), and `mypy` over `sim/`,
   `pi/roverd/tests/` and `firmware/pan_cal/` (settings in `mypy.ini`; it runs from the robo-control
   venv, `$(PY) -m mypy`). A missing tool fails the step with an install hint
   (`brew install cppcheck llvm`; with Homebrew's llvm the hook finds `clang-tidy` even though it is
   not on PATH).

`ROBO_CONTROL_DIR` and `PY` are as for `make test`. `ROVER_HOOK_SKIP=cppcheck,clang-tidy,mypy,arduino
git push` skips those steps for one push and says so loudly. `ROVER_CHECK_WRAP='cmd args'` runs the whole
check under a wrapper command (a lock, a sandbox: the bus tests open zenoh sessions on the network).
`git push --no-verify` skips the hook. `make lint` runs all the analysis; `make cppcheck`, `make tidy` and `make mypy` run one each.

## Pins (aLaMode)

| Pin | Use |
|---|---|
| D0/D1 | serial to the Pi, 115200 |
| D4, D7, D8, D12 | motor shield latch |
| D5, D6 | PWM for M3 (left) and M4 (right) |
| D9 | pan servo signal (shield header S2) |
| D10 | Ping))) ultrasonic sensor, trigger and echo on one pin (shield header S1) |
| A0 | pack voltage, 10k over 3.3k divider |
| D2, D3 | reserved for wheel encoders (odometry, for self-driving and SLAM later) |
| A1 / A2 | free (a 4-pin HC-SR04 would use them: trigger / echo) |

## Use

Drive the simulated rover from the console (`make console` calls the robo-control repo):

```
make console            # the rover's console on its local simulation
make console SIM=0     # the real rover
```

Here: `make robo-control` builds `roverd` and the twin's firmware library, `make test` runs the
tests on this machine (firmware core, twin, `roverd`, the Pi programs), `make sim` runs the twin
alone. `make` lists every target.

## Limits (in firmware)

PWM is capped at 150, a drive command lasts 300 ms unless refreshed, forward motion is refused inside
80 cm, and nothing drives below 10.5 V on the pack. With no divider on A0 the pack check fails, so wire
it (or lower `MIN_PACK_MV`) for bench tests.
