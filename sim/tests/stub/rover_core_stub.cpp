// Test double of the rover firmware core, implementing only the C ABI and protocol of
// docs/interfaces.md section 4. Used by sim/tests when the real library is not built.
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>

namespace {
constexpr int PWM_MAX = 150, STANDOFF_CM = 80, MIN_MV = 10500, TIMEOUT_MS = 300;

struct Core {
  std::string rx, tx;
  int16_t l = 0, r = 0;
  uint8_t pan = 90;
  uint32_t now = 0, last_v = 0, last_t = 0, seq = 0, loop_max = 0;
  bool have_v = false, timeout = false, blocked = false, low = false, bad = false, started = false;
  int16_t cmd_l = 0, cmd_r = 0;
};

void send(Core* c, const std::string& p) {
  uint8_t x = 0;
  for (char ch : p) x ^= (uint8_t)ch;
  char tail[8];
  snprintf(tail, sizeof tail, "*%02X\n", x);
  c->tx += "$" + p + tail;
}

void hello(Core* c) { send(c, "H rover stub pwm_max=150 standoff_cm=80 min_mv=10500 timeout_ms=300"); }

int clampi(int v, int lo, int hi) { return v < lo ? lo : v > hi ? hi : v; }

void line(Core* c, const std::string& s, uint32_t now) {
  if (s.size() < 4 || s[0] != '$' || s[s.size() - 3] != '*') { c->bad = true; return; }
  std::string p = s.substr(1, s.size() - 4);
  uint8_t x = 0;
  for (char ch : p) x ^= (uint8_t)ch;
  char want[4];
  snprintf(want, sizeof want, "%02X", x);
  if (s.compare(s.size() - 2, 2, want) != 0) { c->bad = true; return; }
  int a, b;
  unsigned q;
  if (sscanf(p.c_str(), "V %d %d %u", &a, &b, &q) == 3) {
    c->cmd_l = (int16_t)clampi(a, -PWM_MAX, PWM_MAX);
    c->cmd_r = (int16_t)clampi(b, -PWM_MAX, PWM_MAX);
    c->seq = q; c->last_v = now; c->have_v = true; c->timeout = false;
  } else if (sscanf(p.c_str(), "P %d", &a) == 1) {
    c->pan = (uint8_t)clampi(a, 0, 180);
  } else if (p == "S") {
    c->cmd_l = c->cmd_r = 0; c->have_v = false;
  } else if (p == "?") {
    hello(c);
  } else {
    send(c, "E 1 unknown");
  }
}
}  // namespace

extern "C" {
void* rc_host_new(void) { Core* c = new Core; hello(c); return c; }
void rc_host_free(void* rc) { delete (Core*)rc; }
void rc_host_rx(void* rc, const uint8_t* d, size_t n) {
  Core* c = (Core*)rc;
  for (size_t i = 0; i < n; i++) {
    if (d[i] == '\n') { line(c, c->rx, c->now); c->rx.clear(); }
    else if (d[i] != '\r' && c->rx.size() < 200) c->rx += (char)d[i];
  }
}
void rc_host_tick(void* rc, uint32_t now, uint16_t mv, uint16_t cm, uint32_t loop_us,
                  int16_t* lp, int16_t* rp, uint8_t* pan) {
  Core* c = (Core*)rc;
  c->now = now;
  if (loop_us > c->loop_max) c->loop_max = loop_us;
  if (c->have_v && now - c->last_v > TIMEOUT_MS) { c->have_v = false; c->cmd_l = c->cmd_r = 0; c->timeout = true; }
  int16_t l = c->cmd_l, r = c->cmd_r;
  c->low = mv < MIN_MV;
  if (c->low) l = r = 0;
  c->blocked = cm < STANDOFF_CM && l + r > 0;
  if (c->blocked) l = r = 0;
  c->l = l; c->r = r;
  if (now - c->last_t >= 50) {
    c->last_t = now;
    int flags = ((l || r) ? 1 : 0) | (c->blocked ? 2 : 0) | (c->low ? 4 : 0) | (c->timeout ? 8 : 0) | (c->bad ? 16 : 0);
    char buf[128];
    snprintf(buf, sizeof buf, "T %u %u %u %d %d %u %d %u %u", now, mv, cm, l, r, c->pan, flags, c->loop_max, c->seq);
    send(c, buf);
    c->bad = false; c->loop_max = 0;
  }
  *lp = c->l; *rp = c->r; *pan = c->pan;
}
size_t rc_host_tx(void* rc, uint8_t* buf, size_t cap) {
  Core* c = (Core*)rc;
  size_t n = c->tx.size() < cap ? c->tx.size() : cap;
  memcpy(buf, c->tx.data(), n);
  c->tx.erase(0, n);
  return n;
}
}
