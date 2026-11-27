// rover: drive the rover or take a snapshot. Meant to be run over SSH.
//
//   rover move L R MS    motors at -255..255 for MS milliseconds
//   rover pan DEG        camera pan, 0..180
//   rover stop
//   rover status         pack millivolts and front range in cm
//   rover snap           JPEG on stdout:  ssh rover rover snap > look.jpg
//
// snap takes the picture from rovercam's stream when that is running (it holds the camera), else
// from the stock rpicam-still. Settings: rover_conf.hpp.
#include <arpa/inet.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <termios.h>
#include <unistd.h>

#include <cstdio>
#include <cstdlib>
#include <map>
#include <string>

#include "rover_conf.hpp"

static const char* PORT = "/dev/serial0";

// One JPEG from rovercam, which holds the camera while it runs. False if it is not running.
static bool snap_from_stream(int port) {
  int fd = socket(AF_INET, SOCK_STREAM, 0);
  sockaddr_in addr{};
  addr.sin_family = AF_INET;
  addr.sin_port = htons((uint16_t)port);
  addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
  if (fd < 0 || connect(fd, (sockaddr*)&addr, sizeof addr) != 0) {
    if (fd >= 0) close(fd);
    return false;
  }
  const char req[] = "GET /snap.jpg HTTP/1.0\r\n\r\n";
  if (write(fd, req, sizeof req - 1) < 0) {
    close(fd);
    return false;
  }
  std::string reply;
  char buf[65536];
  ssize_t n;
  while ((n = read(fd, buf, sizeof buf)) > 0) reply.append(buf, (size_t)n);
  close(fd);
  size_t body = reply.find("\r\n\r\n");
  if (reply.compare(0, 12, "HTTP/1.0 200") != 0 || body == std::string::npos) {
    fprintf(stderr, "rover: rovercam has no picture: %s\n", reply.substr(0, reply.find('\r')).c_str());
    exit(1);
  }
  fwrite(reply.data() + body + 4, 1, reply.size() - body - 4, stdout);
  return true;
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

  if (cmd == "snap") {  // one JPEG on stdout
    rover_conf::Conf c = rover_conf::read("rover");
    if (snap_from_stream(std::stoi(c["stream_port"]))) return 0;
    execlp("rpicam-still", "rpicam-still", "-n", "-o", "-",
           "-t", c["snap_warmup_ms"].c_str(),
           "--width", c["snap_width"].c_str(),
           "--height", c["snap_height"].c_str(),
           "--rotation", c["camera_rotation"].c_str(), nullptr);
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
