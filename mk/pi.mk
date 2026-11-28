##@ On the Pi: these act on PI_HOST over ssh; the sources are copied to ~/PI_DIR first
.PHONY: sync deploy firmware firmware-deps upload

sync:
	rsync -a --delete --exclude /pi/rover --exclude /pi/rovercam pi firmware $(PI_HOST):$(PI_DIR)/

deploy: sync ## build pi/rover and pi/rovercam on PI_HOST, install to /usr/local/bin; /etc/rover.conf if absent
	ssh $(PI_HOST) 'g++ $(CXXFLAGS) -o $(PI_DIR)/pi/rover $(PI_DIR)/pi/rover.cpp && g++ $(CXXFLAGS) -pthread -o $(PI_DIR)/pi/rovercam $(PI_DIR)/pi/rovercam.cpp && sudo install -m 755 $(PI_DIR)/pi/rover $(PI_DIR)/pi/rovercam $(PI_PREFIX)/bin/ && { test -e $(PI_CONF) || sudo install -m 644 $(PI_DIR)/pi/rover.conf $(PI_CONF); }'

firmware: sync ## compile firmware/rover on PI_HOST into ~/rover-cam/build/firmware
	ssh $(PI_HOST) 'arduino-cli compile --fqbn $(FQBN) --output-dir $(FW_BUILD) $(PI_DIR)/firmware/rover'

firmware-deps: ## install arduino-cli, the AVR core, AFMotor and Servo on PI_HOST
	ssh $(PI_HOST) 'command -v arduino-cli >/dev/null || { curl -fsSL $(CLI_URL) | tar -xz -C /tmp arduino-cli && sudo install -m 755 /tmp/arduino-cli /usr/local/bin/; }'
	ssh $(PI_HOST) 'arduino-cli core update-index && arduino-cli core install arduino:avr && arduino-cli lib install "Adafruit Motor Shield library" Servo'

upload: firmware ## compile on PI_HOST, then flash the aLaMode from there
	ssh $(PI_HOST) SERIAL=$(PI_SERIAL) sh -s $(FW_BUILD)/rover.ino.hex < pi/flash-alamode.sh
