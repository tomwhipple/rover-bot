##@ On the Pi: these act on PI_HOST over ssh; the sources are copied to ~/PI_DIR first
.PHONY: sync deploy deployed hw-check firmware firmware-deps upload

# By checksum, without the times: a file whose content changed lands newer than the last build on
# the Pi, whatever its time here, and an unchanged one keeps its time, so nothing rebuilds.
PI_RSYNC := rsync -rlpc --delete --exclude __pycache__

sync:
	$(PI_RSYNC) --exclude /pi/rover --exclude /pi/rovercam pi firmware $(PI_HOST):$(PI_DIR)/

# The deployed record says whether the sources differed from their commits: this repo's pi/,
# firmware/ and robo/ (the description), robo-control's core/ and schemas/.
_git_dirty = $$(git -C $(1) status --porcelain -- $(2) | grep -q . && echo 1 || echo 0)

deploy: sync ## build rover, rovercam and roverd on PI_HOST, install what changed with its unit, restart what changed; record the commits
	@test -f $(ROBO_CONTROL_DIR)/core/CMakeLists.txt || \
	  { echo "deploy: ROBO_CONTROL_DIR=$(ROBO_CONTROL_DIR) has no core/CMakeLists.txt"; exit 1; }
	ssh $(PI_HOST) mkdir -p $(PI_RC_DIR)
	$(PI_RSYNC) --exclude build $(ROBO_CONTROL_DIR)/core $(ROBO_CONTROL_DIR)/schemas $(PI_HOST):$(PI_RC_DIR)/
	ssh $(PI_HOST) "PI_DIR=$(PI_DIR) RC_DIR=$(PI_RC_DIR) PREFIX=$(PI_PREFIX) CONF=$(PI_CONF) \
	  CXXFLAGS='$(CXXFLAGS)' \
	  ROVER_COMMIT=$$(git rev-parse HEAD) ROVER_DIRTY=$(call _git_dirty,.,pi firmware robo) \
	  RC_COMMIT=$$(git -C $(ROBO_CONTROL_DIR) rev-parse HEAD) RC_DIRTY=$(call _git_dirty,$(ROBO_CONTROL_DIR),core schemas) \
	  sh -s" < pi/deploy.sh

deployed: ## what PI_HOST runs: the deployed record, roverd's version and arguments, the firmware's hello, the services
	@ssh $(PI_HOST) 'cat $(PI_PREFIX)/share/rover/deployed.json 2>/dev/null || echo "no deployed record"; \
	  $(PI_PREFIX)/bin/roverd --version; \
	  echo "running: $$(ps -o args= -C roverd)"; \
	  echo "firmware: $$(journalctl -u roverd --no-pager -o cat | grep "FIRMWARE_HELLO" | tail -1)"; \
	  for s in roverd rovercam; do \
	    echo "$$s: $$(systemctl is-active $$s), $$(systemctl is-enabled $$s), since $$(systemctl show -P ActiveEnterTimestamp $$s)"; \
	  done'

# Against the real rover on the default bus namespace. It moves the pan; it refuses to send motion
# unless the firmware reports LOW_PACK (the drive motors unpowered).
hw-check: ## the on-hardware command-path check; motors must be unpowered (tools/hw_check.py, ARGS: its options)
	PYTHONPATH=$(abspath $(ROBO_CONTROL_DIR)):$(abspath $(ROBO_CONTROL_DIR))/build/gen/py \
	  $(PY) tools/hw_check.py $(ARGS)

firmware: sync ## compile firmware/rover on PI_HOST into ~/rover-cam/build/firmware
	ssh $(PI_HOST) 'arduino-cli compile --fqbn $(FQBN) --output-dir $(FW_BUILD) $(PI_DIR)/firmware/rover'

firmware-deps: ## install arduino-cli, the AVR core, AFMotor and Servo on PI_HOST
	ssh $(PI_HOST) 'command -v arduino-cli >/dev/null || { curl -fsSL $(CLI_URL) | tar -xz -C /tmp arduino-cli && sudo install -m 755 /tmp/arduino-cli /usr/local/bin/; }'
	ssh $(PI_HOST) 'arduino-cli core update-index && arduino-cli core install arduino:avr && arduino-cli lib install "Adafruit Motor Shield library" Servo'

upload: firmware ## compile on PI_HOST, then flash the aLaMode from there; roverd, which shares the port, is stopped meanwhile
	ssh $(PI_HOST) 'a=$$(systemctl is-active roverd); [ "$$a" != active ] || sudo systemctl stop roverd; \
	  SERIAL=$(PI_SERIAL) sh -s $(FW_BUILD)/rover.ino.hex; st=$$?; \
	  [ "$$a" != active ] || sudo systemctl start roverd; exit $$st' < pi/flash-alamode.sh
