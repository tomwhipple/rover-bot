#include "clock_map.hpp"

namespace roverd {

namespace {
// Forward jumps of millis() up to the host's elapsed time plus this much are believed.
constexpr int64_t SLACK_MS = 1000;
}  // namespace

void ClockMap::reset() {
    started_ = false;
    window_.clear();
}

int64_t ClockMap::map(uint32_t millis, int64_t host_ns) {
    if (started_) {
        const uint32_t delta = millis - last_raw_;  // modulo 2^32
        const int64_t host_elapsed_ms = (host_ns - last_host_ns_) / 1'000'000;
        if (delta >= 0x80000000u || static_cast<int64_t>(delta) > host_elapsed_ms + SLACK_MS) {
            ++restarts_;
            started_ = false;
            window_.clear();
        } else {
            fw_ms_ += delta;
        }
    }
    if (!started_) {
        started_ = true;
        fw_ms_ = millis;
    }
    last_raw_ = millis;
    last_host_ns_ = host_ns;

    const int64_t offset = host_ns - fw_ms_ * 1'000'000;
    while (!window_.empty() && window_.back().offset_ns >= offset) window_.pop_back();
    window_.push_back({host_ns, offset});
    while (window_.front().host_ns < host_ns - window_ns_) window_.pop_front();
    return fw_ms_ * 1'000'000 + window_.front().offset_ns;
}

}  // namespace roverd
