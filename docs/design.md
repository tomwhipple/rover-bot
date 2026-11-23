# Rover camera for watching the biped — design

Nov 22, 2026 · @Tom Whipple

## Summary

A floor rover built almost entirely from the old kit: the Pi 4 runs the camera and a small command-line tool, the aLaMode runs the motors and pan servo, and Claude drives it with short move, pan and snapshot commands sent over SSH from your laptop. The priority is getting it running quickly, so there is no custom service and no custom power board.

The Pi never touches the motors directly. It sends one-line commands over serial to the aLaMode, which enforces time limits and a stand-off distance on its own. If the Pi, Wi-Fi or Claude stalls, the rover stops.

Choices and assumptions, two of them mine because the question went unanswered:

- **Surface:** floor only (your answer, read as option 1). No cliff sensors.
- **Power:** one of the biped's 3S packs (OVONIC 4200 mAh, XT60), through an XT60 pigtail with an inline 3 A fuse. Code lives in a new repo, rover-cam, not in the robot repo.
- **Control (assumed):** staged. Manual step-and-look driving first, tag-based auto-follow later.
- **Budget (assumed):** small buys of about $50 are fine.

Video stays on your network. Any frame Claude looks at is sent to Anthropic, same as with the PTZ camera plan.

## Parts on hand

Identified from your three photos. Only the camera still needs a look before wiring.

| Part | What it is | Job on the rover |
| --- | --- | --- |
| Raspberry Pi 4 (with heatsinks) | Linux computer, Wi-Fi, camera port | Camera, HTTP service, serial link to the aLaMode |
| WyoLum aLaMode | Arduino-Uno-compatible ATmega328P board that plugs onto the Pi's first 26 GPIO pins; has an RTC and microSD | Real-time motor and servo control, safety limits |
| Adafruit Motor Shield v1 | Two L293D drivers behind a 74HC595 latch, two servo headers | Drives the two wheel motors |
| Round two-deck chassis | Two yellow TT gearmotors, caster, toggle switch | The base |
| Parallax servo on the metal bracket | **Standard type, per its label** | Camera pan |
| Raspberry Pi Camera Board (element14 box) | **Check:** most likely the original 5 MP v1 with a 15-pin ribbon | The eye |
| OVONIC 3S 4200 mAh LiPo, XT60 | 11.1 V nominal, 12.6 V full, from the biped's stock | Power |
| Power-lite board | The biped's protection and distribution board | Not used: there is no spare BMS, so a fused pigtail replaces it |

## Architecture

Three boards stack on the top deck, and every command passes down the same chain.

Physical stack, bottom to top:

1. Lower deck: gearmotors, the 3S pack, fuse, switch and the two buck converters.
2. Upper deck: Pi 4 on standoffs.
3. aLaMode on the Pi's GPIO pins 1 to 26.
4. Motor shield on the aLaMode's Arduino headers.
5. Mast at the front: pan servo on the metal bracket, camera on the servo horn, about 28 cm above the floor and level, which frames the 56 cm biped from 1 m away.

Command path:

1. Claude runs `ssh` in a shell on your laptop.
2. The command crosses your Wi-Fi to the Pi over SSH.
3. The rover tool on the Pi writes a one-line command to the aLaMode over the Pi's UART.
4. The aLaMode checks limits, then drives the shield and servo.
5. For a look, the tool grabs one JPEG from the camera and streams it back.

Each look-and-move cycle takes Claude several seconds, so this is step-and-look driving. For anything fast, such as a fall, the Pi records video locally and Claude reviews frames afterwards.

## Wiring and pin map

The wheel motors go on the shield's M3 and M4 terminals, which keeps D11 free and leaves only one pin shared with the aLaMode's SPI lines.

| Signal | Pin | Notes |
| --- | --- | --- |
| Pi to aLaMode serial | Pi header 8 and 10 to ATmega D0 and D1 | 115200 baud; also used to flash firmware |
| aLaMode reset | Pi header 12 | Lets the Pi reset the ATmega for flashing |
| aLaMode power | 5V-LINK jumper ON | aLaMode logic runs from the Pi's 5 V |
| Shield latch | D4, D7, D8, D12 | D12 is also SPI MISO, so the aLaMode microSD stays unused and Pi SPI stays off |
| Left and right motor PWM | D5, D6 (M3, M4) | Speed capped in firmware |
| Pan servo signal | D9 | Signal only; servo power comes from the 6 V buck, not the shield |
| Pack voltage | A0 | 10 k over 3.3 k divider from the 12 V rail, 3.1 V at a full pack |
| Front ultrasonic sensor (on hand) | A1 trigger, A2 echo | Fixed to the chassis, facing forward. A 3-pin Ping))) uses A1 alone |
| Camera | Pi CSI port | Ribbon exits from under the stack to the mast |

On the shield, pull the PWR jumper so motor power comes only from the EXT\_PWR terminals.

Sources: the [aLaMode v1 datasheet](https://www.electrokit.com/upload/product/41011/41011667/AlaMode_V1datasheet.pdf) for the header pins and 5V-LINK, and [shieldlist.org](https://www.shieldlist.org/adafruit/motor) for the shield pins. Shieldlist does not say which shield revision it covers, so confirm D5/D6 and D9 with the first bench test.

## Power

One 3S pack feeds everything through an XT60 pigtail, an inline 3 A fuse and a switch, then two buck converters make 5 V for the Pi and 6 V for the motors and servo.

The power-lite board is not used. There is no spare BMS for it, and the pigtail is quicker to build. The fuse covers a short; nothing covers over-discharge except the firmware check below and the switch.

From the switch (fused pack voltage):

- **5 V buck, 3 A or more:** to the Pi 4's USB-C port. The aLaMode takes its 5 V from the Pi.
- **6 V buck, 2 A or more:** to the shield's EXT\_PWR and the pan servo's power pin.

The motors must not see pack voltage. The TT gearmotors are 3 to 6 V parts and the L293D is a 0.6 A driver, so 12 V would overrun both.

Expected load is about 0.6 A at 12 V for the Pi and up to 0.7 A for motors and servo, well inside the 3 A fuse. A 4200 mAh pack should give roughly 4 hours; that is an estimate, not a measurement.

**Under-voltage is not protected.** With no BMS, nothing disconnects a low pack. The aLaMode reads pack voltage on A0 and refuses to drive below 10.5 V, and the status command reports it, but the switch has to be turned off by hand. A left-on rover will ruin the pack.

## Software

Two small programs: an Arduino sketch on the aLaMode and `rover`, a C++ command-line tool on the Pi that Claude runs over SSH. Both live in the new `rover-cam` repo.

Firmware serial commands, one per line:

| Command | Meaning |
| --- | --- |
| `M left right ms` | Run the motors at -255 to 255 for at most 2000 ms, then stop |
| `P deg` | Pan the camera to 0 to 180 degrees |
| `S` | Stop now |
| `?` | Report pack millivolts and front range in cm |

Pi commands:

| Command | Does |
| --- | --- |
| `rover snap` | One JPEG on stdout |
| `rover move L R MS` | Timed move |
| `rover pan DEG` | Pan |
| `rover stop` | Stop |
| `rover status` | Pack voltage and range |

There is no HTTP service. SSH already authenticates, runs commands and returns pictures, so nothing extra has to be written, secured or kept running. Pi-side logic is C++ from the start, with no dependencies beyond the standard library, so the later stages extend this tool. Snapshots shell out to the stock `rpicam-still` for now.

How Claude uses it: it runs `ssh rover rover snap > look.jpg`, looks at the picture and decides the next move. The hardware session on your laptop can do this directly. A phone chat linked to the laptop can only if its shell is allowed to reach LAN hosts.

Stage 2, later: an AprilTag on the biped's torso. A loop on the Pi keeps the tag centred, pan first and wheels second, and holds the stand-off distance. Add a `rover record` command then, so falls are captured at full frame rate. That loop is the core logic and stays in C++.

## Self-driving and SLAM (secondary goal)

These come after the camera works, but the rover needs two things it does not have: a way to measure its own motion and a sensor that can see walls.

1. **Remote-driven camera.** The build above. No new parts.
2. **Tag follow.** Keeps the biped in frame. No new parts.
3. **Self-driving with obstacle avoidance.** Needs wheel encoders, because the TT gearmotors have none. Slotted-disc encoders for these motors cost about $5 a pair and go on D2 and D3, the ATmega's two interrupt pins, both free in this pin map. The ultrasonic sensor is enough for stopping short of things.
4. **SLAM.** Needs a 2D lidar on the top deck, about $70 to $100, on a Pi USB port. The ultrasonic sensor is too coarse to map with, and camera-only SLAM on a Pi 4 with this camera is heavy and fragile. A BNO085 like the biped's would steady the heading if one is spare.

Choices made now to keep that path open:

- The mast sits at the front edge, so the centre of the top deck stays clear for a lidar.
- D2 and D3 stay unused until the encoders arrive.
- The Pi code is C++, the language of the usual lidar SLAM stack (ROS 2 with slam\_toolbox). The serial code in `rover` becomes the base driver there.

One decision is deferred: the Pi's OS. Raspberry Pi OS is quickest for the camera today, while ROS 2 installs most easily on Ubuntu. Start on Raspberry Pi OS and revisit at stage 4. Part prices and the ROS 2 note are from memory, not looked up.

## Safety

The limits sit in the aLaMode firmware, so nothing sent from the Pi can override them.

- **Timed moves only.** Every move ends by itself within 2 seconds. A stalled Pi, dropped Wi-Fi or slow Claude means a stopped rover.
- **Speed cap.** PWM is limited to about 60 percent.
- **Stand-off.** Forward motion is refused when the front sensor reads under 80 cm. The biped is 56 cm to the top of its head (robot repo, DESIGN.md), so a fall cannot reach the rover.
- **No self-driving in stage 1.** The rover moves only on an explicit command.
- **Low pack.** No driving below 10.5 V.
- **Hard off.** The main switch cuts everything.

## Buy list

About $50 in total with the Pololu regulators, or $57 with both optional items. Regulator prices were checked on 2026-11-22; the rest are approximate, from memory.

Regulators: [Pololu D36V28F5](https://www.pololu.com/product/3782) and [Pololu D36V28F6](https://www.pololu.com/product/3783). Both take up to 50 V in and have reverse-voltage protection. Generic adjustable bucks cost a few dollars each and would also work, with less trustworthy ratings.

| Item | Why | Approx. |
| --- | --- | --- |
| Pololu D36V28F5, 5 V 3.2 A buck (add a USB-C plug lead) | Pi 4 supply from the pack | $22 |
| Pololu D36V28F6, 6 V 2.7 A buck | Motor and servo rail | $23 |
| Inline blade fuse holder with a 3 A fuse | Short-circuit protection for the pack | $3 |
| 26-pin stacking header | Only if the aLaMode fouls the Pi 4's heatsinks or jacks | $3 |
| 30 cm camera ribbon | Only if the stock ribbon will not reach the mast | $4 |
| XT60 male pigtail | Pack to fuse, if not already on hand | $2 |

## Build order

Each step has one check before moving on.

1. **Pi and camera alone.** Flash the OS, connect the camera. Check: `rpicam-still -o test.jpg` gives a picture.
2. **aLaMode on the Pi, no shield.** Enable the UART, turn the serial console off, flash a blink sketch. Check: the D13 LED blinks.
3. **Power.** Pack, fuse, switch, both bucks, nothing connected downstream. Check: 5.0 to 5.2 V and 6.0 V on a meter.
4. **Shield and motors, wheels off the ground.** Check: `M 120 120 1000` turns both wheels forward for one second, then stops.
5. **Servo.** Check: `P 90` centres it.
6. **The rover tool.** Check: a snapshot and a move from the laptop with `ssh`.
7. **Claude in the loop.** Check: Claude pulls a snapshot through the laptop and describes the room.
8. **Distance sensor and stand-off.** Check: the rover refuses to drive at a wall inside 80 cm.
9. **Floor test with the biped powered off**, then on.
10. **Stage 2:** tag tracking and recording.

## Open questions

Settled on 2026-11-22: fused pigtail and no power-lite board; the servo is a standard one; stand-off is 80 cm from the biped's 56 cm height; SSH and no HTTP service; Pi-side code in C++; self-driving and SLAM are a secondary goal.

- [ ] Which camera is in the box, and is its ribbon there?
- [ ] Which ultrasonic sensors do you have: 4-pin HC-SR04 or 3-pin Parallax Ping)))?
- [ ] Does the aLaMode seat on the Pi 4 with the heatsinks fitted?
- [ ] What does the aLaMode's U6\_OE jumper do? The datasheet does not say; check the KiCad files in WyoLum's repo before enabling Pi SPI.
- [ ] Is the chassis toggle switch rated for 3 A DC?
- [ ] Which Claude session will drive the rover, and can it reach the Pi by SSH?
