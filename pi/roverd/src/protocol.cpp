#include "protocol.hpp"

#include <algorithm>
#include <cerrno>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <vector>

namespace roverd {

namespace {

std::vector<std::string_view> split(std::string_view s) {
    std::vector<std::string_view> out;
    size_t i = 0;
    while (i < s.size()) {
        while (i < s.size() && s[i] == ' ') ++i;
        size_t j = i;
        while (j < s.size() && s[j] != ' ') ++j;
        if (j > i) out.push_back(s.substr(i, j - i));
        i = j;
    }
    return out;
}

std::optional<int64_t> to_int(std::string_view s) {
    if (s.empty() || s.size() > 20) return std::nullopt;
    char buf[24];
    std::copy(s.begin(), s.end(), buf);
    buf[s.size()] = '\0';
    char* end = nullptr;
    errno = 0;
    const long long v = std::strtoll(buf, &end, 10);
    if (errno != 0 || end != buf + s.size()) return std::nullopt;
    return static_cast<int64_t>(v);
}

int hex_digit(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    return -1;
}

}  // namespace

std::string flag_name(uint32_t bit) {
    switch (bit) {
        case FLAG_MOVING: return "MOVING";
        case FLAG_BLOCKED: return "BLOCKED";
        case FLAG_LOW_PACK: return "LOW_PACK";
        case FLAG_CMD_TIMEOUT: return "CMD_TIMEOUT";
        case FLAG_BAD_LINE: return "BAD_LINE";
        default: return "FLAG_" + std::to_string(bit);
    }
}

uint8_t checksum(std::string_view payload) {
    uint8_t x = 0;
    for (char c : payload) x ^= static_cast<uint8_t>(c);
    return x;
}

std::string frame(std::string_view payload) {
    char hex[4];
    std::snprintf(hex, sizeof hex, "%02X", checksum(payload));
    std::string out;
    out.reserve(payload.size() + 5);
    out += '$';
    out += payload;
    out += '*';
    out += hex;
    out += '\n';
    return out;
}

std::optional<std::string> unframe(std::string_view line) {
    if (!line.empty() && line.back() == '\r') line.remove_suffix(1);
    const size_t dollar = line.rfind('$');
    if (dollar == std::string_view::npos) return std::nullopt;
    line.remove_prefix(dollar + 1);
    if (line.size() < 3 || line[line.size() - 3] != '*') return std::nullopt;
    const int hi = hex_digit(line[line.size() - 2]);
    const int lo = hex_digit(line[line.size() - 1]);
    if (hi < 0 || lo < 0) return std::nullopt;
    const std::string_view payload = line.substr(0, line.size() - 3);
    if (checksum(payload) != static_cast<uint8_t>(hi * 16 + lo)) return std::nullopt;
    return std::string(payload);
}

std::optional<Hello> parse_hello(std::string_view payload) {
    const auto tok = split(payload);
    if (tok.size() < 3 || tok[0] != "H") return std::nullopt;
    Hello h;
    h.name = std::string(tok[1]);
    h.version = std::string(tok[2]);
    for (size_t i = 3; i < tok.size(); ++i) {
        const size_t eq = tok[i].find('=');
        if (eq == std::string_view::npos || eq == 0) continue;
        h.limits[std::string(tok[i].substr(0, eq))] = std::string(tok[i].substr(eq + 1));
    }
    if (auto it = h.limits.find("pwm_max"); it != h.limits.end()) {
        if (auto v = to_int(it->second); v && *v > 0 && *v <= 255) h.pwm_max = static_cast<int>(*v);
    }
    if (auto it = h.limits.find("timeout_ms"); it != h.limits.end()) {
        if (auto v = to_int(it->second); v && *v > 0 && *v <= 60000) h.timeout_ms = static_cast<int>(*v);
    }
    return h;
}

std::optional<Telemetry> parse_telemetry(std::string_view payload) {
    const auto tok = split(payload);
    if (tok.size() < 10 || tok[0] != "T") return std::nullopt;
    int64_t v[9];
    for (size_t i = 0; i < 9; ++i) {
        auto x = to_int(tok[i + 1]);
        if (!x) return std::nullopt;
        v[i] = *x;
    }
    if (v[0] < 0 || v[0] > INT64_C(0xFFFFFFFF) || v[6] < 0 || v[6] > INT64_C(0xFFFFFFFF)) {
        return std::nullopt;
    }
    Telemetry t;
    t.ms = static_cast<uint32_t>(v[0]);
    t.mv = v[1];
    t.cm = v[2];
    t.left = v[3];
    t.right = v[4];
    t.pan = v[5];
    t.flags = static_cast<uint32_t>(v[6]);
    t.loop_us = v[7];
    t.seq = v[8];
    return t;
}

std::optional<FirmwareError> parse_error(std::string_view payload) {
    const auto tok = split(payload);
    if (tok.size() < 2 || tok[0] != "E") return std::nullopt;
    FirmwareError e;
    if (auto c = to_int(tok[1])) e.code = *c;
    const size_t at = static_cast<size_t>(tok[1].data() - payload.data()) + tok[1].size();
    std::string_view rest = payload.substr(at);
    while (!rest.empty() && rest.front() == ' ') rest.remove_prefix(1);
    e.text = std::string(rest);
    return e;
}

std::string cmd_drive(int left, int right, unsigned seq) {
    return frame("V " + std::to_string(left) + " " + std::to_string(right) + " " +
                 std::to_string(seq));
}

std::string cmd_pan(int deg) { return frame("P " + std::to_string(deg)); }
std::string cmd_stop() { return frame("S"); }
std::string cmd_hello() { return frame("?"); }

Wheels mix(double linear, double angular, int pwm_max) {
    auto clamp1 = [](double x) { return std::isnan(x) ? 0.0 : std::clamp(x, -1.0, 1.0); };
    const double lin = clamp1(linear);
    const double ang = clamp1(angular);
    double l = (lin - ang) * pwm_max;
    double r = (lin + ang) * pwm_max;
    const double m = std::max(std::fabs(l), std::fabs(r));
    if (m > pwm_max) {
        l *= pwm_max / m;
        r *= pwm_max / m;
    }
    return {static_cast<int>(std::lround(l)), static_cast<int>(std::lround(r))};
}

std::optional<int> pan_degrees(double value) {
    if (std::isnan(value)) return std::nullopt;
    return static_cast<int>(std::lround(std::clamp(value, -90.0, 90.0)));
}

}  // namespace roverd
