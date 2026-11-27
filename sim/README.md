# Simulated twin

`rover_twin.py` stands in for the rover's microcontroller and the physical rover. It loads the
firmware core as a host library (`build/librover_core.dylib`, built by
`make -f mk/firmware.mk rover-core-lib`) through its C ABI (`rc_host_*`, docs/interfaces.md section 4)
and runs it once per 1 ms of simulated time, in real time. The protocol and limits are the firmware's;
the twin only moves bytes and applies the outputs.

- **Serial port**: a pty. The adapter opens the slave (`READY pty=<path>`, or the `--pty-link` symlink).
  The twin keeps its own slave descriptor open, so the adapter may close and reopen it.
- **Physics** (`rover.xml`): differential drive, two driven wheels and a rear skid caster; a DC motor
  model per wheel (PWM to `(pack_v - 2.6 V) * |pwm|/255`, no-load speed proportional to voltage,
  torque linear in speed); pan servo joint, 90 degrees = straight ahead, above 90 = left.
- **Range**: a ray from the `range` site on the pan head, along the pan direction, in cm, 999 beyond
  4 m, refreshed every 60 ms.
- **Cameras**: `mast` (on the pan joint, 0.28 m above the floor) and `overview` (fixed, room corner),
  640x480 JPEG, about 15 fps, MJPEG on `http://127.0.0.1:<port>/mast.mjpg` and `/overview.mjpg`, each part
  with `X-Timestamp-Ns` (capture time, UTC ns). A camera renders only while a client is connected.
  Rendering and physics share one thread; JPEG encoding runs on another.

## Run

    make twin                      # interactive; prints READY pty=... http=...
    make sim-test                  # tests (use ROVER_CORE_LIB=path to pick another library)

    rover_twin.py [--pty-link PATH] [--http-port N] [--pack-mv 11800] [--no-render]
                  [--start X Y YAW_DEG] [--dump-pose-file PATH] [--lib PATH] [--model XML]

`--dump-pose-file` rewrites a JSON file (pose, pan, range, PWM, wheel speeds) every 0.1 s of sim time.
The tests use the firmware library if it is built, else a test double in `tests/stub/`.

## Nominal numbers (not measured)

Wheel radius 0.033 m, track 0.18 m, body radius 0.10 m, mass 1.2 kg, motor 3.5 rad/s per volt and
0.025 N m per volt stall, mast camera 0.08 m ahead of the centre at 0.28 m, range sensor on the pan head, 0.135 m ahead
of the centre at 0.043 m, room 5 m x 5 m.
