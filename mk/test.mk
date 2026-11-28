##@ Tests: prepush runs them side by side; each runs alone
.PHONY: test host sim-test roverd-test pi-test

# pytest-xdist workers: a capped count, never `-n auto`. PYTEST_WORKERS=0 runs one process.
# --dist loadgroup keeps each @pytest.mark.xdist_group on one worker. A venv without
# pytest-xdist runs one process, with a warning.
PYTEST_WORKERS ?= 8
PYTEST_PAR = $(if $(filter 0,$(PYTEST_WORKERS)),-p no:xdist,-n $(PYTEST_WORKERS) --dist loadgroup)

# $(call run_pytest,<env assignments>,<pytest arguments>)
define run_pytest
	@par='$(PYTEST_PAR)'; \
	if [ '$(PYTEST_WORKERS)' != 0 ] && ! $(PY) -c 'import xdist' 2>/dev/null; then \
	  echo "WARNING: pytest-xdist is not installed for $(PY); running in one process" >&2; \
	  par=''; \
	fi; \
	echo "$(PY) -m pytest -q $$par $(2)"; \
	$(1) $(PY) -m pytest -q -p no:cacheprovider $$par $(2)
endef

test: host sim-test roverd-test pi-test ## every test that runs on this machine: host, sim-test, roverd-test, pi-test

host: $(CORE_BUILD)/.host.ok ## the firmware core's host test, under ASan/UBSan

sim-test: rover-core-lib ## the simulated twin's tests, in parallel workers (ROVER_CORE_LIB=path picks another firmware library)
	$(call run_pytest,PYTHONDONTWRITEBYTECODE=1,sim/tests)

roverd-test: _roverd_units $(_roverd_py)/robo_v1_pb2.py ## build roverd and run its unit tests, then its bus tests in parallel workers
	$(ROVERD_BUILD)/roverd_units
	$(call run_pytest,ROVERD_BIN=$(abspath $(ROVERD_BIN)) PYTHONPATH=$(abspath $(_roverd_py)) PYTHONDONTWRITEBYTECODE=1,pi/roverd/tests)

# Exit codes without hardware: 2 = usage error.
pi-test: pi/rover pi/rovercam ## pi/rover's argument and config handling and pi/rovercam's, without hardware
	@./pi/rover >/dev/null 2>&1; test $$? -eq 2 || { echo "FAIL: no args should exit 2"; exit 1; }
	@./pi/rover bogus >/dev/null 2>&1; test $$? -eq 2 || { echo "FAIL: unknown command should exit 2"; exit 1; }
	@./pi/rover 2>&1 | grep -q '^usage: rover' || { echo "FAIL: usage text missing"; exit 1; }
	@mkdir -p build/test/bin && printf '#!/bin/sh\necho "$$@"\n' > build/test/bin/rpicam-still && chmod +x build/test/bin/rpicam-still
	@PATH="build/test/bin:$$PATH" ROVER_CONF=pi/rover.conf ./pi/rover snap | grep -q -- '-t 500 --width 1296 --height 972 --rotation 180$$' || { echo "FAIL: snap does not pass pi/rover.conf"; exit 1; }
	@sh pi/test_rovercam.sh
	@printf 'camera_rotation = 90\n' > build/test/bad.conf; PATH="build/test/bin:$$PATH" ROVER_CONF=build/test/bad.conf ./pi/rover snap >/dev/null 2>&1; test $$? -eq 1 || { echo "FAIL: a bad setting should exit 1"; exit 1; }
	@printf 'camera_rotatoin = 180\n' > build/test/bad.conf; PATH="build/test/bin:$$PATH" ROVER_CONF=build/test/bad.conf ./pi/rover snap >/dev/null 2>&1; test $$? -eq 1 || { echo "FAIL: an unknown key should exit 1"; exit 1; }
	@echo "pi/rover: ok"
