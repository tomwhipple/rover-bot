// rovercam: the rover's live camera, as MJPEG over HTTP for the robo-control console.
//
//   GET /snap.jpg     the next frame, one JPEG
//   GET <any other>   multipart/x-mixed-replace stream; each part carries X-Timestamp, the
//                     time the frame arrived here, in seconds (UTC)
//
// It runs the stock rpicam-vid and splits its MJPEG output into frames, so it holds the camera
// for as long as it runs. Settings: rover_conf.hpp.
#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <unistd.h>

#include <chrono>
#include <condition_variable>
#include <csignal>
#include <cstdio>
#include <cstring>
#include <memory>
#include <mutex>
#include <string>
#include <thread>

#include "rover_conf.hpp"

namespace {

struct Latest {
  std::mutex mu;
  std::condition_variable cv;
  std::shared_ptr<const std::string> jpeg;
  double time_s = 0;
  unsigned long seq = 0;
} latest;

double now_s() {
  timeval tv;
  gettimeofday(&tv, nullptr);
  return (double)tv.tv_sec + (double)tv.tv_usec / 1e6;
}

void publish(std::string&& frame) {
  auto p = std::make_shared<const std::string>(std::move(frame));
  std::lock_guard<std::mutex> lock(latest.mu);
  latest.jpeg = std::move(p);
  latest.time_s = now_s();
  latest.seq++;
  latest.cv.notify_all();
}

// Runs the camera for ever, restarting it when it ends (the camera busy, or unplugged).
void camera_loop(const std::string& cmd) {
  for (;;) {
    FILE* p = popen(cmd.c_str(), "r");
    if (!p) {
      perror("rovercam: popen");
    } else {
      // A JPEG runs from FF D8 to FF D9; inside one, FF is always followed by a stuffed 00.
      std::string frame;
      bool in_frame = false;
      int prev = -1, c;
      unsigned long frames = 0;
      while ((c = fgetc(p)) != EOF) {
        if (!in_frame) {
          if (prev == 0xFF && c == 0xD8) {
            frame.assign("\xFF\xD8", 2);
            in_frame = true;
          }
        } else {
          frame.push_back((char)c);
          if (prev == 0xFF && c == 0xD9) {
            publish(std::move(frame));
            frame.clear();
            in_frame = false;
            frames++;
          } else if (frame.size() > size_t{8} * 1024 * 1024) {
            in_frame = false;  // not a JPEG stream after all
          }
        }
        prev = c;
      }
      pclose(p);
      fprintf(stderr, "rovercam: the camera stopped after %lu frames; starting it again\n", frames);
    }
    std::this_thread::sleep_for(std::chrono::seconds(2));
  }
}

bool send_all(int fd, const char* data, size_t n) {
  while (n) {
    ssize_t w = send(fd, data, n, 0);
    if (w <= 0) return false;
    data += w;
    n -= (size_t)w;
  }
  return true;
}

bool send_all(int fd, const std::string& s) { return send_all(fd, s.data(), s.size()); }

// Waits for a frame newer than `seen`. False after 5 s without one.
bool next_frame(unsigned long& seen, std::shared_ptr<const std::string>& jpeg, double& time_s) {
  std::unique_lock<std::mutex> lock(latest.mu);
  if (!latest.cv.wait_for(lock, std::chrono::seconds(5), [&] { return latest.seq != seen; })) return false;
  seen = latest.seq;
  jpeg = latest.jpeg;
  time_s = latest.time_s;
  return true;
}

void serve(int fd) {
  timeval tv = {10, 0};
  setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof tv);
  setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof tv);
  std::string req;
  char buf[1024];
  while (req.find("\r\n\r\n") == std::string::npos && req.size() < 8192) {
    ssize_t n = recv(fd, buf, sizeof buf, 0);
    if (n <= 0) break;
    req.append(buf, (size_t)n);
  }
  const bool snap = req.compare(0, 14, "GET /snap.jpg ") == 0 || req.compare(0, 14, "GET /snap.jpg?") == 0;
  std::shared_ptr<const std::string> jpeg;
  double t = 0;
  unsigned long seen;
  {
    std::lock_guard<std::mutex> lock(latest.mu);
    seen = latest.seq;
  }
  char head[256];
  if (req.compare(0, 4, "GET ") != 0) {
    send_all(fd, "HTTP/1.0 400 Bad Request\r\nContent-Length: 0\r\n\r\n");
  } else if (snap) {
    if (next_frame(seen, jpeg, t)) {
      snprintf(head, sizeof head,
               "HTTP/1.0 200 OK\r\nContent-Type: image/jpeg\r\nContent-Length: %zu\r\n"
               "X-Timestamp: %.6f\r\nCache-Control: no-cache\r\n\r\n", jpeg->size(), t);
      send_all(fd, head) && send_all(fd, *jpeg);
    } else {
      send_all(fd, "HTTP/1.0 503 Service Unavailable\r\nContent-Length: 0\r\n\r\n");
    }
  } else if (send_all(fd, "HTTP/1.0 200 OK\r\nContent-Type: multipart/x-mixed-replace; boundary=frame\r\n"
                          "Cache-Control: no-cache\r\n\r\n")) {
    // A viewer slower than the camera gets the newest frame each time, not a growing queue.
    while (next_frame(seen, jpeg, t)) {
      snprintf(head, sizeof head,
               "--frame\r\nContent-Type: image/jpeg\r\nContent-Length: %zu\r\nX-Timestamp: %.6f\r\n\r\n",
               jpeg->size(), t);
      if (!send_all(fd, head) || !send_all(fd, *jpeg) || !send_all(fd, "\r\n")) break;
    }
  }
  close(fd);
}

}  // namespace

int main(int argc, char**) {
  if (argc != 1) {
    fprintf(stderr, "usage: rovercam    (settings in /etc/rover.conf)\n");
    return 2;
  }
  rover_conf::Conf c = rover_conf::read("rovercam");
  signal(SIGPIPE, SIG_IGN);

  int srv = socket(AF_INET6, SOCK_STREAM, 0);
  int on = 1, off = 0;
  setsockopt(srv, SOL_SOCKET, SO_REUSEADDR, &on, sizeof on);
  setsockopt(srv, IPPROTO_IPV6, IPV6_V6ONLY, &off, sizeof off);
  sockaddr_in6 addr{};
  addr.sin6_family = AF_INET6;
  addr.sin6_addr = in6addr_any;
  addr.sin6_port = htons((uint16_t)std::stoi(c["stream_port"]));
  if (srv < 0 || bind(srv, (sockaddr*)&addr, sizeof addr) != 0 || listen(srv, 8) != 0) {
    perror("rovercam: listen");
    return 1;
  }

  // --mode keeps the sensor's whole field of view (2x2 binned) whatever size is streamed.
  const std::string cmd = "exec rpicam-vid -n -t 0 --codec mjpeg --mode 1296:972 --flush -o -"
                          " --width " + c["stream_width"] + " --height " + c["stream_height"] +
                          " --framerate " + c["stream_fps"] + " --rotation " + c["camera_rotation"] +
                          " 2>/dev/null";
  fprintf(stderr, "rovercam: port %s, %sx%s at %s fps\n", c["stream_port"].c_str(),
          c["stream_width"].c_str(), c["stream_height"].c_str(), c["stream_fps"].c_str());
  std::thread(camera_loop, cmd).detach();
  for (;;) {
    int fd = accept(srv, nullptr, nullptr);
    if (fd < 0) continue;
    std::thread(serve, fd).detach();
  }
}
