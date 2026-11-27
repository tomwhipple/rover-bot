# Rover serial protocol (v2)

Between the Pi and the aLaMode. 115200 8N1, ASCII lines ending `\n` (a `\r` is ignored). The logic is
`firmware/rover/rover_core.{h,cpp}`; `firmware/rover/rover.ino` is only glue.

## Framing

Every line, both directions: `$<payload>*HH`. `HH` is the XOR of the payload's bytes (the bytes between
`$` and `*`) in two upper-case hex digits; the firmware also accepts lower case. Payload fields are
single-space-separated decimal integers.

The firmware ignores a line that has a bad or missing checksum, is not `$`-framed, is longer than 40
bytes after the `$`, or is cut off by a new `$`. Each sets `BAD_LINE` until the next `T`. A line with a
good checksum but a malformed command gets an `E` reply.

## Pi to firmware

| Payload | Meaning |
|---|---|
| `V <left> <right> <seq>` | Drive. PWM -255..255 per side (sign = direction), `seq` 0..4294967295. Values beyond ±150 are clamped to ±150. Valid for 300 ms; with no newer `V` the motors stop. |
| `P <deg>` | Pan target in degrees: 0 is straight ahead, positive is to the left. Clamped to the pan's edges. Initial 0. The pan moves there at 62.5 deg/s. |
| `S` | Stop now: the motors stop and the pan stays where it is. |
| `?` | Send the hello line. |

Arguments are exact: one space between fields, no trailing text. A `V` whose PWM is outside ±255 is
malformed.

## Firmware to Pi

| Payload | Meaning |
|---|---|
| `H rover 2.1.0 pwm_max=150 standoff_cm=80 min_mv=10500 timeout_ms=300` | Hello, at boot and on `?`. |
| `T <ms> <mv> <cm> <left> <right> <pan> <flags> <loop_us> <seq>` | State, every 50 ms. |
| `E <code> <text>` | A command was malformed or refused. |

`T` fields: `millis()`; pack mV; range cm (999 = no echo); PWM applied per side after the limits; pan
degrees; flags; the longest loop iteration since the previous `T`, in us; `seq` of the last well-formed
`V` (0 before any).

`T` is scheduled on a 50 ms grid (no drift); with a loop slower than 1 ms it goes out at the first
loop pass at or after each grid time. The longest line is under 80 bytes.

Flags (bit mask):

| Bit | Name | Set while |
|---|---|---|
| 1 | `MOVING` | either applied PWM is non-zero |
| 2 | `BLOCKED` | a forward command is being stopped by the stand-off; also latched until the next `T` |
| 4 | `LOW_PACK` | pack below 10500 mV |
| 8 | `CMD_TIMEOUT` | the last motion ended by timeout; cleared by the next `V` or `S` |
| 16 | `BAD_LINE` | a line was rejected since the previous `T` |

Error codes: 1 unknown command, 2 bad arguments, 3 blocked by the stand-off, 4 refused for low pack.
Codes 3 and 4 are sent once when a refusal begins, not on every `V`.

## Limits

Enforced in the firmware; nothing upstream can override them.

- PWM per side is clamped to 150.
- Stand-off: while range < 80 cm, a command whose clamped `left + right` is positive is replaced by
  stop and `BLOCKED` is set. Reversing and pivoting are allowed. The check runs every loop, so driving
  into range stops the rover; if the obstacle clears while the command is still valid the rover
  resumes. Range 999 (no echo) is clear.
- Low pack: below 10500 mV nothing drives and `LOW_PACK` is set.
- Command timeout: 300 ms after the last `V` the motors stop.
- Pan edges: the pan stops at -60 and +60 degrees (`PAN_EDGE_RIGHT`, `PAN_EDGE_LEFT`), so no command
  drives the servo into its end stops. The values are provisional until the servo is calibrated on
  the rover.
- Pan rate: the pan moves towards its target one degree every 16 ms (62.5 deg/s), so a full sweep takes
  2.9 s. `T` reports where the pan is (signed), not the target. The sketch turns the angle into a
  servo pulse from two calibration constants (`PAN_CENTER_US`, `PAN_US_PER_90` in `rover.ino`).

## Host library

The same source builds as a host library (`make -f mk/firmware.mk rover-core-lib` gives
`build/librover_core.dylib` or `.so`) with this C ABI:

```c
void*  rc_host_new(void);
void   rc_host_free(void* rc);
void   rc_host_rx(void* rc, const uint8_t* data, size_t n);
void   rc_host_tick(void* rc, uint32_t now_ms, uint16_t pack_mv, uint16_t range_cm, uint32_t loop_us,
                    int16_t* left_pwm, int16_t* right_pwm, uint8_t* pan_deg);
size_t rc_host_tx(void* rc, uint8_t* buf, size_t cap);
```

`rc_host_tick` runs once per loop pass; `loop_us` is the previous pass's duration and the core reports
the maximum since the last `T`. A new instance has the hello line queued. A command takes effect, and
its 300 ms window starts, at the next `rc_host_tick`. The transmit queue holds 160 bytes; a line that
does not fit is dropped whole.

`make -f mk/firmware.mk firmware-host-test` builds and runs `firmware/host/test_rover_core.cpp` under
ASan and UBSan.
