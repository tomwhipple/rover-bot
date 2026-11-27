# rover-cam build. `make help` lists targets.

# Everything for the rover is built on the Pi, in ~/$(PI_DIR) there.
PI_HOST   ?= rover.local
PI_DIR    ?= rover-cam
PI_PREFIX ?= /usr/local
PI_SERIAL ?= /dev/serial0
PI_CONF   ?= /etc/rover.conf
CXX       ?= g++
CXXFLAGS  ?= -std=c++17 -O2 -Wall

# Firmware: aLaMode = Arduino Uno. Arduino ships avr-gcc for the Pi (arm64)
# but only for Intel Macs, so it compiles on the Pi too.
FQBN     ?= arduino:avr:uno
FW_BUILD := $(PI_DIR)/build/firmware
CLI_URL  := https://downloads.arduino.cc/arduino-cli/arduino-cli_latest_Linux_ARM64.tar.gz

# The console and bus live in the robo-control repo, beside this one.
ROBO_CONTROL_DIR ?= ../robo-control
PY               ?= $(ROBO_CONTROL_DIR)/.venv/bin/python

.PHONY: all help pi test sync deploy firmware firmware-deps upload clean robo-control robo-test

all: pi

help:
	@echo "make pi             build pi/rover locally (compile check off the Pi)"
	@echo "make test           build pi/rover and check its argument handling"
	@echo "make deploy         build pi/rover.cpp on PI_HOST=$(PI_HOST), install to $(PI_PREFIX)/bin; $(PI_CONF) if absent"
	@echo "make firmware-deps  install arduino-cli, the AVR core, AFMotor and Servo on PI_HOST"
	@echo "make firmware       compile firmware/rover on PI_HOST into ~/$(FW_BUILD)"
	@echo "make upload         compile on PI_HOST, then flash the aLaMode from there"
	@echo "make clean          remove the local build"
	@echo "make robo-control   build what the robo-control console needs: roverd and the twin's firmware library"
	@echo "make robo-test      host tests: firmware core, twin, roverd"
	@echo "make twin           run the simulated twin alone"

pi: pi/rover

pi/rover: pi/rover.cpp
	$(CXX) $(CXXFLAGS) -o $@ $<

# Exit codes without hardware: 2 = usage error.
test: pi/rover
	@./pi/rover >/dev/null 2>&1; test $$? -eq 2 || { echo "FAIL: no args should exit 2"; exit 1; }
	@./pi/rover bogus >/dev/null 2>&1; test $$? -eq 2 || { echo "FAIL: unknown command should exit 2"; exit 1; }
	@./pi/rover 2>&1 | grep -q '^usage: rover' || { echo "FAIL: usage text missing"; exit 1; }
	@mkdir -p build/test/bin && printf '#!/bin/sh\necho "$$@"\n' > build/test/bin/rpicam-still && chmod +x build/test/bin/rpicam-still
	@PATH="build/test/bin:$$PATH" ROVER_CONF=pi/rover.conf ./pi/rover snap | grep -q -- '-t 500 --width 1296 --height 972 --rotation 180$$' || { echo "FAIL: snap does not pass pi/rover.conf"; exit 1; }
	@printf 'snap_rotation = 90\n' > build/test/bad.conf; PATH="build/test/bin:$$PATH" ROVER_CONF=build/test/bad.conf ./pi/rover snap >/dev/null 2>&1; test $$? -eq 1 || { echo "FAIL: a bad setting should exit 1"; exit 1; }
	@printf 'snap_rotatoin = 180\n' > build/test/bad.conf; PATH="build/test/bin:$$PATH" ROVER_CONF=build/test/bad.conf ./pi/rover snap >/dev/null 2>&1; test $$? -eq 1 || { echo "FAIL: an unknown key should exit 1"; exit 1; }
	@echo "pi/rover: ok"

sync:
	rsync -a --delete --exclude /pi/rover pi firmware $(PI_HOST):$(PI_DIR)/

deploy: sync
	ssh $(PI_HOST) 'g++ $(CXXFLAGS) -o $(PI_DIR)/pi/rover $(PI_DIR)/pi/rover.cpp && sudo install -m 755 $(PI_DIR)/pi/rover $(PI_PREFIX)/bin/rover && { test -e $(PI_CONF) || sudo install -m 644 $(PI_DIR)/pi/rover.conf $(PI_CONF); }'

firmware: sync
	ssh $(PI_HOST) 'arduino-cli compile --fqbn $(FQBN) --output-dir $(FW_BUILD) $(PI_DIR)/firmware/rover'

firmware-deps:
	ssh $(PI_HOST) 'command -v arduino-cli >/dev/null || { curl -fsSL $(CLI_URL) | tar -xz -C /tmp arduino-cli && sudo install -m 755 /tmp/arduino-cli /usr/local/bin/; }'
	ssh $(PI_HOST) 'arduino-cli core update-index && arduino-cli core install arduino:avr && arduino-cli lib install "Adafruit Motor Shield library" Servo'

upload: firmware
	ssh $(PI_HOST) SERIAL=$(PI_SERIAL) sh -s $(FW_BUILD)/rover.ino.hex < pi/flash-alamode.sh

clean:
	rm -f pi/rover
	rm -rf build

robo-control: rover-core-lib roverd

robo-test: firmware-host-test sim-test roverd-test

include mk/*.mk
