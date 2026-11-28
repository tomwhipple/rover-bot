#include "rover_core.h"

namespace rover {

namespace {

struct LineBuf {
  char b[96];
  uint8_t n = 1;  // b[0] is reserved for '$'

  void c(char ch) {
    if (n + 5u >= sizeof(b)) return;
    b[n++] = ch;
  }
  void str(const char *s) { while (*s) c(*s++); }
  void u(uint32_t v) {
    char t[10];
    uint8_t k = 0;
    do { t[k++] = '0' + v % 10; v /= 10; } while (v);
    while (k) c(t[--k]);
  }
  void s(int32_t v) {
    if (v < 0) { c('-'); u((uint32_t)(-(v + 1)) + 1u); }
    else u((uint32_t)v);
  }
  // Wraps the payload built so far as "$payload*HH\n".
  void finish() {
    uint8_t x = 0;
    for (uint8_t i = 1; i < n; i++) x ^= (uint8_t)b[i];
    b[0] = '$';
    b[n++] = '*';
    b[n++] = "0123456789ABCDEF"[x >> 4];
    b[n++] = "0123456789ABCDEF"[x & 15];
    b[n++] = '\n';
  }
};

// Parses " [-]digits" at p. Magnitude only; the caller range-checks.
bool num(const char *&p, bool &neg, uint32_t &mag) {
  if (*p != ' ') return false;
  p++;
  neg = false;
  if (*p == '-') { neg = true; p++; }
  if (*p < '0' || *p > '9') return false;
  mag = 0;
  while (*p >= '0' && *p <= '9') {
    uint32_t d = *p - '0';
    if (mag > (0xFFFFFFFFu - d) / 10) return false;
    mag = mag * 10 + d;
    p++;
  }
  return true;
}

int hexval(char c) {
  if (c >= '0' && c <= '9') return c - '0';
  if (c >= 'A' && c <= 'F') return c - 'A' + 10;
  if (c >= 'a' && c <= 'f') return c - 'a' + 10;
  return -1;
}

int16_t clamp_pwm(int v) {
  return v > PWM_MAX ? PWM_MAX : v < -PWM_MAX ? -PWM_MAX : v;
}

}  // namespace

Core::Core()
    : rx_n_(0), in_line_(false), overflow_(false), garbage_(false),
      tx_head_(0), tx_n_(0), tx_dropped_(0), bad_count_(0),
      cmd_l_(0), cmd_r_(0), seq_(0), last_v_ms_(0), next_t_ms_(0),
      loop_max_us_(0), have_cmd_(false), v_pending_(false), timed_out_(false),
      bad_seen_(false), blocked_seen_(false), t_started_(false),
      pan_(PAN_DEFAULT), pan_target_(PAN_DEFAULT), refusing_(0), pan_moving_(false), pan_ms_(0),
      applied_l_(0), applied_r_(0) {
  rxbuf_[0] = 0;
  send_hello();
}

void Core::queue(const char *line, uint8_t n) {
  if (TX_SIZE - tx_n_ < n) { tx_dropped_++; return; }
  for (uint8_t i = 0; i < n; i++) {
    uint16_t at = tx_head_ + tx_n_;
    tx_[at >= TX_SIZE ? at - TX_SIZE : at] = (uint8_t)line[i];
    tx_n_++;
  }
}

int Core::tx_pop() {
  if (!tx_n_) return -1;
  uint8_t b = tx_[tx_head_];
  tx_head_ = tx_head_ + 1 == TX_SIZE ? 0 : tx_head_ + 1;
  tx_n_--;
  return b;
}

size_t Core::tx_read(uint8_t *buf, size_t cap) {
  size_t k = 0;
  while (k < cap && tx_n_) buf[k++] = (uint8_t)tx_pop();
  return k;
}

void Core::send_hello() {
  LineBuf l;
  l.str("H rover " ROVER_VERSION " pwm_max=");
  l.u(PWM_MAX);
  l.str(" standoff_cm=");
  l.u(STANDOFF_CM);
  l.str(" min_mv=");
  l.u(MIN_PACK_MV);
  l.str(" timeout_ms=");
  l.u(CMD_TIMEOUT_MS);
  l.finish();
  queue(l.b, l.n);
}

void Core::send_error(uint8_t code, const char *text) {
  LineBuf l;
  l.str("E ");
  l.u(code);
  l.c(' ');
  l.str(text);
  l.finish();
  queue(l.b, l.n);
}

void Core::send_state(const Inputs &in, bool moving, bool blocked, bool low) {
  uint8_t flags = (moving ? F_MOVING : 0) | (blocked ? F_BLOCKED : 0) |
                  (low ? F_LOW_PACK : 0) | (timed_out_ ? F_CMD_TIMEOUT : 0) |
                  (bad_seen_ ? F_BAD_LINE : 0);
  LineBuf l;
  l.str("T ");
  l.u(in.now_ms);
  l.c(' ');
  l.u(in.pack_mv);
  l.c(' ');
  l.u(in.range_cm > NO_ECHO_CM ? NO_ECHO_CM : in.range_cm);
  l.c(' ');
  l.s(applied_l_);
  l.c(' ');
  l.s(applied_r_);
  l.c(' ');
  l.s(pan_);
  l.c(' ');
  l.u(flags);
  l.c(' ');
  l.u(loop_max_us_);
  l.c(' ');
  l.u(seq_);
  l.finish();
  queue(l.b, l.n);
  bad_seen_ = false;
  blocked_seen_ = false;
  loop_max_us_ = 0;
}

void Core::rx(uint8_t c) {
  if (c == '\r') return;
  if (c == '$') {
    if (in_line_) { bad_seen_ = true; bad_count_++; }
    in_line_ = true;
    overflow_ = false;
    garbage_ = false;
    rx_n_ = 0;
    return;
  }
  if (c == '\n') {
    if (in_line_) process_line();
    else if (garbage_) { bad_seen_ = true; bad_count_++; }
    in_line_ = false;
    garbage_ = false;
    rx_n_ = 0;
    return;
  }
  if (!in_line_) { garbage_ = true; return; }
  if (rx_n_ < RX_MAX) rxbuf_[rx_n_++] = (char)c;
  else overflow_ = true;
}

void Core::process_line() {
  if (overflow_ || rx_n_ < 3 || rxbuf_[rx_n_ - 3] != '*') {
    bad_seen_ = true;
    bad_count_++;
    return;
  }
  uint8_t p = rx_n_ - 3;
  int hi = hexval(rxbuf_[p + 1]), lo = hexval(rxbuf_[p + 2]);
  uint8_t x = 0;
  for (uint8_t i = 0; i < p; i++) x ^= (uint8_t)rxbuf_[i];
  if (hi < 0 || lo < 0 || x != (uint8_t)(hi << 4 | lo)) {
    bad_seen_ = true;
    bad_count_++;
    return;
  }
  rxbuf_[p] = 0;
  command(rxbuf_);
}

void Core::command(const char *p) {
  const char c = p[0];
  bool neg;
  uint32_t a, b, s;
  const char *q = p + 1;
  if (c == 'V') {
    bool na, nb, ns;
    q = p + 1;
    if (!num(q, na, a) || !num(q, nb, b) || !num(q, ns, s) || *q || ns ||
        a > 255 || b > 255) {
      send_error(E_ARGUMENT, "bad V arguments");
      return;
    }
    cmd_l_ = clamp_pwm(na ? -(int)a : (int)a);
    cmd_r_ = clamp_pwm(nb ? -(int)b : (int)b);
    seq_ = s;
    v_pending_ = true;
    timed_out_ = false;
  } else if (c == 'P') {
    if (!num(q, neg, a) || *q) {
      send_error(E_ARGUMENT, "bad P arguments");
      return;
    }
    const int deg = a > 90 ? (neg ? -90 : 90) : (neg ? -(int)a : (int)a);
    pan_target_ = (int8_t)(deg > PAN_EDGE_LEFT ? PAN_EDGE_LEFT : deg < PAN_EDGE_RIGHT ? PAN_EDGE_RIGHT : deg);
  } else if (c == 'S' && !p[1]) {
    have_cmd_ = false;
    v_pending_ = false;
    timed_out_ = false;
    cmd_l_ = cmd_r_ = 0;
    pan_target_ = pan_;  // stop means everything: the pan stays where it is
  } else if (c == '?' && !p[1]) {
    send_hello();
  } else {
    send_error(E_UNKNOWN, "unknown command");
  }
}

Outputs Core::tick(const Inputs &in) {
  if (in.loop_us > loop_max_us_) loop_max_us_ = in.loop_us;

  if (v_pending_) {
    v_pending_ = false;
    have_cmd_ = true;
    last_v_ms_ = in.now_ms;
  }
  if (have_cmd_ && (uint32_t)(in.now_ms - last_v_ms_) >= (uint32_t)CMD_TIMEOUT_MS) {
    have_cmd_ = false;
    if (cmd_l_ || cmd_r_) timed_out_ = true;
    cmd_l_ = cmd_r_ = 0;
  }

  const bool low = in.pack_mv < MIN_PACK_MV;
  const bool blocked = cmd_l_ + cmd_r_ > 0 && in.range_cm < STANDOFF_CM;
  const bool low_refuse = low && (cmd_l_ || cmd_r_);
  const uint8_t refusing = blocked ? E_BLOCKED : low_refuse ? E_LOW_PACK : 0;
  if (refusing && refusing != refusing_)
    send_error(refusing, refusing == E_BLOCKED ? "blocked" : "low pack");
  refusing_ = refusing;
  if (blocked) blocked_seen_ = true;

  if (refusing) {
    applied_l_ = applied_r_ = 0;
  } else {
    applied_l_ = cmd_l_;
    applied_r_ = cmd_r_;
  }

  // The pan follows its target a degree at a time, so no command can slam the servo.
  if (pan_ == pan_target_) {
    pan_moving_ = false;
  } else if (!pan_moving_) {
    pan_moving_ = true;
    pan_ms_ = in.now_ms;
  } else {
    uint32_t deg = (uint32_t)(in.now_ms - pan_ms_) / PAN_MS_PER_DEG;
    const uint32_t gap = (uint32_t)(pan_ < pan_target_ ? pan_target_ - pan_ : pan_ - pan_target_);
    if (deg > gap) deg = gap;
    pan_ = (int8_t)(pan_ < pan_target_ ? pan_ + (int)deg : pan_ - (int)deg);
    pan_ms_ += deg * PAN_MS_PER_DEG;
  }

  if (!t_started_) {
    t_started_ = true;
    next_t_ms_ = in.now_ms;
  }
  if ((int32_t)(in.now_ms - next_t_ms_) >= 0) {
    send_state(in, applied_l_ || applied_r_, blocked || blocked_seen_, low);
    next_t_ms_ += T_PERIOD_MS;
    if ((int32_t)(in.now_ms - next_t_ms_) >= 0) next_t_ms_ = in.now_ms + T_PERIOD_MS;
  }

  Outputs o;
  o.left = applied_l_;
  o.right = applied_r_;
  o.pan = pan_;
  return o;
}

}  // namespace rover

#if !defined(ARDUINO) && !defined(__AVR__)
#include <new>

#define RC_EXPORT extern "C" __attribute__((visibility("default")))

RC_EXPORT void *rc_host_new(void) { return new (std::nothrow) rover::Core(); }

RC_EXPORT void rc_host_free(void *rc) { delete static_cast<rover::Core *>(rc); }

RC_EXPORT void rc_host_rx(void *rc, const uint8_t *data, size_t n) {
  rover::Core *core = static_cast<rover::Core *>(rc);
  for (size_t i = 0; i < n; i++) core->rx(data[i]);
}

RC_EXPORT void rc_host_tick(void *rc, uint32_t now_ms, uint16_t pack_mv,
                            uint16_t range_cm, uint32_t loop_us,
                            int16_t *left_pwm, int16_t *right_pwm,
                            int8_t *pan_deg) {
  rover::Inputs in = {now_ms, pack_mv, range_cm, loop_us};
  rover::Outputs o = static_cast<rover::Core *>(rc)->tick(in);
  *left_pwm = o.left;
  *right_pwm = o.right;
  *pan_deg = o.pan;
}

RC_EXPORT size_t rc_host_tx(void *rc, uint8_t *buf, size_t cap) {
  return static_cast<rover::Core *>(rc)->tx_read(buf, cap);
}
#endif
