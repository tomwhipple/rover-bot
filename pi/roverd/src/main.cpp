#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>

#include "roverd.hpp"

namespace {

void usage(FILE* out) {
    std::fprintf(out,
                 "usage: roverd --serial PATH [--robot-id ID] [--camera NAME=URL]... [--simulated]\n"
                 "              [--description-commit HASH] [--home NAME=VALUE]...\n"
                 "       roverd --version\n"
                 "Bus settings come from ROBO_NS, ROBO_ZENOH_MODE, ROBO_ZENOH_LISTEN,\n"
                 "ROBO_ZENOH_CONNECT and ROBO_ZENOH_SCOUTING. Without ROBO_ZENOH_LISTEN it listens on\n"
                 "tcp/[::]:7447 (a free port if that one is taken).\n"
                 "--home names a position channel's home value; the rover has pan (-90..90, default 0).\n");
}

// "pan=0" -> opts.home_pan; false on anything else.
bool parse_home(const char* arg, roverd::Options& opts) {
    const char* eq = std::strchr(arg, '=');
    if (!eq || std::string(arg, eq) != "pan" || !eq[1]) return false;
    char* end = nullptr;
    const double v = std::strtod(eq + 1, &end);
    if (*end || !std::isfinite(v) || v < -90 || v > 90) return false;
    opts.home_pan = static_cast<int>(std::lround(v));
    return true;
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
        } else if (a == "--serial" || a == "--robot-id" || a == "--camera" || a == "--description-commit" ||
                   a == "--home") {
            const char* v = value(a.c_str());
            if (!v) return 2;
            if (a == "--serial") {
                opts.serial = v;
            } else if (a == "--robot-id") {
                opts.robot_id = v;
            } else if (a == "--description-commit") {
                opts.description_commit = v;
            } else if (a == "--home") {
                if (!parse_home(v, opts)) {
                    std::fprintf(stderr, "roverd: --home wants pan=DEG with DEG in -90..90, got '%s'\n", v);
                    return 2;
                }
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
