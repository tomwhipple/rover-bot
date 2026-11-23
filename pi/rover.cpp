// rover: drive the rover or take a snapshot. Meant to be run over SSH.
//
//   rover move L R MS    motors at -255..255 for MS milliseconds
//   rover pan DEG        camera pan, 0..180
//   rover stop
//   rover status         pack millivolts and front range in cm
//   rover snap           JPEG on stdout:  ssh rover rover snap > look.jpg
#include <fcntl.h>
#include <termios.h>
#include <unistd.h>

#include <cstdio>
#include <cstdlib>
#include <map>
#include <string>

static const char* PORT = "/dev/serial0";

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
    execlp("rpicam-still", "rpicam-still", "-n", "-t", "500", "-o", "-",
           "--width", "1296", "--height", "972", nullptr);
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
