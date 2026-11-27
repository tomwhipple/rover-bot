// roverd: the rover's bus adapter (robo-control docs/interfaces.md sections 1, 2, 4, 5).
#pragma once

#include <map>
#include <string>

namespace roverd {

struct Options {
    std::string serial;
    std::string robot_id = "rover";
    std::map<std::string, std::string> cameras;
    bool simulated = false;
    std::string description_commit;
};

// Opens the bus from the ROBO_* environment and runs until SIGINT/SIGTERM. Returns the exit code.
int run(const Options& opts);

const char* version();

}  // namespace roverd
