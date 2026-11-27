// Host test for rover_core. Plain asserts; built with sanitizers by mk/firmware.mk.
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

#include "../rover/rover_core.h"

using namespace rover;

#define CHECK(c) do { if (!(c)) { std::fprintf(stderr, "%s:%d: CHECK failed: %s\n", __FILE__, __LINE__, #c); std::abort(); } } while (0)

static int g_checks = 0;
#define EXPECT(c) do { g_checks++; CHECK(c); } while (0)

static std::string wrap(const std::string &payload) {
  unsigned x = 0;
  for (char c : payload) x ^= (unsigned char)c;
  char hh[8];
  std::snprintf(hh, sizeof hh, "%02X", x);
  return "$" + payload + "*" + hh + "\n";
}

struct Line {
  std::string payload;
  std::vector<std::string> f;
};

static std::vector<std::string> split(const std::string &s) {
  std::vector<std::string> out;
  size_t i = 0;
  while (i <= s.size()) {
    size_t j = s.find(' ', i);
    if (j == std::string::npos) j = s.size();
    out.push_back(s.substr(i, j - i));
    i = j + 1;
  }
  return out;
}

struct Rig {
  Core c;
  uint32_t now = 1000;
  uint16_t mv = 12000, cm = 999;
  uint32_t loop_us = 0;
  Outputs out{0, 0, 0};
  std::vector<Line> lines;
  std::string partial;

  void send(const std::string &payload) { raw(wrap(payload)); }
  void raw(const std::string &s) { for (char ch : s) c.rx((uint8_t)ch); }
  void step(uint32_t dt = 1) {
    now += dt;
    out = c.tick(Inputs{now, mv, cm, loop_us});
    drain();
  }
  void run(uint32_t ms) { for (uint32_t i = 0; i < ms; i++) step(1); }
  void drain() {
    int b;
    while ((b = c.tx_pop()) >= 0) {
      if (b == '\n') { parse(partial); partial.clear(); }
      else partial += (char)b;
    }
  }
  static void parse(const std::string &l) {
    CHECK(l.size() >= 5 && l[0] == '$' && l[l.size() - 3] == '*');
    unsigned x = 0;
    for (size_t i = 1; i < l.size() - 3; i++) x ^= (unsigned char)l[i];
    char hh[8];
    std::snprintf(hh, sizeof hh, "%02X", x);
    CHECK(l.substr(l.size() - 2) == hh);
    CHECK(l.size() < 80);
    Line ln;
    ln.payload = l.substr(1, l.size() - 4);
    ln.f = split(ln.payload);
    g_last->lines.push_back(ln);
  }
  static Rig *g_last;
  Rig() { g_last = this; drain(); }

  std::vector<Line> take(char kind) {
    std::vector<Line> r;
    std::vector<Line> keep;
    for (auto &l : lines) (l.f[0][0] == kind ? r : keep).push_back(l);
    lines = keep;
    return r;
  }
  Line lastT() {
    auto t = take('T');
    CHECK(!t.empty());
    return t.back();
  }
};
Rig *Rig::g_last = nullptr;

static long num(const Line &l, size_t i) { return std::strtol(l.f[i].c_str(), nullptr, 10); }

static void test_hello_and_state_line() {
  Rig r;
  auto h = r.take('H');
  EXPECT(h.size() == 1);
  EXPECT(h[0].payload == "H rover 2.1.0 pwm_max=150 standoff_cm=80 min_mv=10500 timeout_ms=300");
  r.send("?");
  r.drain();
  h = r.take('H');
  EXPECT(h.size() == 1 && h[0].payload.find("H rover 2.1.0") == 0);

  r.mv = 11800;
  r.cm = 123;
  r.loop_us = 777;
  r.send("P 33");
  r.send("V 100 -50 42");
  r.step();
  r.loop_us = 300;
  r.run(60);
  auto t = r.take('T');
  EXPECT(t.size() >= 2);
  const Line &a = t[0];
  EXPECT(a.f.size() == 10);
  EXPECT(num(a, 1) == 1001);        // first tick reports at once
  EXPECT(num(a, 2) == 11800);
  EXPECT(num(a, 3) == 123);
  EXPECT(num(a, 4) == 100);
  EXPECT(num(a, 5) == -50);
  EXPECT(num(a, 6) == 0);           // the pan has only begun to move towards 33
  EXPECT(num(a, 7) == F_MOVING);
  EXPECT(num(a, 8) == 777);
  EXPECT(num(a, 9) == 42);
  EXPECT(num(t[1], 8) == 300);      // max since the previous T
  EXPECT(num(t[1], 1) == 1051);
}

static void test_cadence() {
  Rig r;
  for (int i = 0; i < 1000; i++) r.step(7);  // loop slower than 1 ms
  auto t = r.take('T');
  EXPECT(t.size() >= 1000 * 7 / 50 - 2);
  for (size_t i = 1; i < t.size(); i++) {
    long d = num(t[i], 1) - num(t[i - 1], 1);
    EXPECT(d >= 44 && d <= 56);  // quantised to the loop period, no drift
  }
  // 1 ms ticks: exactly 50 ms apart.
  Rig q;
  q.run(500);
  auto u = q.take('T');
  EXPECT(u.size() == 10);
  for (size_t i = 1; i < u.size(); i++) EXPECT(num(u[i], 1) - num(u[i - 1], 1) == 50);
}

static void test_checksum() {
  Rig r;
  r.take('H');
  r.send("V 100 100 1");
  r.step();
  EXPECT(r.out.left == 100 && r.out.right == 100);
  r.send("S");
  r.step();
  EXPECT(r.out.left == 0);
  // bad checksum: ignored, flag until the next T
  r.run(60);
  r.take('T');
  r.raw("$V 100 100 2*00\n");
  r.raw("$V 100 100 3\n");
  r.raw("garbage\n");
  r.step();
  EXPECT(r.out.left == 0 && r.out.right == 0);
  EXPECT(r.c.bad_lines() == 3);
  r.run(110);
  auto t = r.take('T');
  EXPECT(!t.empty());
  EXPECT(num(t[0], 7) & F_BAD_LINE);
  EXPECT(!(num(t.back(), 7) & F_BAD_LINE));
  EXPECT(r.take('E').empty());
  // lower-case hex is accepted
  std::string w = wrap("V 20 20 9");
  for (auto &ch : w) if (ch >= 'A' && ch <= 'F') ch = ch - 'A' + 'a';
  r.raw(w);
  r.step();
  EXPECT(r.out.left == 20);
  // a line cut off by a new '$' is bad, the new one works
  r.raw("$V 1");
  r.send("V 30 30 10");
  r.step();
  EXPECT(r.out.left == 30);
  EXPECT(r.c.bad_lines() == 4);
  // overlong line
  r.raw("$V 100 100 1111111111111111111111111111111111111111111111111*00\n");
  EXPECT(r.c.bad_lines() == 5);
}

static void test_clamp_and_directions() {
  Rig r;
  r.send("V 255 -255 1");
  r.step();
  EXPECT(r.out.left == 150 && r.out.right == -150);
  r.send("V -200 -149 2");
  r.step();
  EXPECT(r.out.left == -150 && r.out.right == -149);
  r.send("V 256 0 3");
  r.step();
  auto e = r.take('E');
  EXPECT(e.size() == 1 && e[0].f[1] == "2");
  EXPECT(r.out.left == -150);  // refused command leaves the previous one
}

static void test_timeout_and_refresh() {
  Rig r;
  r.send("V 80 80 5");
  r.step();
  uint32_t t0 = r.now;
  r.take('T');
  while (r.now < t0 + 299) r.step();
  EXPECT(r.out.left == 80);
  r.step();  // t0 + 300
  EXPECT(r.out.left == 0 && r.out.right == 0);
  r.run(60);
  auto t = r.take('T');
  EXPECT(num(t.back(), 7) == F_CMD_TIMEOUT);
  EXPECT(num(t.back(), 9) == 5);
  // refresh every 100 ms keeps it moving
  r.send("V 80 80 6");
  for (int i = 0; i < 20; i++) {
    r.run(100);
    EXPECT(r.out.left == 80);
    r.send("V 80 80 " + std::to_string(7 + i));
  }
  r.run(60);
  EXPECT(num(r.lastT(), 7) == F_MOVING);
  // timeout of an already-stopped command is not a CMD_TIMEOUT
  Rig q;
  q.send("V 0 0 1");
  q.run(400);
  EXPECT(!(num(q.lastT(), 7) & F_CMD_TIMEOUT));
}

static void test_standoff() {
  Rig r;
  r.cm = 79;
  r.send("V 100 100 1");
  r.step();
  EXPECT(r.out.left == 0 && r.out.right == 0);
  auto e = r.take('E');
  EXPECT(e.size() == 1 && e[0].f[1] == "3");
  r.run(100);
  EXPECT(r.take('E').empty());  // reported once
  auto t = r.take('T');
  EXPECT(num(t.back(), 7) == F_BLOCKED);
  // reverse and pivot are allowed
  r.send("V -100 -100 2");
  r.step();
  EXPECT(r.out.left == -100 && r.out.right == -100);
  r.send("V -100 100 3");
  r.step();
  EXPECT(r.out.left == -100 && r.out.right == 100);
  r.send("V 100 -100 4");
  r.step();
  EXPECT(r.out.left == 100 && r.out.right == -100);
  r.run(60);
  EXPECT(!(num(r.lastT(), 7) & F_BLOCKED));
  // forward with a slight turn still blocked; 80 cm exactly is clear
  r.send("V 100 -50 5");
  r.step();
  EXPECT(r.out.left == 0);
  r.cm = 80;
  r.step();
  EXPECT(r.out.left == 100);
  // 999 = no echo = clear
  r.cm = 999;
  r.step();
  EXPECT(r.out.left == 100);
}

static void test_drive_into_range() {
  Rig r;
  r.cm = 200;
  r.send("V 120 120 1");
  r.step();
  EXPECT(r.out.left == 120);
  r.take('T');
  r.cm = 79;
  r.step();
  EXPECT(r.out.left == 0 && r.out.right == 0);
  r.run(60);
  EXPECT(num(r.lastT(), 7) & F_BLOCKED);
  // obstacle gone while the command is still fresh: resumes
  r.cm = 300;
  r.send("V 120 120 2");
  r.step();
  EXPECT(r.out.left == 120);
}

static void test_low_pack() {
  Rig r;
  r.mv = 10499;
  r.send("V 100 100 1");
  r.step();
  EXPECT(r.out.left == 0 && r.out.right == 0);
  auto e = r.take('E');
  EXPECT(e.size() == 1 && e[0].f[1] == "4");
  r.run(60);
  EXPECT(num(r.lastT(), 7) & F_LOW_PACK);
  r.mv = 10500;
  r.send("V 100 100 2");
  r.step();
  EXPECT(r.out.left == 100);
  r.run(60);
  EXPECT(!(num(r.lastT(), 7) & F_LOW_PACK));
  // low pack mid-drive stops it
  r.mv = 10000;
  r.step();
  EXPECT(r.out.left == 0);
}

static void test_pan_and_stop() {
  const int L = rover::PAN_EDGE_LEFT, R = rover::PAN_EDGE_RIGHT, MS = rover::PAN_MS_PER_DEG;
  CHECK(L > 45 && L <= 90 && R < -45 && R >= -90);
  Rig r;
  EXPECT(r.out.pan == 0);
  // A commanded angle is approached at one degree per PAN_MS_PER_DEG, never in one step.
  r.send("P 40");
  r.step();
  EXPECT(r.out.pan == 0);
  for (int deg = 1; deg <= 40; deg++) {
    int before = r.out.pan;
    r.run(MS);
    EXPECT(r.out.pan - before == 1);
  }
  r.run(100);
  EXPECT(r.out.pan == 40 && num(r.lastT(), 6) == 40);
  // One slow loop pass still moves no further than the elapsed time allows.
  r.send("P -40");
  r.step();
  r.step(10 * MS);
  EXPECT(r.out.pan == 30);
  r.run(80 * MS);
  EXPECT(r.out.pan == -40 && num(r.lastT(), 6) == -40);
  // The pan stops at its edges, whatever is asked for.
  const char *past_left[] = {"P 90", "P 91", "P 500", "P 4294967295"};
  for (const char *cmd : past_left) {
    r.send("P 0");
    r.run(200 * MS);
    r.send(cmd);
    r.run(200 * MS);
    EXPECT(r.out.pan == L && num(r.lastT(), 6) == L);
  }
  const char *past_right[] = {"P -90", "P -91", "P -500"};
  for (const char *cmd : past_right) {
    r.send("P 0");
    r.run(200 * MS);
    r.send(cmd);
    r.run(200 * MS);
    EXPECT(r.out.pan == R && num(r.lastT(), 6) == R);
  }
  // A new target mid-move turns the pan round from where it is.
  r.send("P 0");
  r.step();
  r.run(20 * MS);
  EXPECT(r.out.pan == R + 20);
  r.send("P -90");
  r.run(5 * MS);
  EXPECT(r.out.pan == R + 15);
  r.send("P 45");
  r.run(130 * MS);
  EXPECT(r.out.pan == 45);
  // S stops the pan where it is; it does not go on to its target.
  r.send("P -45");
  r.step();
  r.run(10 * MS);
  EXPECT(r.out.pan == 35);
  r.send("S");
  r.run(100 * MS);
  EXPECT(r.out.pan == 35 && num(r.lastT(), 6) == 35);
  r.send("P 45");
  r.step();
  r.run(10 * MS);
  EXPECT(r.out.pan == 45);
  EXPECT(r.take('E').empty());
  r.send("V 100 100 1");
  r.step();
  EXPECT(r.out.left == 100);
  r.send("S");
  r.step();
  EXPECT(r.out.left == 0 && r.out.right == 0 && r.out.pan == 45);
  r.run(60);
  EXPECT(num(r.lastT(), 7) == 0);
  // S in the same loop as a pending V wins
  r.send("V 100 100 2");
  r.send("S");
  r.step();
  EXPECT(r.out.left == 0);
}

static void test_malformed() {
  Rig r;
  r.take('H');
  const char *bad[] = {"X", "", "V", "V 1 2", "V 1 2 3 4", "V a b c", "V 1 2 -3", "P", "P x",
                       "S 1", "? x", "V  1 2 3", "V 1 2 3 ", "v 1 2 3"};
  size_t n = 0;
  for (const char *p : bad) {
    r.send(p);
    r.step();
    n++;
    auto e = r.take('E');
    EXPECT(e.size() == 1);
    EXPECT(e[0].f[1] == "1" || e[0].f[1] == "2");
  }
  EXPECT(n == sizeof bad / sizeof *bad);
  EXPECT(r.out.left == 0);
  EXPECT(r.c.bad_lines() == 0);
  EXPECT(r.take('H').empty());
}

static void test_tx_full() {
  Rig r;
  // Never drain: queue far more than the ring holds.
  for (int i = 0; i < 40; i++) {
    r.send("?");
    r.send("X");
    r.now += 50;
    r.out = r.c.tick(Inputs{r.now, r.mv, r.cm, 0});
  }
  EXPECT(r.c.tx_dropped() > 0);
  EXPECT(r.c.tx_pending() <= Core::TX_SIZE);
  r.drain();
  EXPECT(!r.lines.empty());   // every drained line parsed with a valid checksum
  EXPECT(r.partial.empty());
  // after draining, new lines flow again
  r.send("?");
  r.drain();
  EXPECT(!r.take('H').empty());
}

static void test_seq_wrap_and_time_wrap() {
  Rig r;
  r.now = 0xFFFFFF00u;
  r.send("V 50 50 4294967295");
  r.step();
  uint32_t t0 = r.now;
  while ((uint32_t)(r.now - t0) < 299) r.step();
  EXPECT(r.out.left == 50);
  r.step();
  EXPECT(r.out.left == 0);
  EXPECT(num(r.lastT(), 9) == 4294967295L);
  r.send("V 50 50 4294967296");
  r.drain();
  EXPECT(r.take('E').size() == 1);
}

int main() {
  test_hello_and_state_line();
  test_cadence();
  test_checksum();
  test_clamp_and_directions();
  test_timeout_and_refresh();
  test_standoff();
  test_drive_into_range();
  test_low_pack();
  test_pan_and_stop();
  test_malformed();
  test_tx_full();
  test_seq_wrap_and_time_wrap();
  std::printf("rover_core: 12 tests, %d checks passed\n", g_checks);
  return 0;
}
