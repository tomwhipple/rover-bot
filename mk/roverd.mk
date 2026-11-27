# roverd: the rover's bus adapter (pi/roverd), built with CMake against robo-control's core/ and
# schemas/. ROBO_CONTROL_DIR is the robo-control checkout; PY a Python with eclipse-zenoh,
# protobuf, pytest and pytest-timeout. ROVERD_SANITIZE=1 builds with ASan/UBSan into
# build/roverd-asan. Both are RelWithDebInfo: protobuf's generated code must agree with the
# installed (release) libprotobuf on NDEBUG.
_roverd_prev_goal := $(.DEFAULT_GOAL)

ROBO_CONTROL_DIR ?= ../robo-control
PY               ?= $(ROBO_CONTROL_DIR)/.venv/bin/python
ROVERD_SANITIZE  ?= 0

_roverd_asan  := $(filter 1,$(ROVERD_SANITIZE))
ROVERD_BUILD  := build/roverd$(if $(_roverd_asan),-asan)
ROVERD_BIN    := $(ROVERD_BUILD)/roverd
_roverd_rc    := $(abspath $(ROBO_CONTROL_DIR))
_roverd_flags := -DROBO_CONTROL_DIR=$(_roverd_rc) -DROBO_SANITIZE=$(if $(_roverd_asan),ON,OFF) \
                 -DCMAKE_BUILD_TYPE=RelWithDebInfo
_roverd_proto := $(_roverd_rc)/schemas/robo_v1.proto
_roverd_py    := $(ROVERD_BUILD)/py
_roverd_deps  := $(wildcard pi/roverd/CMakeLists.txt pi/roverd/src/*.cpp pi/roverd/src/*.hpp \
                   $(_roverd_rc)/core/CMakeLists.txt $(_roverd_rc)/core/src/*.cpp \
                   $(_roverd_rc)/core/include/robo/*.hpp) $(_roverd_proto)

.PHONY: roverd roverd-test _roverd_force

# Rewritten only when the CMake flags change, so changing them reconfigures.
$(ROVERD_BUILD)/.vars: _roverd_force
	@mkdir -p $(@D)
	@echo '$(_roverd_flags)' | cmp -s - $@ || echo '$(_roverd_flags)' > $@

$(ROVERD_BIN): $(_roverd_deps) $(ROVERD_BUILD)/.vars
	cmake -S pi/roverd -B $(ROVERD_BUILD) $(_roverd_flags) >/dev/null
	cmake --build $(ROVERD_BUILD) --parallel --target roverd
	@touch $@

roverd: $(ROVERD_BIN) ## build pi/roverd (the rover's bus adapter) into build/roverd

$(_roverd_py)/robo_v1_pb2.py: $(_roverd_proto)
	@mkdir -p $(@D)
	protoc -I$(_roverd_rc)/schemas --python_out=$(@D) $<

roverd-test: $(ROVERD_BIN) $(_roverd_py)/robo_v1_pb2.py ## build roverd and run its unit and bus tests
	cmake --build $(ROVERD_BUILD) --parallel --target roverd_units
	$(ROVERD_BUILD)/roverd_units
	ROVERD_BIN=$(abspath $(ROVERD_BIN)) PYTHONPATH=$(abspath $(_roverd_py)) PYTHONDONTWRITEBYTECODE=1 \
	  $(PY) -m pytest -q -p no:cacheprovider pi/roverd/tests

.DEFAULT_GOAL := $(_roverd_prev_goal)
