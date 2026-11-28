##@ Build
.PHONY: all generated robo-control pi

# What the tests and the console build from: the firmware core library, roverd and the Pi programs.
generated: robo-control pi _roverd_units ## everything the tests build from: the firmware core library, roverd, pi/rover and pi/rovercam

# The contract with the robo-control repo (its docs/interfaces.md section 3): what its console needs.
robo-control: rover-core-lib roverd ## what the robo-control console needs: the twin's firmware library and roverd

pi: pi/rover pi/rovercam ## build pi/rover and pi/rovercam locally (a compile check off the Pi)

pi/rover: pi/rover.cpp pi/rover_conf.hpp
	$(CXX) $(CXXFLAGS) $(PI_WARN) -o $@ $<

pi/rovercam: pi/rovercam.cpp pi/rover_conf.hpp
	$(CXX) $(CXXFLAGS) $(PI_WARN) -pthread -o $@ $<
