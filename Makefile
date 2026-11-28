# rover-cam. `make` lists the targets and variables.
#
# GNU Make 3.81 (macOS /usr/bin/make) or newer. Runs from the repo root.

.DEFAULT_GOAL := help

##@ Variables
## PI_HOST=host   the Pi's ssh name (default rover.local): deploy, deployed, firmware-deps, firmware, upload
## PI_DIR=dir     the checkout's directory on the Pi, under ~ (default rover-cam)
## PI_RC_DIR=dir  where deploy puts robo-control's core/ and schemas/ on the Pi, under ~ (default robo-control)
## SIM=0|1        console: 1 (default) opens the console on the local simulation, 0 on the real rover
## ARGS="..."     more arguments for the program behind the target (console, hw-check)
## ROBO_CONTROL_DIR=dir  the robo-control checkout (default ../robo-control): roverd, console, tests, deploy, hw-check
## PY=path        the Python interpreter (default: robo-control's .venv): sim-test, roverd-test, mypy, hw-check
## PYTEST_WORKERS=n  pytest-xdist workers for sim-test and roverd-test (default 8, a capped count; 0 = one process)
## PREPUSH_SERIAL=1  prepush: run the gates one at a time, not side by side
## ROVERD_SANITIZE=1  build roverd with ASan/UBSan into build/roverd-asan (roverd, roverd-test)

# Everything for the rover is built on the Pi, in ~/$(PI_DIR) there.
PI_HOST   ?= rover.local
PI_DIR    ?= rover-cam
PI_RC_DIR ?= robo-control
PI_PREFIX ?= /usr/local
PI_SERIAL ?= /dev/serial0
PI_CONF   ?= /etc/rover.conf
CXX       ?= g++
CXXFLAGS  ?= -std=c++17 -O2 -Wall
# Added to the local builds (`make pi`, `make pi-test`, the pre-push hook) but not to `make deploy`,
# so a newer compiler on the Pi cannot break a deploy over a new warning.
PI_WARN   ?= -Wextra -Werror

# Firmware: aLaMode = Arduino Uno. Arduino ships avr-gcc for the Pi (arm64)
# but only for Intel Macs, so it compiles on the Pi too.
FQBN     ?= arduino:avr:uno
FW_BUILD := $(PI_DIR)/build/firmware
CLI_URL  := https://downloads.arduino.cc/arduino-cli/arduino-cli_latest_Linux_ARM64.tar.gz

# The console and bus live in the robo-control repo, beside this one.
ROBO_CONTROL_DIR ?= ../robo-control
PY               ?= $(ROBO_CONTROL_DIR)/.venv/bin/python
SIM              ?= 1

include mk/run.mk mk/build.mk mk/firmware.mk mk/roverd.mk mk/pi.mk mk/test.mk mk/check.mk mk/housekeeping.mk
