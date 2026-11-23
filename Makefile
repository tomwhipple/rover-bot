# rover-cam build. `make help` lists targets.

# Pi side
PI_HOST   ?= rover.local
PI_PREFIX ?= /usr/local
CXX       ?= g++
CXXFLAGS  ?= -std=c++17 -O2 -Wall

# Firmware side (aLaMode = Arduino Uno, flashed from the Pi over its UART)
ARDUINO_CLI ?= arduino-cli
PI_SERIAL   ?= /dev/serial0
FQBN        ?= arduino:avr:uno
FW_BUILD    := build/firmware
FW_HEX      := $(FW_BUILD)/rover.ino.hex

.PHONY: all help pi test deploy firmware firmware-deps upload clean

all: pi firmware

help:
	@echo "make pi             build pi/rover locally (compile check off the Pi)"
	@echo "make test           build pi/rover and check its argument handling"
	@echo "make deploy         copy pi/rover.cpp to PI_HOST=$(PI_HOST), build there, install to $(PI_PREFIX)/bin"
	@echo "make firmware-deps  install the AVR core, AFMotor and Servo with arduino-cli"
	@echo "make firmware       compile firmware/rover for $(FQBN) into $(FW_BUILD)"
	@echo "make upload         copy the firmware to PI_HOST and flash the aLaMode from there"
	@echo "make clean          remove build output"

pi: pi/rover

pi/rover: pi/rover.cpp
	$(CXX) $(CXXFLAGS) -o $@ $<

# Exit codes without hardware: 2 = usage error.
test: pi/rover
	@./pi/rover >/dev/null 2>&1; test $$? -eq 2 || { echo "FAIL: no args should exit 2"; exit 1; }
	@./pi/rover bogus >/dev/null 2>&1; test $$? -eq 2 || { echo "FAIL: unknown command should exit 2"; exit 1; }
	@./pi/rover 2>&1 | grep -q '^usage: rover' || { echo "FAIL: usage text missing"; exit 1; }
	@echo "pi/rover: ok"

deploy:
	ssh $(PI_HOST) 'mkdir -p ~/src/rover-cam'
	scp pi/rover.cpp $(PI_HOST):src/rover-cam/rover.cpp
	ssh $(PI_HOST) 'g++ $(CXXFLAGS) -o src/rover-cam/rover src/rover-cam/rover.cpp && sudo install -m 755 src/rover-cam/rover $(PI_PREFIX)/bin/rover'

firmware: $(FW_HEX)

$(FW_HEX): firmware/rover/rover.ino
	@command -v $(ARDUINO_CLI) >/dev/null || { echo "$(ARDUINO_CLI) not found (brew install arduino-cli)"; exit 1; }
	$(ARDUINO_CLI) compile --fqbn $(FQBN) --output-dir $(FW_BUILD) firmware/rover

firmware-deps:
	$(ARDUINO_CLI) core update-index
	$(ARDUINO_CLI) core install arduino:avr
	$(ARDUINO_CLI) lib install "Adafruit Motor Shield library" Servo

upload: $(FW_HEX)
	scp $(FW_HEX) $(PI_HOST):/tmp/rover.ino.hex
	ssh $(PI_HOST) SERIAL=$(PI_SERIAL) sh -s /tmp/rover.ino.hex < pi/flash-alamode.sh

clean:
	rm -rf build pi/rover
