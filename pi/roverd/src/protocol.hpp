// Rover firmware serial protocol v2 (robo-control docs/interfaces.md section 4): framing,
// parsing of firmware lines, command formatting, and the drive mixer (section 5).
#pragma once

#include <cstddef>
#include <cstdint>
#include <map>
#include <optional>
#include <string>
#include <string_view>

namespace roverd {

constexpr int DEFAULT_PWM_MAX = 150;
constexpr int DEFAULT_TIMEOUT_MS = 300;

enum Flag : uint32_t {
    FLAG_MOVING = 1,
    FLAG_BLOCKED = 2,
    FLAG_LOW_PACK = 4,
    FLAG_CMD_TIMEOUT = 8,
    FLAG_BAD_LINE = 16,
};

// Name of a single flag bit; "FLAG_<bit>" for bits the protocol does not define.
std::string flag_name(uint32_t bit);

uint8_t checksum(std::string_view payload);
// "$<payload>*HH\n"
std::string frame(std::string_view payload);
// The payload of "$<payload>*HH" (a trailing '\r' is tolerated); nullopt when the frame or its
// checksum is wrong. Bytes before the last '$' are line noise and ignored.
std::optional<std::string> unframe(std::string_view line);

// Splits a byte stream into lines. Partial reads are joined; a line longer than max_len is
// discarded up to its newline and counted as overlong.
class LineAssembler {
public:
    explicit LineAssembler(size_t max_len = 256) : max_len_(max_len) {}

    template <class F>
    void feed(const char* data, size_t n, F&& on_line) {
        for (size_t i = 0; i < n; ++i) {
            const char c = data[i];
            if (c == '\n') {
                if (overflow_) {
                    overflow_ = false;
                } else if (!buf_.empty()) {
                    on_line(std::string_view(buf_));
                }
                buf_.clear();
            } else if (!overflow_) {
                if (buf_.size() >= max_len_) {
                    overflow_ = true;
                    ++overlong_;
                    buf_.clear();
                } else {
                    buf_.push_back(c);
                }
            }
        }
    }
    void reset() {
        buf_.clear();
        overflow_ = false;
    }
    uint64_t overlong() const { return overlong_; }

private:
    size_t max_len_;
    std::string buf_;
    bool overflow_ = false;
    uint64_t overlong_ = 0;
};

struct Hello {
    std::string name;
    std::string version;
    std::map<std::string, std::string> limits;  // key=value tokens
    int pwm_max = DEFAULT_PWM_MAX;
    int timeout_ms = DEFAULT_TIMEOUT_MS;
};

struct Telemetry {
    uint32_t ms = 0;
    int64_t mv = 0;
    int64_t cm = 0;
    int64_t left = 0;
    int64_t right = 0;
    int64_t pan = 0;
    uint32_t flags = 0;
    int64_t loop_us = 0;
    int64_t seq = 0;
};

struct FirmwareError {
    int64_t code = 0;
    std::string text;
};

std::optional<Hello> parse_hello(std::string_view payload);
std::optional<Telemetry> parse_telemetry(std::string_view payload);
std::optional<FirmwareError> parse_error(std::string_view payload);

std::string cmd_drive(int left, int right, unsigned seq);
std::string cmd_pan(int deg);
std::string cmd_stop();
std::string cmd_hello();

struct Wheels {
    int left = 0;
    int right = 0;
};

// left = (linear - angular) * pwm_max, right = (linear + angular) * pwm_max, both scaled down
// together when either exceeds pwm_max. Inputs are clamped to [-1, 1]; NaN counts as 0.
Wheels mix(double linear, double angular, int pwm_max);
// Clamp to [0, 180] and round; NaN gives nullopt.
std::optional<int> pan_degrees(double value);

}  // namespace roverd
