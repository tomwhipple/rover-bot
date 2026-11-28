// Unit tests of roverd's pure parts: framing, parsing, the mixer and the clock map.
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <string>
#include <vector>

#include "clock_map.hpp"
#include "protocol.hpp"

using namespace roverd;

static int g_checks = 0;
static int g_failures = 0;

#define CHECK(cond)                                                             \
    do {                                                                        \
        ++g_checks;                                                             \
        if (!(cond)) {                                                          \
            ++g_failures;                                                       \
            std::fprintf(stderr, "%s:%d: CHECK failed: %s\n", __FILE__, __LINE__, #cond); \
        }                                                                       \
    } while (0)

static void test_framing() {
    CHECK(checksum("") == 0);
    CHECK(checksum("S") == 'S');
    CHECK(frame("S") == "$S*53\n");
    CHECK(cmd_stop() == "$S*53\n");
    CHECK(cmd_hello() == "$?*3F\n");
    CHECK(cmd_drive(-150, 75, 7) == frame("V -150 75 7"));
    CHECK(cmd_pan(90) == frame("P 90"));
    CHECK(unframe("$S*53") == std::optional<std::string>("S"));
    CHECK(unframe("$S*53\r") == std::optional<std::string>("S"));
    CHECK(unframe("$S*53 ") == std::nullopt);
    CHECK(unframe("$S*54") == std::nullopt);
    CHECK(unframe("$S*5") == std::nullopt);
    CHECK(unframe("S*53") == std::nullopt);
    CHECK(unframe("$S") == std::nullopt);
    CHECK(unframe("$S*5G") == std::nullopt);
    CHECK(unframe("\x01garbage$S*53") == std::optional<std::string>("S"));
    CHECK(unframe("$S*53$") == std::nullopt);
    const std::string t = "T 1 2 3 4 5 6 7 8 9";
    CHECK(unframe(frame(t).substr(0, frame(t).size() - 1)) == std::optional<std::string>(t));
}

static void test_line_assembler() {
    LineAssembler a(16);
    std::vector<std::string> lines;
    auto sink = [&](std::string_view l) { lines.emplace_back(l); };
    a.feed("ab", 2, sink);
    CHECK(lines.empty());
    a.feed("c\nde\n\nf", 7, sink);
    CHECK(lines.size() == 2 && lines[0] == "abc" && lines[1] == "de");
    a.feed("\n0123456789abcdefXYZ\nok\n", 24, sink);
    CHECK(lines.size() == 4 && lines[2] == "f" && lines[3] == "ok");
    CHECK(a.overlong() == 1);
}

static void test_parse_hello() {
    auto h = parse_hello("H rover 2.0.1 pwm_max=120 standoff_cm=80 min_mv=10500 timeout_ms=250");
    CHECK(h.has_value());
    if (!h) return;  // CHECK does not stop the test
    CHECK(h->name == "rover");
    CHECK(h->version == "2.0.1");
    CHECK(h->pwm_max == 120);
    CHECK(h->timeout_ms == 250);
    CHECK(h->limits.size() == 4);
    CHECK(h->limits.at("standoff_cm") == "80");
    auto d = parse_hello("H rover 2 pwm_max=abc");
    CHECK(d && d->pwm_max == DEFAULT_PWM_MAX);
    CHECK(!parse_hello("H rover"));
    CHECK(!parse_hello("T rover 1"));
}

static void test_parse_telemetry() {
    auto t = parse_telemetry("T 4294967295 11100 999 -150 150 90 6 1234 65535");
    CHECK(t.has_value());
    if (!t) return;
    CHECK(t->ms == 4294967295u);
    CHECK(t->mv == 11100);
    CHECK(t->cm == 999);
    CHECK(t->left == -150 && t->right == 150);
    CHECK(t->pan == 90);
    CHECK(t->flags == 6);
    CHECK(t->loop_us == 1234);
    CHECK(t->seq == 65535);
    CHECK(!parse_telemetry("T 1 2 3 4 5 6 7 8"));
    CHECK(!parse_telemetry("T 1 2 3 4 5 6 7 8 x"));
    CHECK(!parse_telemetry("T -1 2 3 4 5 6 7 8 9"));
    CHECK(!parse_telemetry("T 4294967296 2 3 4 5 6 7 8 9"));
    CHECK(!parse_telemetry("T 1 2 3 4 5 6 7 8 99999999999999999999999"));
}

static void test_parse_error() {
    auto e = parse_error("E 3 bad drive  args");
    CHECK(e && e->code == 3 && e->text == "bad drive  args");
    auto f = parse_error("E E");
    CHECK(f && f->code == 0 && f->text.empty());
    CHECK(!parse_error("E"));
}

static void test_mix() {
    auto w = mix(1, 0, 150);
    CHECK(w.left == 150 && w.right == 150);
    w = mix(0, 1, 150);  // positive angular turns left: right wheel forward
    CHECK(w.left == -150 && w.right == 150);
    w = mix(0.5, 0.25, 150);
    CHECK(w.left == 38 && w.right == 113);  // 37.5 rounds away from zero
    w = mix(1, 1, 150);  // (0, 300) scaled to (0, 150)
    CHECK(w.left == 0 && w.right == 150);
    w = mix(0.8, -0.6, 100);  // (140, 20) scaled by 100/140
    CHECK(w.left == 100 && w.right == 14);
    w = mix(-1, -1, 150);
    CHECK(w.left == 0 && w.right == -150);
    w = mix(5, 0, 150);  // clamped input
    CHECK(w.left == 150 && w.right == 150);
    w = mix(std::nan(""), 0.5, 150);
    CHECK(w.left == -75 && w.right == 75);
    CHECK(pan_degrees(-5) == std::optional<int>(-5));
    CHECK(pan_degrees(-200) == std::optional<int>(-90));
    CHECK(pan_degrees(200) == std::optional<int>(90));
    CHECK(pan_degrees(44.6) == std::optional<int>(45));
    CHECK(!pan_degrees(std::nan("")));
}

static void test_flag_names() {
    CHECK(flag_name(FLAG_MOVING) == "MOVING");
    CHECK(flag_name(FLAG_BAD_LINE) == "BAD_LINE");
    CHECK(flag_name(64) == "FLAG_64");
}

static constexpr int64_t MS = 1'000'000;

static void test_clock_basic() {
    ClockMap c;
    const int64_t base = INT64_C(1'700'000'000'000) * MS;
    // Firmware at 1000 ms; first line seen 5 ms late, later lines 1 ms late.
    CHECK(c.map(1000, base + 5 * MS) == base + 5 * MS);
    CHECK(c.map(1050, base + 51 * MS) == base + 51 * MS);  // offset improved by 4 ms
    CHECK(c.map(1100, base + 109 * MS) == base + 101 * MS);  // a late line maps onto the minimum
    CHECK(c.firmware_ns() == 1100 * MS);
    CHECK(c.restarts() == 0);
}

static void test_clock_window() {
    ClockMap c(1000 * MS);
    const int64_t base = 1'000'000 * MS;
    c.map(0, base + 1 * MS);  // offset base + 1ms
    for (int i = 1; i <= 20; ++i) c.map(static_cast<uint32_t>(i * 50), base + (i * 50 + 3) * MS);
    // The 1 ms sample left the 1 s window: the minimum is now 3 ms.
    CHECK(c.map(1050, base + 1053 * MS) == base + 1053 * MS);
}

static void test_clock_wrap() {
    ClockMap c;
    const int64_t base = 5'000'000 * MS;
    const uint32_t near = 0xFFFFFFFFu - 20;
    CHECK(c.map(near, base) == base);
    CHECK(c.map(near + 50u, base + 50 * MS) == base + 50 * MS);  // wrapped past 2^32
    CHECK(c.restarts() == 0);
    CHECK(c.firmware_ns() == (int64_t{near} + 50) * MS);
}

static void test_clock_restart() {
    ClockMap c;
    const int64_t base = 9'000'000 * MS;
    c.map(500000, base);
    c.map(500050, base + 50 * MS);
    // Firmware reboots: millis goes back to a small number.
    CHECK(c.map(30, base + 2000 * MS) == base + 2000 * MS);
    CHECK(c.restarts() == 1);
    CHECK(c.map(80, base + 2051 * MS) == base + 2050 * MS);
    // Reboot after a long uptime: a small millis looks like a forward wrap, but the host clock
    // has not moved that far.
    ClockMap d;
    d.map(3'000'000'000u, base);
    CHECK(d.map(100, base + 3000 * MS) == base + 3000 * MS);
    CHECK(d.restarts() == 1);
    // A long silence with the firmware still running is not a restart.
    ClockMap e;
    e.map(1000, base);
    CHECK(e.map(61000, base + 60000 * MS) == base + 60000 * MS);
    CHECK(e.restarts() == 0);
}

int main() {
    test_framing();
    test_line_assembler();
    test_parse_hello();
    test_parse_telemetry();
    test_parse_error();
    test_mix();
    test_flag_names();
    test_clock_basic();
    test_clock_window();
    test_clock_wrap();
    test_clock_restart();
    std::printf("roverd_units: %d checks, %d failed\n", g_checks, g_failures);
    return g_failures ? EXIT_FAILURE : EXIT_SUCCESS;
}
