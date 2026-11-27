// Settings shared by the rover's Pi programs, read from /etc/rover.conf (pi/rover.conf is the
// installed copy's source); $ROVER_CONF names another file.
#ifndef ROVER_CONF_HPP
#define ROVER_CONF_HPP

#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <map>
#include <string>

namespace rover_conf {

using Conf = std::map<std::string, std::string>;

inline Conf defaults() {
  return {{"camera_rotation", "0"},  // the camera tools take 0 or 180
          {"snap_width", "1296"},
          {"snap_height", "972"},
          {"snap_warmup_ms", "500"},
          {"stream_width", "648"},
          {"stream_height", "486"},
          {"stream_fps", "15"},
          {"stream_port", "8080"}};
}

inline std::string trim(const std::string& s) {
  const char* ws = " \t\r";
  size_t a = s.find_first_not_of(ws);
  if (a == std::string::npos) return "";
  return s.substr(a, s.find_last_not_of(ws) - a + 1);
}

// Lines of "key = value"; '#' starts a comment. A missing file leaves the defaults. An unknown
// key or a bad value is an error: a typo must not pass for a setting.
inline Conf read(const char* prog) {
  Conf conf = defaults();
  const char* env = getenv("ROVER_CONF");
  const std::string path = env && *env ? env : "/etc/rover.conf";
  std::ifstream in(path);
  if (!in) {
    if (env && *env) {
      perror(path.c_str());
      exit(1);
    }
    return conf;
  }
  std::string line;
  for (int n = 1; std::getline(in, line); n++) {
    line = trim(line.substr(0, line.find('#')));
    if (line.empty()) continue;
    size_t eq = line.find('=');
    std::string key = trim(line.substr(0, eq));
    std::string val = eq == std::string::npos ? "" : trim(line.substr(eq + 1));
    bool ok = conf.count(key) && !val.empty() && val.size() <= 5 &&
              val.find_first_not_of("0123456789") == std::string::npos;
    if (ok && key == "camera_rotation") ok = val == "0" || val == "180";
    if (ok && key == "stream_fps") ok = val != "0";
    if (!ok) {
      fprintf(stderr, "%s: %s:%d: bad setting: %s\n", prog, path.c_str(), n, line.c_str());
      exit(1);
    }
    conf[key] = val;
  }
  return conf;
}

}  // namespace rover_conf

#endif
