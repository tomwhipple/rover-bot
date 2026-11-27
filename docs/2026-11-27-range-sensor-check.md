# Range sensor check against a ruler

2026-11-27 · record of a bench session

The Ping))) on the pan head was checked against an 18 in steel rule, after the firmware change that
times its echo in an interrupt (`firmware/rover/rover.ino`).

## Setup

The rover sat on a board against a wooden stop, pan at 0. The rule lay on the board along the
sensor's line of sight with its zero roughly under the sensor's circuit board (placed by eye). The
target was the long face of a tissue box, standing on the board, square to the rule, with its face at
the stated mark. Each row is 100 consecutive readings from `roverd`'s state messages (5 s).

## Readings

| Target at | True distance | Reported (all 100) | Difference |
|---|---|---|---|
| 18 in | 45.7 cm | 46 cm | +0.3 cm |
| 12 in | 30.5 cm | 30 cm | -0.5 cm |
| 9 in | 22.9 cm | 22 cm | -0.9 cm |
| 6 in | 15.2 cm | 15 cm | -0.2 cm |
| 3 in | 7.6 cm | 7 cm | -0.6 cm |

The 18 in row was read with the box touching the rule's far end, judged from an overhead camera; the
raw echo there was 2680 us.

## Result

Every reading is within 1 cm of the rule, and each position gave one value 100 times out of 100. The
firmware reports whole centimetres, so the scale cannot be checked to better than about 3 % over this
span (39 cm reported over 38.1 cm true); within that it agrees with the 58 us per cm the firmware
uses. The readings average 0.4 cm short, which is less than the uncertainty in where the rule's zero
sat against the sensor. No correction was added.

Not covered: distances beyond 46 cm, targets that are soft or at an angle, and other pan angles.
