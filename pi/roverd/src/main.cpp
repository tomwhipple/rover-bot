#include <cstdio>
#include <cstring>
#include <string>

#include "roverd.hpp"

namespace {

void usage(FILE* out) {
    std::fprintf(out,
                 "usage: roverd --serial PATH [--robot-id ID] [--camera NAME=URL]... [--simulated]\n"
                 "              [--description-commit HASH]\n"
                 "       roverd --version\n"
                 "Bus settings come from ROBO_NS, ROBO_ZENOH_MODE, ROBO_ZENOH_LISTEN,\n"
                 "ROBO_ZENOH_CONNECT and ROBO_ZENOH_SCOUTING. Without ROBO_ZENOH_LISTEN it listens on\n"
                 "tcp/[::]:7447 (a free port if that one is taken).\n");
}

}  // namespace

int main(int argc, char** argv) {
    roverd::Options opts;
    for (int i = 1; i < argc; ++i) {
        const std::string a = argv[i];
        auto value = [&](const char* name) -> const char* {
            if (i + 1 >= argc) {
                std::fprintf(stderr, "roverd: %s needs a value\n", name);
                return nullptr;
            }
            return argv[++i];
        };
        if (a == "--version") {
            std::printf("roverd %s\n", roverd::version());
            return 0;
        } else if (a == "-h" || a == "--help") {
            usage(stdout);
            return 0;
        } else if (a == "--simulated") {
            opts.simulated = true;
        } else if (a == "--serial" || a == "--robot-id" || a == "--camera" || a == "--description-commit") {
            const char* v = value(a.c_str());
            if (!v) return 2;
            if (a == "--serial") {
                opts.serial = v;
            } else if (a == "--robot-id") {
                opts.robot_id = v;
            } else if (a == "--description-commit") {
                opts.description_commit = v;
            } else {
                const char* eq = std::strchr(v, '=');
                if (!eq || eq == v) {
                    std::fprintf(stderr, "roverd: --camera wants NAME=URL, got '%s'\n", v);
                    return 2;
                }
                opts.cameras[std::string(v, eq)] = std::string(eq + 1);
            }
        } else {
            std::fprintf(stderr, "roverd: unknown argument '%s'\n", a.c_str());
            usage(stderr);
            return 2;
        }
    }
    if (opts.serial.empty()) {
        usage(stderr);
        return 2;
    }
    if (opts.robot_id.empty() || opts.robot_id.find_first_of("/*$?#") != std::string::npos) {
        std::fprintf(stderr, "roverd: bad --robot-id '%s'\n", opts.robot_id.c_str());
        return 2;
    }
    return roverd::run(opts);
}
