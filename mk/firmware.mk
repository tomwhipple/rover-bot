# Rover firmware core: host library and host test. Standalone:
#   make -f mk/firmware.mk firmware-host-test rover-core-lib
CORE_BUILD ?= build
FW_CXX   ?= c++
FW_STD   := -std=c++11 -Wall -Wextra -Werror

ifeq ($(shell uname -s),Darwin)
FW_SOEXT := dylib
else
FW_SOEXT := so
endif

FW_LIB  := $(CORE_BUILD)/librover_core.$(FW_SOEXT)
FW_TEST := $(CORE_BUILD)/test_rover_core
FW_SRCS := firmware/rover/rover_core.cpp
FW_HDRS := firmware/rover/rover_core.h

.PHONY: rover-core-lib firmware-host-build firmware-host-test

rover-core-lib: $(FW_LIB)

$(FW_LIB): $(FW_SRCS) $(FW_HDRS)
	@mkdir -p $(CORE_BUILD)
	$(FW_CXX) $(FW_STD) -O2 -fPIC -shared -fvisibility=hidden -o $@ $(FW_SRCS)

$(FW_TEST): firmware/host/test_rover_core.cpp $(FW_SRCS) $(FW_HDRS)
	@mkdir -p $(CORE_BUILD)
	$(FW_CXX) $(FW_STD) -O1 -g -fsanitize=address,undefined -fno-sanitize-recover=undefined \
	  -o $@ firmware/host/test_rover_core.cpp $(FW_SRCS)

firmware-host-build: $(FW_TEST) ## build the host test without running it

$(CORE_BUILD)/.firmware-host-test.ok: $(FW_TEST)
	$(FW_TEST)
	@touch $@

firmware-host-test: $(CORE_BUILD)/.firmware-host-test.ok
