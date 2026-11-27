// Maps the firmware's 32-bit millis() onto the host clock (docs/interfaces.md section 5).
#pragma once

#include <cstdint>
#include <deque>
#include <optional>

namespace roverd {

class ClockMap {
public:
    explicit ClockMap(int64_t window_ns = INT64_C(10'000'000'000)) : window_ns_(window_ns) {}

    // A line stamped `millis` by the firmware arrived at host time `host_ns`. Returns the line's
    // time on the host clock. The offset (host - firmware) is the minimum over the window, so it
    // follows the least-delayed line. millis() wrapping at 2^32 is unwrapped; millis() going
    // backwards, or jumping forward further than the host clock moved, is a firmware restart
    // and starts a new estimate.
    int64_t map(uint32_t millis, int64_t host_ns);

    // The firmware clock in ns since its boot, unwrapped; valid after the first map().
    int64_t firmware_ns() const { return fw_ms_ * INT64_C(1'000'000); }
    uint64_t restarts() const { return restarts_; }
    void reset();

private:
    struct Sample {
        int64_t host_ns;
        int64_t offset_ns;
    };
    int64_t window_ns_;
    bool started_ = false;
    uint32_t last_raw_ = 0;
    int64_t last_host_ns_ = 0;
    int64_t fw_ms_ = 0;
    uint64_t restarts_ = 0;
    std::deque<Sample> window_;  // increasing offset_ns: front is the window minimum
};

}  // namespace roverd
