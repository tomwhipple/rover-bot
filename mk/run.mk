##@ Run
.PHONY: console sim

# The console is robo-control's; it builds this repo's side (make robo-control) before it opens.
console: ## open the rover's console, on its local simulation (SIM=0: the real rover), through the robo-control repo
	$(MAKE) -C $(ROBO_CONTROL_DIR) console ROBOT=rover SIM=$(SIM) ARGS="$(ARGS)"

# Simulated twin: MuJoCo rover driven through a pty by the real firmware core.
sim: rover-core-lib ## run the simulated twin alone (MuJoCo rover on the real firmware core), on a pty
	$(PY) sim/rover_twin.py --pty-link build/rover.pty
