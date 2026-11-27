# Simulated twin: MuJoCo rover driven through a pty by the real firmware core.
PY ?= $(ROBO_CONTROL_DIR)/.venv/bin/python

.PHONY: sim-test twin

sim-test: rover-core-lib
	$(PY) -m pytest sim/tests -q

twin: rover-core-lib
	$(PY) sim/rover_twin.py --pty-link build/rover.pty
