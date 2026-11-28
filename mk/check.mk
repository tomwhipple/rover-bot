# The pre-push hook's checks (.githooks/pre-push -> `make prepush` -> tools/prepush.sh), and the
# static analysis they run: cppcheck and clang-tidy over the C++, mypy over the Python. Still the
# Tests section.
CPPCHECK   ?= cppcheck
CLANG_TIDY ?= clang-tidy
TIDY_STD   := -std=c++17 -Wall -Wextra

CPP_DIRS      := firmware pi
CPP_TIDY_PLAIN:= firmware/rover/rover_core.cpp firmware/host/test_rover_core.cpp pi/rover.cpp pi/rovercam.cpp
CPP_TIDY_ROVERD := pi/roverd/src/clock_map.cpp pi/roverd/src/main.cpp pi/roverd/src/protocol.cpp \
                   pi/roverd/src/roverd.cpp pi/roverd/src/serial_port.cpp pi/roverd/tests/test_units.cpp
PY_CHECKED    := sim pi/roverd/tests tools/hw_check.py $(wildcard firmware/pan_cal/*.py)

.PHONY: hooks prepush lint cppcheck tidy mypy sketch-compile

# core.hooksPath is relative, so it holds in every worktree of the clone.
hooks: ## enable the pre-push hook for every worktree of this clone (git config core.hooksPath .githooks)
	git config core.hooksPath .githooks
	@echo "pre-push hook enabled (core.hooksPath = .githooks); make prepush runs the same checks by hand"

# tools/prepush.sh is the body: `make generated`, then the gates (host, sim-test, roverd-test,
# pi-test, cppcheck, tidy, mypy; `make test` and `make lint` are the same ones) side by side, each
# with its own log, the skip switches (ROVER_HOOK_SKIP) and a missing-tool report.
prepush: ## every gate the pre-push hook runs, in its order: compile, test, cppcheck, tidy, mypy (tools/prepush.sh)
	sh tools/prepush.sh

lint: cppcheck tidy mypy ## all static analysis: cppcheck, tidy and mypy

# The sketch, where arduino-cli and the AVR core are installed (the Pi; make firmware-deps).
sketch-compile: ## compile the Arduino sketch here (needs arduino-cli and the AVR core: the Pi has them)
	arduino-cli compile --fqbn $(FQBN) --output-dir build/firmware firmware/rover

# Warnings and portability findings fail. rover.ino is Arduino C++ (setup/loop, no main): cppcheck
# is told they are used.
cppcheck: ## whole-program cppcheck over firmware/ and pi/; warnings and portability findings fail
	@mkdir -p build/cppcheck
	$(CPPCHECK) --enable=warning,portability --error-exitcode=1 --inline-suppr -q --std=c++17 \
	  --cppcheck-build-dir=build/cppcheck $(CPP_DIRS)

# clang-tidy with the checks in .clang-tidy; roverd with the flags CMake compiled it with.
tidy: _roverd_units ## clang-tidy (.clang-tidy) over the firmware core, pi/ and roverd
	$(CLANG_TIDY) -quiet firmware/rover/rover_core.cpp firmware/host/test_rover_core.cpp -- -std=c++11 -Wall -Wextra
	$(CLANG_TIDY) -quiet pi/rover.cpp -- $(TIDY_STD)
	$(CLANG_TIDY) -quiet pi/rovercam.cpp -- $(TIDY_STD) -pthread
	$(CLANG_TIDY) -quiet -p $(ROVERD_BUILD) $(CPP_TIDY_ROVERD)

mypy: ## mypy over sim/, roverd's tests and firmware/pan_cal (needs mypy in PY's environment)
	@$(PY) -m mypy --version >/dev/null 2>&1 || { echo "mypy is not installed for $(PY): install it in the robo-control venv"; exit 1; }
	$(PY) -m mypy --cache-dir build/mypy $(PY_CHECKED)
