# rover-cam build. `make help` lists targets.

# Everything for the rover is built on the Pi, in ~/$(PI_DIR) there.
PI_HOST   ?= rover.local
PI_DIR    ?= rover-cam
PI_PREFIX ?= /usr/local
PI_SERIAL ?= /dev/serial0
CXX       ?= g++
CXXFLAGS  ?= -std=c++17 -O2 -Wall

# Firmware: aLaMode = Arduino Uno. Arduino ships avr-gcc for the Pi (arm64)
# but only for Intel Macs, so it compiles on the Pi too.
FQBN     ?= arduino:avr:uno
FW_BUILD := $(PI_DIR)/build/firmware
CLI_URL  := https://downloads.arduino.cc/arduino-cli/arduino-cli_latest_Linux_ARM64.tar.gz

.PHONY: all help pi test sync deploy firmware firmware-deps upload clean

all: pi

help:
	@echo "make pi             build pi/rover locally (compile check off the Pi)"
	@echo "make test           build pi/rover and check its argument handling"
	@echo "make deploy         build pi/rover.cpp on PI_HOST=$(PI_HOST), install to $(PI_PREFIX)/bin"
	@echo "make firmware-deps  install arduino-cli, the AVR core, AFMotor and Servo on PI_HOST"
	@echo "make firmware       compile firmware/rover on PI_HOST into ~/$(FW_BUILD)"
	@echo "make upload         compile on PI_HOST, then flash the aLaMode from there"
	@echo "make clean          remove the local build"

pi: pi/rover

pi/rover: pi/rover.cpp
	$(CXX) $(CXXFLAGS) -o $@ $<

# Exit codes without hardware: 2 = usage error.
test: pi/rover
	@./pi/rover >/dev/null 2>&1; test $$? -eq 2 || { echo "FAIL: no args should exit 2"; exit 1; }
	@./pi/rover bogus >/dev/null 2>&1; test $$? -eq 2 || { echo "FAIL: unknown command should exit 2"; exit 1; }
	@./pi/rover 2>&1 | grep -q '^usage: rover' || { echo "FAIL: usage text missing"; exit 1; }
	@echo "pi/rover: ok"

sync:
	rsync -a --delete --exclude /pi/rover pi firmware $(PI_HOST):$(PI_DIR)/

deploy: sync
	ssh $(PI_HOST) 'g++ $(CXXFLAGS) -o $(PI_DIR)/pi/rover $(PI_DIR)/pi/rover.cpp && sudo install -m 755 $(PI_DIR)/pi/rover $(PI_PREFIX)/bin/rover'

firmware: sync
	ssh $(PI_HOST) 'arduino-cli compile --fqbn $(FQBN) --output-dir $(FW_BUILD) $(PI_DIR)/firmware/rover'

firmware-deps:
	ssh $(PI_HOST) 'command -v arduino-cli >/dev/null || { curl -fsSL $(CLI_URL) | tar -xz -C /tmp arduino-cli && sudo install -m 755 /tmp/arduino-cli /usr/local/bin/; }'
	ssh $(PI_HOST) 'arduino-cli core update-index && arduino-cli core install arduino:avr && arduino-cli lib install "Adafruit Motor Shield library" Servo'

upload: firmware
	ssh $(PI_HOST) SERIAL=$(PI_SERIAL) sh -s $(FW_BUILD)/rover.ino.hex < pi/flash-alamode.sh

clean:
	rm -f pi/rover
