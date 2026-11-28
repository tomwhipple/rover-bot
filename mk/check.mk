# The pre-push hook's checks (.githooks/pre-push -> `make check` -> tools/check.sh), and the
# static analysis they run: cppcheck and clang-tidy over the C++, mypy over the Python.
CPPCHECK   ?= cppcheck
CLANG_TIDY ?= clang-tidy
TIDY_STD   := -std=c++17 -Wall -Wextra

CPP_DIRS      := firmware pi
CPP_TIDY_PLAIN:= firmware/rover/rover_core.cpp firmware/host/test_rover_core.cpp pi/rover.cpp pi/rovercam.cpp
CPP_TIDY_ROVERD := pi/roverd/src/clock_map.cpp pi/roverd/src/main.cpp pi/roverd/src/protocol.cpp \
                   pi/roverd/src/roverd.cpp pi/roverd/src/serial_port.cpp pi/roverd/tests/test_units.cpp
PY_CHECKED    := sim pi/roverd/tests $(wildcard firmware/pan_cal/*.py)

.PHONY: hooks check cppcheck tidy mypy sketch-compile

# core.hooksPath is relative, so it holds in every worktree of the clone.
hooks:
	git config core.hooksPath .githooks
	@echo "pre-push hook enabled (core.hooksPath = .githooks); make check runs the same checks by hand"

check:
	sh tools/check.sh

# The sketch, where arduino-cli and the AVR core are installed (the Pi; make firmware-deps).
sketch-compile:
	arduino-cli compile --fqbn $(FQBN) --output-dir build/firmware firmware/rover

# Warnings and portability findings fail. rover.ino is Arduino C++ (setup/loop, no main): cppcheck
# is told they are used.
cppcheck:
	@mkdir -p build/cppcheck
	$(CPPCHECK) --enable=warning,portability --error-exitcode=1 --inline-suppr -q --std=c++17 \
	  --cppcheck-build-dir=build/cppcheck $(CPP_DIRS)

# clang-tidy with the checks in .clang-tidy; roverd with the flags CMake compiled it with.
tidy: $(ROVERD_BIN)
	$(CLANG_TIDY) -quiet firmware/rover/rover_core.cpp firmware/host/test_rover_core.cpp -- -std=c++11 -Wall -Wextra
	$(CLANG_TIDY) -quiet pi/rover.cpp -- $(TIDY_STD)
	$(CLANG_TIDY) -quiet pi/rovercam.cpp -- $(TIDY_STD) -pthread
	cmake --build $(ROVERD_BUILD) --target roverd_units >/dev/null
	$(CLANG_TIDY) -quiet -p $(ROVERD_BUILD) $(CPP_TIDY_ROVERD)

mypy:
	@$(PY) -m mypy --version >/dev/null 2>&1 || { echo "mypy is not installed for $(PY): install it in the robo-control venv"; exit 1; }
	$(PY) -m mypy --cache-dir build/mypy $(PY_CHECKED)
