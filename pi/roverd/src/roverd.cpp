#include "roverd.hpp"

#include <algorithm>
#include <atomic>
#include <cerrno>
#include <chrono>
#include <csignal>
#include <cstdio>
#include <cstring>
#include <deque>
#include <mutex>
#include <optional>
#include <utility>
#include <vector>

#include <fcntl.h>
#include <poll.h>
#include <unistd.h>

#include "clock_map.hpp"
#include "protocol.hpp"
#include "robo/bus_env.hpp"
#include "robo/control_lease.hpp"
#include "robo_v1.pb.h"
#include "serial_port.hpp"

#ifndef ROVERD_VERSION
#define ROVERD_VERSION "0.0.0"
#endif

namespace roverd {

const char* version() { return ROVERD_VERSION; }

namespace {

namespace pb = robo::v1;

constexpr int64_t NO_LINK_AFTER_MS = 500;
constexpr int64_t NO_LINK_PERIOD_MS = 500;
constexpr int64_t INFO_PERIOD_MS = 2000;
constexpr int64_t CONTROL_PERIOD_MS = 500;
constexpr int64_t REOPEN_PERIOD_MS = 500;
constexpr int64_t HELLO_RETRY_MS = 1000;
constexpr int64_t BAD_LINE_EVENT_PERIOD_MS = 5000;
constexpr int64_t STATS_LOG_PERIOD_MS = 10000;
// READY waits for a subscriber on the event topic: an event put before a peer has linked is lost.
constexpr int64_t READY_POLL_MS = 100;
constexpr int64_t READY_WAIT_MS = 10000;

std::atomic<int> g_wake_fd{-1};
volatile std::sig_atomic_t g_quit = 0;

void on_signal(int) {
    g_quit = 1;
    const int fd = g_wake_fd.load();
    if (fd >= 0) {
        const char b = 'q';
        [[maybe_unused]] ssize_t n = ::write(fd, &b, 1);
    }
}

int64_t mono_ms() {
    return std::chrono::duration_cast<std::chrono::milliseconds>(
               std::chrono::steady_clock::now().time_since_epoch())
        .count();
}

int64_t wall_ns() { return static_cast<int64_t>(robo::bus::now_ns()); }

std::string host_name() {
    char buf[256] = {0};
    return ::gethostname(buf, sizeof buf - 1) == 0 && buf[0] ? buf : "unknown host";
}

// True when roverd is being stopped because its host is shutting down or rebooting (systemd).
bool host_stopping() {
    FILE* p = ::popen("systemctl is-system-running 2>/dev/null", "r");
    if (!p) return false;
    char buf[32] = {0};
    const bool got = std::fgets(buf, sizeof buf, p) != nullptr;
    ::pclose(p);
    return got && std::string(buf).rfind("stopping", 0) == 0;
}

void log(const std::string& s) { std::fprintf(stderr, "roverd: %s\n", s.c_str()); }

enum class Inbound { Request, Intent, Stop };

class Adapter {
public:
    explicit Adapter(const Options& opts) : Adapter(opts, robo::bus::open_adapter_session()) {}

    Adapter(const Options& opts, robo::bus::AdapterSession bus)
        : opts_(opts),
          session_(std::move(bus.session)),
          listen_(std::move(bus.listen)),
          hostname_(robo::bus::host_name()),
          serial_(opts.serial),
          synced_(robo::bus::clock_synced()) {
        if (::pipe(wake_) != 0) throw std::runtime_error(std::string("pipe: ") + std::strerror(errno));
        for (int fd : wake_) {
            ::fcntl(fd, F_SETFL, ::fcntl(fd, F_GETFL) | O_NONBLOCK);
            ::fcntl(fd, F_SETFD, FD_CLOEXEC);
        }
        const auto& id = opts_.robot_id;
        pub_info_.emplace(robo::bus::declare_robot_publisher(session_, id, "info"));
        pub_state_.emplace(robo::bus::declare_robot_publisher(session_, id, "state"));
        pub_event_.emplace(robo::bus::declare_robot_publisher(session_, id, "event"));
        pub_control_.emplace(robo::bus::declare_robot_publisher(session_, id, "control/state"));
        subscribe("control/request", Inbound::Request);
        subscribe("cmd/intent", Inbound::Intent);
        subscribe("cmd/stop", Inbound::Stop);
    }

    ~Adapter() {
        g_wake_fd.store(-1);
        for (int fd : wake_) ::close(fd);
    }

    int run() {
        g_wake_fd.store(wake_[1]);
        struct sigaction sa {};
        sa.sa_handler = on_signal;
        sigemptyset(&sa.sa_mask);
        ::sigaction(SIGINT, &sa, nullptr);
        ::sigaction(SIGTERM, &sa, nullptr);
        ::signal(SIGPIPE, SIG_IGN);

        start_ms_ = mono_ms();
        log(std::string("version ") + version() + ", robot " + opts_.robot_id + ", serial " +
            opts_.serial + ", ns " + robo::bus::ns() + ", listening on " + listen_);
        publish_info();
        publish_control();
        next_info_ms_ = start_ms_ + INFO_PERIOD_MS;
        next_stats_ms_ = start_ms_ + STATS_LOG_PERIOD_MS;

        std::vector<char> buf(4096);
        while (!g_quit) {
            int64_t now = mono_ms();
            if (!serial_.is_open() && now >= next_open_ms_) try_open(now);
            if (serial_.is_open() && !hello_seen_on_port_ && now >= next_hello_ms_) {
                send(cmd_hello());
                next_hello_ms_ = now + HELLO_RETRY_MS;
            }

            pollfd fds[2] = {{wake_[0], POLLIN, 0}, {serial_.fd(), POLLIN, 0}};
            const nfds_t nfds = serial_.is_open() ? 2 : 1;
            const int timeout = static_cast<int>(std::clamp<int64_t>(next_deadline(now) - now, 0, 500));
            const int pr = ::poll(fds, nfds, timeout);
            if (pr < 0 && errno != EINTR) {
                log(std::string("poll: ") + std::strerror(errno));
                break;
            }
            if (pr > 0 && (fds[0].revents & POLLIN)) {
                char sink[64];
                while (::read(wake_[0], sink, sizeof sink) > 0) {
                }
            }
            if (pr > 0 && nfds == 2 && fds[1].revents) {
                const int64_t rx_ns = wall_ns();
                now = mono_ms();
                for (int i = 0; i < 16 && serial_.is_open(); ++i) {
                    const ssize_t n = serial_.read(buf.data(), buf.size());
                    if (n < 0) {
                        serial_lost(now);
                        break;
                    }
                    if (n == 0) {
                        if (fds[1].revents & (POLLHUP | POLLERR | POLLNVAL)) {
                            serial_.close();
                            serial_lost(now);
                        }
                        break;
                    }
                    lines_.feed(buf.data(), static_cast<size_t>(n),
                                [&](std::string_view line) { on_line(line, rx_ns, now); });
                }
            }
            now = mono_ms();
            process_inbox(now);
            if (lease_.tick(now)) on_expired(now);
            periodic(now);
        }

        log("shutting down");
        send(cmd_stop());
        serial_.close();
        event(pb::SEVERITY_WARNING, "SHUTDOWN",
              host_stopping() ? host_name() + " is shutting down" : "roverd on " + host_name() + " is stopping");
        session_.close();
        log("relayed " + std::to_string(relayed_) + " intents, dropped " +
            std::to_string(lease_.dropped()) + ", bad lines " + std::to_string(bad_lines_));
        return 0;
    }

private:
    struct Item {
        Inbound kind;
        std::string bytes;
    };

    void subscribe(const std::string& topic, Inbound kind) {
        session_.declare_background_subscriber(
            zenoh::KeyExpr(robo::bus::robot_key(opts_.robot_id, topic)),
            [this, kind](zenoh::Sample& sample) {
                {
                    std::lock_guard<std::mutex> lock(inbox_mu_);
                    inbox_.push_back({kind, sample.get_payload().as_string()});
                }
                const char b = 'm';
                [[maybe_unused]] ssize_t n = ::write(wake_[1], &b, 1);
            },
            zenoh::closures::none);
    }

    // ---- bus out ----

    void stamp(pb::Header* h, uint64_t& seq, int64_t t_ns, int64_t sim_ns = 0) {
        h->set_source_time_ns(static_cast<uint64_t>(t_ns));
        h->set_synced(synced_);
        h->set_seq(++seq);
        h->set_sim_time_ns(static_cast<uint64_t>(sim_ns));
    }

    template <class M>
    void put(zenoh::Publisher& pub, const M& msg) {
        std::string bytes;
        if (!msg.SerializeToString(&bytes)) {
            log("serialize failed");
            return;
        }
        zenoh::ZResult err = 0;
        pub.put(zenoh::Bytes(std::move(bytes)), zenoh::Publisher::PutOptions::create_default(), &err);
        if (err != 0) log("publish failed: " + std::to_string(err));
    }

    void event(pb::Severity sev, const std::string& code, const std::string& text) {
        pb::Event e;
        stamp(e.mutable_header(), seq_event_, wall_ns());
        e.set_severity(sev);
        e.set_code(code);
        e.set_text(text);
        put(*pub_event_, e);
        log("event " + code + ": " + text);
    }

    void publish_info() {
        pb::Info m;
        stamp(m.mutable_header(), seq_info_, wall_ns());
        m.set_robot_id(opts_.robot_id);
        m.set_kind("diff_drive");
        m.set_firmware_version(hello_ ? hello_->version : "");
        m.set_adapter_version(std::string("roverd ") + version());
        m.set_description_commit(opts_.description_commit);
        m.set_simulated(opts_.simulated);
        for (const auto& [k, v] : opts_.cameras) (*m.mutable_cameras())[k] = v;
        if (hello_) {
            for (const auto& [k, v] : hello_->limits) (*m.mutable_limits())[k] = v;
        }
        m.set_hostname(hostname_);
        for (const auto& e : robo::bus::advertised_endpoints(listen_, robo::bus::local_ipv4_addresses())) {
            m.add_endpoints(e);
        }
        put(*pub_info_, m);
    }

    void publish_control() {
        pb::ControlState m;
        stamp(m.mutable_header(), seq_control_, wall_ns());
        m.set_holder_id(lease_.holder_id());
        m.set_holder_role(static_cast<pb::Role>(static_cast<int>(lease_.holder_role())));
        m.set_holder_label(lease_.holder_label());
        m.set_held_since_ns(lease_.held() ? held_since_ns_ : 0);
        m.set_stopped(lease_.stopped());
        put(*pub_control_, m);
        control_version_ = lease_.version();
        next_control_ms_ = mono_ms() + CONTROL_PERIOD_MS;
    }

    void publish_state(int64_t now, bool link, int64_t t_ns, int64_t sim_ns) {
        // Mapped firmware times can step back by the improvement of the offset estimate.
        t_ns = std::max(t_ns, last_state_ns_ + 1);
        last_state_ns_ = t_ns;
        pb::State m;
        stamp(m.mutable_header(), seq_state_, t_ns, sim_ns);
        const Telemetry& t = last_t_;
        const uint32_t flags = link ? t.flags : 0;
        if (!link) {
            m.set_mode("NO_LINK");
        } else if (flags & FLAG_LOW_PACK) {
            m.set_mode("LOW_PACK");
        } else if (flags & FLAG_BLOCKED) {
            m.set_mode("BLOCKED");
        } else if (flags & FLAG_MOVING) {
            m.set_mode("MOVING");
        } else {
            m.set_mode("IDLE");
        }
        const int timeout_ms = hello_ ? hello_->timeout_ms : DEFAULT_TIMEOUT_MS;
        const bool cmd_live = last_v_ms_ >= 0 && now - last_v_ms_ < timeout_ms;
        auto add_act = [&](const char* name, double commanded, double applied, bool active) {
            pb::Actuator* a = m.add_actuators();
            a->set_name(name);
            a->set_commanded(static_cast<float>(commanded));
            a->set_applied(static_cast<float>(applied));
            a->set_has_measured(false);
            a->set_active(active);
        };
        const auto d = [](int64_t v) { return static_cast<double>(v); };
        add_act("left_wheel", cmd_live ? commanded_.left : 0, link ? d(t.left) : 0, link && t.left != 0);
        add_act("right_wheel", cmd_live ? commanded_.right : 0, link ? d(t.right) : 0, link && t.right != 0);
        add_act("pan", pan_cmd_ ? *pan_cmd_ : d(t.pan), d(t.pan), link);
        pb::Sensor* range = m.add_sensors();
        range->set_name("range");
        range->set_value(static_cast<float>(t.cm));
        range->set_valid(link && has_t_ && t.cm != 999 && t.cm >= 0);
        pb::Sensor* pack = m.add_sensors();
        pack->set_name("pack");
        pack->set_value(static_cast<float>(t.mv) / 1000.0f);
        pack->set_valid(link && has_t_);
        m.set_pack_v(link ? static_cast<float>(t.mv) / 1000.0f : 0.0f);
        for (uint32_t bit = 1; bit != 0 && bit <= flags; bit <<= 1) {
            if (flags & bit) m.add_flags(flag_name(bit));
        }
        m.set_loop_time_ms(link ? static_cast<float>(t.loop_us) / 1000.0f : 0.0f);
        m.set_deadline_misses(0);
        m.set_firmware_link_age_ms(last_heard_ms_ >= 0 ? static_cast<float>(now - last_heard_ms_) : -1.0f);
        m.set_last_intent_seq(last_intent_seq_);
        put(*pub_state_, m);
    }

    // ---- serial ----

    void send(const std::string& framed) {
        if (!serial_.is_open()) return;
        if (!serial_.write(framed)) log("serial write failed: " + serial_.error());
    }

    void send_stop() {
        send(cmd_stop());
        last_v_ms_ = -1;
        commanded_ = {};
        // S halts the pan too, so the firmware no longer holds the target sent before.
        pan_cmd_.reset();
        pan_sent_.reset();
    }

    void try_open(int64_t now) {
        next_open_ms_ = now + REOPEN_PERIOD_MS;
        if (!serial_.open()) {
            if (serial_.error() != last_open_error_) log("serial: " + serial_.error());
            last_open_error_ = serial_.error();
            return;
        }
        last_open_error_.clear();
        lines_.reset();
        hello_seen_on_port_ = false;
        next_hello_ms_ = now;
        prev_flags_ = 0;
        pan_sent_.reset();
        event(pb::SEVERITY_INFO, "SERIAL_OPEN", serial_.path());
    }

    void serial_lost(int64_t now) {
        next_open_ms_ = now + REOPEN_PERIOD_MS;
        event(pb::SEVERITY_WARNING, "SERIAL_LOST", serial_.error().empty() ? serial_.path() : serial_.error());
        clock_.reset();
    }

    void on_line(std::string_view line, int64_t rx_ns, int64_t now) {
        const auto payload = unframe(line);
        if (!payload) {
            ++bad_lines_;
            return;
        }
        last_heard_ms_ = now;
        if (payload->empty()) return;
        switch ((*payload)[0]) {
            case 'H':
                if (auto h = parse_hello(*payload)) on_hello(std::move(*h));
                else ++bad_lines_;
                break;
            case 'T':
                if (auto t = parse_telemetry(*payload)) on_telemetry(*t, rx_ns, now);
                else ++bad_lines_;
                break;
            case 'E':
                if (auto e = parse_error(*payload)) {
                    event(pb::SEVERITY_WARNING, "FIRMWARE_ERROR", std::to_string(e->code) + " " + e->text);
                }
                break;
            default:
                ++unknown_lines_;
        }
    }

    void on_hello(Hello h) {
        const bool changed = !hello_ || hello_->version != h.version || hello_->limits != h.limits;
        // The firmware says hello at boot: whatever pan it had is gone.
        pan_sent_.reset();
        hello_ = std::move(h);
        if (!hello_seen_on_port_ || changed) {
            event(pb::SEVERITY_INFO, "FIRMWARE_HELLO", hello_->name + " " + hello_->version);
        }
        hello_seen_on_port_ = true;
        if (changed) publish_info();
    }

    void on_telemetry(const Telemetry& t, int64_t rx_ns, int64_t now) {
        const int64_t mapped = clock_.map(t.ms, rx_ns);
        const uint32_t rising = t.flags & ~prev_flags_ & ~static_cast<uint32_t>(FLAG_MOVING);
        prev_flags_ = t.flags;
        last_t_ = t;
        has_t_ = true;
        last_t_ms_ = now;
        if (no_link_reported_) {
            no_link_reported_ = false;
            event(pb::SEVERITY_INFO, "LINK_OK", "firmware telemetry resumed");
        }
        for (uint32_t bit = 1; bit != 0 && bit <= rising; bit <<= 1) {
            if (!(rising & bit)) continue;
            const auto sev = bit == FLAG_CMD_TIMEOUT ? pb::SEVERITY_INFO : pb::SEVERITY_WARNING;
            event(sev, flag_name(bit), "firmware flag set");
        }
        publish_state(now, true, mapped, opts_.simulated ? clock_.firmware_ns() : 0);
    }

    // ---- bus in ----

    void process_inbox(int64_t now) {
        std::deque<Item> items;
        {
            std::lock_guard<std::mutex> lock(inbox_mu_);
            items.swap(inbox_);
        }
        for (const auto& it : items) {
            switch (it.kind) {
                case Inbound::Request: on_request(it.bytes, now); break;
                case Inbound::Intent: on_intent(it.bytes, now); break;
                case Inbound::Stop: on_stop(it.bytes, now); break;
            }
        }
        if (lease_.version() != control_version_) publish_control();
    }

    void on_expired(int64_t) {
        held_since_ns_ = 0;
        send_stop();
        event(pb::SEVERITY_WARNING, "CONTROL_EXPIRED",
              lease_.expired_holder() + ": no intent for " + std::to_string(lease_.timeout_ms()) + " ms");
        publish_control();
    }

    void on_request(const std::string& bytes, int64_t now) {
        pb::ControlRequest req;
        if (!req.ParseFromString(bytes)) return;
        if (lease_.tick(now)) on_expired(now);
        const std::string who = req.controller_id() + " (" +
                                robo::to_string(static_cast<robo::Role>(req.role())) +
                                (req.label().empty() ? "" : ", " + req.label()) + ")";
        if (req.action() == pb::ControlRequest::ACTION_TAKE) {
            const auto r = lease_.take(req.controller_id(), static_cast<robo::Role>(req.role()),
                                       req.label(), now);
            switch (r.result) {
                case robo::TakeResult::Granted:
                    held_since_ns_ = wall_ns();
                    event(pb::SEVERITY_INFO, "CONTROL_TAKEN", who);
                    break;
                case robo::TakeResult::Preempted:
                    held_since_ns_ = wall_ns();
                    send_stop();  // the previous holder's last drive command must not run on
                    event(pb::SEVERITY_INFO, "CONTROL_TAKEN", who + " from " + r.previous_holder);
                    break;
                case robo::TakeResult::Retaken:
                    event(pb::SEVERITY_INFO, "CONTROL_TAKEN", who + " again");
                    break;
                case robo::TakeResult::Denied:
                    event(pb::SEVERITY_WARNING, "CONTROL_DENIED",
                          who + ": held by " + r.previous_holder + " (" +
                              robo::to_string(lease_.holder_role()) + ")");
                    break;
            }
        } else if (req.action() == pb::ControlRequest::ACTION_RELEASE) {
            if (lease_.release(req.controller_id(), now).result == robo::ReleaseResult::Released) {
                held_since_ns_ = 0;
                send_stop();
                event(pb::SEVERITY_INFO, "CONTROL_RELEASED", who);
            }
        }
    }

    void on_intent(const std::string& bytes, int64_t now) {
        pb::Intent in;
        if (!in.ParseFromString(bytes)) return;
        const auto r = lease_.accept_intent(in.controller_id(), now);
        if (r.expired) on_expired(now);
        if (r.result != robo::IntentResult::Relay) return;
        const auto& ch = in.channels();
        auto get = [&](const char* name) {
            const auto it = ch.find(name);
            return it == ch.end() ? 0.0 : static_cast<double>(it->second);
        };
        if (const auto it = ch.find("pan"); it != ch.end()) {
            if (auto deg = pan_degrees(it->second)) pan_cmd_ = deg;
        }
        const int pwm_max = hello_ ? hello_->pwm_max : DEFAULT_PWM_MAX;
        const Wheels w = mix(get("linear"), get("angular"), pwm_max);
        v_seq_ = v_seq_ == 0xFFFF ? 1 : v_seq_ + 1;
        send(cmd_drive(w.left, w.right, v_seq_));
        if (pan_cmd_ && pan_cmd_ != pan_sent_ && serial_.is_open()) {
            send(cmd_pan(*pan_cmd_));
            pan_sent_ = pan_cmd_;
        }
        commanded_ = w;
        last_v_ms_ = now;
        last_intent_seq_ = in.header().seq();
        ++relayed_;
    }

    void on_stop(const std::string& bytes, int64_t now) {
        pb::Stop s;
        if (!s.ParseFromString(bytes)) return;
        send_stop();
        const auto r = lease_.stop(now);
        if (r.expired) on_expired(now);
        if (r.result == robo::StopResult::Latched) {
            event(pb::SEVERITY_WARNING, "STOP",
                  s.sender_id() + (s.reason().empty() ? "" : ": " + s.reason()));
        }
    }

    // ---- timers ----

    int64_t next_deadline(int64_t now) const {
        int64_t d = std::min({next_info_ms_, next_control_ms_, lease_.deadline_ms(), next_stats_ms_});
        if (!serial_.is_open()) d = std::min(d, next_open_ms_);
        else if (!hello_seen_on_port_) d = std::min(d, next_hello_ms_);
        const int64_t silent_since = has_t_ ? last_t_ms_ : start_ms_;
        d = std::min(d, std::max(silent_since + NO_LINK_AFTER_MS, next_no_link_ms_));
        if (bad_lines_ != bad_lines_reported_) d = std::min(d, next_bad_line_event_ms_);
        if (!ready_sent_) d = std::min(d, now + READY_POLL_MS);
        return std::max(d, now);
    }

    bool event_heard() {
        zenoh::ZResult err = 0;
        const auto status = pub_event_->get_matching_status(&err);
        return err == 0 && status.matching;
    }

    void periodic(int64_t now) {
        if (!ready_sent_ && (event_heard() || now - start_ms_ >= READY_WAIT_MS)) {
            ready_sent_ = true;
            event(pb::SEVERITY_INFO, "READY", std::string("roverd ") + version() + " on " + host_name());
        }
        const int64_t silent_since = has_t_ ? last_t_ms_ : start_ms_;
        if (now - silent_since >= NO_LINK_AFTER_MS && now >= next_no_link_ms_) {
            if (has_t_ && !no_link_reported_) {
                no_link_reported_ = true;
                event(pb::SEVERITY_WARNING, "NO_LINK",
                      "no telemetry for " + std::to_string(now - last_t_ms_) + " ms");
            }
            publish_state(now, false, wall_ns(), 0);
            next_no_link_ms_ = now + NO_LINK_PERIOD_MS;
        }
        if (now >= next_info_ms_) {
            publish_info();
            next_info_ms_ = now + INFO_PERIOD_MS;
        }
        if (lease_.version() != control_version_ || now >= next_control_ms_) publish_control();
        if (bad_lines_ != bad_lines_reported_ && now >= next_bad_line_event_ms_) {
            event(pb::SEVERITY_WARNING, "SERIAL_BAD_LINE",
                  std::to_string(bad_lines_ - bad_lines_reported_) + " bad line(s) from the firmware, " +
                      std::to_string(bad_lines_) + " in total");
            bad_lines_reported_ = bad_lines_;
            next_bad_line_event_ms_ = now + BAD_LINE_EVENT_PERIOD_MS;
        }
        if (now >= next_stats_ms_) {
            next_stats_ms_ = now + STATS_LOG_PERIOD_MS;
            log("relayed " + std::to_string(relayed_) + ", dropped " + std::to_string(lease_.dropped()) +
                ", bad lines " + std::to_string(bad_lines_) + ", overlong " +
                std::to_string(lines_.overlong()) + ", unknown " + std::to_string(unknown_lines_) +
                ", clock restarts " + std::to_string(clock_.restarts()));
        }
    }

    Options opts_;
    zenoh::Session session_;
    std::string listen_;
    std::string hostname_;
    std::optional<zenoh::Publisher> pub_info_, pub_state_, pub_event_, pub_control_;
    SerialPort serial_;
    LineAssembler lines_;
    ClockMap clock_;
    robo::ControlLease lease_;
    bool synced_;
    int wake_[2] = {-1, -1};

    std::mutex inbox_mu_;
    std::deque<Item> inbox_;

    uint64_t seq_info_ = 0, seq_state_ = 0, seq_event_ = 0, seq_control_ = 0;
    uint64_t control_version_ = ~uint64_t{0};
    int64_t held_since_ns_ = 0;
    int64_t last_state_ns_ = 0;

    std::optional<Hello> hello_;
    bool hello_seen_on_port_ = false;
    Telemetry last_t_;
    bool has_t_ = false;
    bool no_link_reported_ = false;
    uint32_t prev_flags_ = 0;

    Wheels commanded_;
    std::optional<int> pan_cmd_;
    std::optional<int> pan_sent_;
    unsigned v_seq_ = 0;
    uint64_t last_intent_seq_ = 0;
    uint64_t relayed_ = 0;
    uint64_t bad_lines_ = 0, bad_lines_reported_ = 0, unknown_lines_ = 0;
    std::string last_open_error_;

    bool ready_sent_ = false;
    int64_t start_ms_ = 0;
    int64_t last_heard_ms_ = -1;
    int64_t last_t_ms_ = 0;
    int64_t last_v_ms_ = -1;
    int64_t next_open_ms_ = 0;
    int64_t next_hello_ms_ = 0;
    int64_t next_info_ms_ = 0;
    int64_t next_control_ms_ = 0;
    int64_t next_no_link_ms_ = 0;
    int64_t next_bad_line_event_ms_ = 0;
    int64_t next_stats_ms_ = 0;
};

}  // namespace

int run(const Options& opts) {
    try {
        Adapter adapter(opts);
        return adapter.run();
    } catch (const std::exception& e) {
        log(std::string("fatal: ") + e.what());
        return 1;
    }
}

}  // namespace roverd
