// Rover firmware logic: protocol parsing, limits, state reporting.
// No Arduino dependency, no heap. See docs/serial-protocol.md.
#ifndef ROVER_CORE_H
#define ROVER_CORE_H

#include <stddef.h>
#include <stdint.h>

namespace rover {

constexpr int PWM_MAX = 150;
constexpr int STANDOFF_CM = 80;
constexpr int MIN_PACK_MV = 10500;
constexpr int CMD_TIMEOUT_MS = 300;
constexpr int T_PERIOD_MS = 50;
constexpr int NO_ECHO_CM = 999;
// Pan is in degrees, 0 straight ahead, + to the left. A P command names -90..90; the pan stops at
// the edges below, which keep the horn off the servo's end stops. They are not measured on this
// rover yet: +-60 is 1000..2000 us with the sketch's calibration, inside any hobby servo's travel.
constexpr int PAN_EDGE_LEFT = 60;
constexpr int PAN_EDGE_RIGHT = -60;
constexpr int PAN_DEFAULT = 0;
constexpr int PAN_MS_PER_DEG = 16;  // the pan moves to a commanded angle at 62.5 deg/s
#define ROVER_VERSION "2.1.0"

enum Flag : uint8_t {
  F_MOVING = 1,
  F_BLOCKED = 2,
  F_LOW_PACK = 4,
  F_CMD_TIMEOUT = 8,
  F_BAD_LINE = 16,
};

enum ErrCode : uint8_t {
  E_UNKNOWN = 1,
  E_ARGUMENT = 2,
  E_BLOCKED = 3,
  E_LOW_PACK = 4,
};

struct Inputs {
  uint32_t now_ms;
  uint16_t pack_mv;
  uint16_t range_cm;  // NO_ECHO_CM (or more) = nothing in range
  uint32_t loop_us;   // duration of the previous loop iteration
};

struct Outputs {
  int16_t left;
  int16_t right;
  int8_t pan;
};

class Core {
 public:
  static constexpr uint8_t RX_MAX = 40;    // payload + "*HH"
  static constexpr uint8_t TX_SIZE = 160;

  Core();
  void rx(uint8_t byte);
  Outputs tick(const Inputs &in);

  // Bytes for the host, oldest first. Lines are queued whole or not at all.
  int tx_pop();  // -1 when empty
  size_t tx_read(uint8_t *buf, size_t cap);
  size_t tx_pending() const { return tx_n_; }

  uint16_t bad_lines() const { return bad_count_; }
  uint16_t tx_dropped() const { return tx_dropped_; }

 private:
  void process_line();
  void command(const char *p);
  void send_hello();
  void send_error(uint8_t code, const char *text);
  void send_state(const Inputs &in, bool moving, bool blocked, bool low);
  void queue(const char *line, uint8_t n);

  char rxbuf_[RX_MAX + 1];
  uint8_t rx_n_;
  bool in_line_, overflow_, garbage_;

  uint8_t tx_[TX_SIZE];
  uint8_t tx_head_, tx_n_;
  uint16_t tx_dropped_, bad_count_;

  int16_t cmd_l_, cmd_r_;
  uint32_t seq_, last_v_ms_, next_t_ms_, loop_max_us_;
  bool have_cmd_, v_pending_, timed_out_, bad_seen_, blocked_seen_, t_started_;
  int8_t pan_, pan_target_;
  uint8_t refusing_;
  bool pan_moving_;
  uint32_t pan_ms_;
  int16_t applied_l_, applied_r_;
};

}  // namespace rover

#endif
