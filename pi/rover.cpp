// rover: drive the rover or take a snapshot. Meant to be run over SSH.
//
//   rover move L R MS    motors at -255..255 for MS milliseconds
//   rover pan DEG        camera pan, 0..180
//   rover stop
//   rover status         pack millivolts and front range in cm
//   rover snap           JPEG on stdout:  ssh rover rover snap > look.jpg
//
// snap takes its camera settings from /etc/rover.conf (pi/rover.conf is the
// installed copy's source); $ROVER_CONF names another file.
#include <fcntl.h>
#include <termios.h>
#include <unistd.h>

#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <map>
#include <string>

static const char* PORT = "/dev/serial0";
static const char* CONF = "/etc/rover.conf";

using Conf = std::map<std::string, std::string>;

static std::string trim(const std::string& s) {
  const char* ws = " \t\r";
  size_t a = s.find_first_not_of(ws);
  if (a == std::string::npos) return "";
  return s.substr(a, s.find_last_not_of(ws) - a + 1);
}

// Lines of "key = value"; '#' starts a comment. A missing file leaves the
// defaults. An unknown key or a bad value is an error: a typo must not pass
// for a setting.
static Conf read_conf(Conf conf) {
  const char* env = getenv("ROVER_CONF");
  const std::string path = env && *env ? env : CONF;
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
    if (ok && key == "snap_rotation") ok = val == "0" || val == "180";
    if (!ok) {
      fprintf(stderr, "rover: %s:%d: bad setting: %s\n", path.c_str(), n, line.c_str());
      exit(1);
    }
    conf[key] = val;
  }
  return conf;
}

// Send one firmware command and return its one-line reply.
static std::string send(const std::string& line) {
  int fd = open(PORT, O_RDWR | O_NOCTTY);
  if (fd < 0) {
    perror(PORT);
    exit(1);
  }
  termios t{};
  tcgetattr(fd, &t);
  cfmakeraw(&t);
  cfsetspeed(&t, B115200);
  t.c_cc[VMIN] = 0;
  t.c_cc[VTIME] = 30;  // 3 s read timeout
  tcsetattr(fd, TCSANOW, &t);
  tcflush(fd, TCIFLUSH);

  if (write(fd, line.data(), line.size()) < 0) {
    perror("write");
    exit(1);
  }
  std::string reply;
  char c;
  while (read(fd, &c, 1) == 1 && c != '\n')
    if (c != '\r') reply += c;
  close(fd);
  return reply;
}

int main(int argc, char** argv) {
  const std::map<std::string, std::string> letters = {
      {"move", "M"}, {"pan", "P"}, {"stop", "S"}, {"status", "?"}};
  std::string cmd = argc > 1 ? argv[1] : "";

  if (cmd == "snap") {  // one JPEG on stdout, from the stock camera tool
    Conf c = read_conf({{"snap_width", "1296"},
                        {"snap_height", "972"},
                        {"snap_rotation", "0"},  // rpicam-still takes 0 or 180
                        {"snap_warmup_ms", "500"}});
    execlp("rpicam-still", "rpicam-still", "-n", "-o", "-",
           "-t", c["snap_warmup_ms"].c_str(),
           "--width", c["snap_width"].c_str(),
           "--height", c["snap_height"].c_str(),
           "--rotation", c["snap_rotation"].c_str(), nullptr);
    perror("rpicam-still");
    return 1;
  }
  auto it = letters.find(cmd);
  if (it == letters.end()) {
    fprintf(stderr, "usage: rover move L R MS | pan DEG | stop | status | snap\n");
    return 2;
  }
  std::string line = it->second;
  for (int i = 2; i < argc; i++) line += std::string(" ") + argv[i];
  std::string reply = send(line + "\n");
  if (reply.empty()) {
    fprintf(stderr, "rover: no reply from the aLaMode\n");
    return 1;
  }
  puts(reply.c_str());
}
