"""roverd against a fake firmware on a pty: assertions on the bytes the firmware received and on
the bus messages (robo-control docs/interfaces.md sections 1, 2, 4, 5)."""
from __future__ import annotations

import math
import os
import re
import signal
import socket
import struct
import subprocess
import time
import uuid

import pytest

import robo_v1_pb2 as pb
from conftest import ROVERD_BIN, Bus, start_roverd, stop_roverd
from fake_firmware import BLOCKED, CMD_TIMEOUT, LOW_PACK, MOVING, FakeFirmware, frame

pytestmark = pytest.mark.timeout(60)

HUMAN, AGENT = pb.ROLE_HUMAN, pb.ROLE_AGENT


def f32(x: float) -> float:
    return struct.unpack("f", struct.pack("f", x))[0]


def lround(x: float) -> int:
    return int(math.copysign(math.floor(abs(x) + 0.5), x))


def expected_wheels(linear: float, angular: float, pwm_max: int) -> tuple[int, int]:
    lin = max(-1.0, min(1.0, f32(linear)))
    ang = max(-1.0, min(1.0, f32(angular)))
    left, right = (lin - ang) * pwm_max, (lin + ang) * pwm_max
    m = max(abs(left), abs(right))
    if m > pwm_max:
        left, right = left * pwm_max / m, right * pwm_max / m
    return lround(left), lround(right)


def drives(fake, since: float = 0.0) -> list[tuple[int, int, int]]:
    return [tuple(int(x) for x in p.split()[1:]) for p in fake.received("V", since)]


def actuator(state, name):
    return next(a for a in state.actuators if a.name == name)


def sensor(state, name):
    return next(s for s in state.sensors if s.name == name)


# ---- CLI ----

def test_cli_version_and_usage():
    out = subprocess.run([str(ROVERD_BIN), "--version"], capture_output=True, text=True)
    assert out.returncode == 0 and out.stdout.startswith("roverd ")
    assert subprocess.run([str(ROVERD_BIN)], capture_output=True).returncode == 2
    assert subprocess.run([str(ROVERD_BIN), "--serial", "x", "--camera", "nourl"], capture_output=True).returncode == 2
    assert subprocess.run([str(ROVERD_BIN), "--bogus"], capture_output=True).returncode == 2


# ---- Info, hello ----

def test_info(rig_factory):
    rig = rig_factory(fake={"version": "2.1.0-fake", "pwm_max": 120, "timeout_ms": 250})
    assert rig.fake.received("?"), "roverd asks for the hello at start"
    info = rig.bus.wait("info", lambda m: m.firmware_version != "")
    assert info.robot_id == "rover"
    assert info.kind == "diff_drive"
    assert info.firmware_version == "2.1.0-fake"
    assert info.adapter_version.startswith("roverd ")
    assert info.description_commit == "abc123"
    assert info.simulated is True
    assert dict(info.cameras) == {"mast": "http://127.0.0.1:8081/stream", "overview": "http://127.0.0.1:8082/x"}
    assert dict(info.limits) == {"pwm_max": "120", "standoff_cm": "80", "min_mv": "10500", "timeout_ms": "250"}
    assert info.header.seq >= 1 and info.header.source_time_ns > 0
    # Every 2 s.
    time.sleep(4.3)
    times = [r.mono for r in rig.bus.got("info")]
    gaps = [b - a for a, b in zip(times, times[1:])]
    assert max(gaps) < 2.3, gaps
    assert any(1.8 < g < 2.2 for g in gaps), gaps
    seqs = [m.header.seq for m in rig.bus.msgs("info")]
    assert seqs == list(range(seqs[0], seqs[0] + len(seqs)))


def test_hello_retried_until_answered(rig_factory):
    rig = rig_factory()
    rig.fake.answer_hello = False
    # Re-plug the serial port so roverd starts over without a hello.
    t = time.monotonic()
    rig.fake.replace_pty()
    rig.bus.wait_event("SERIAL_OPEN", since=t, timeout=3)
    rig.fake.wait_received("?", count=3, since=t, timeout=4)
    stamps = [ts for ts, _ in rig.fake.received_timed("?", since=t)]
    assert all(0.8 < b - a < 1.3 for a, b in zip(stamps, stamps[1:])), stamps
    rig.fake.answer_hello = True
    rig.bus.wait_event("FIRMWARE_HELLO", since=t, timeout=3)
    time.sleep(1.5)
    n = len(rig.fake.received("?", since=t))
    time.sleep(1.5)
    assert len(rig.fake.received("?", since=t)) == n, "no more ? once the hello arrived"


# ---- State ----

def test_state_mapping_and_cadence(rig):
    fake, bus = rig.fake, rig.bus
    bus.wait("state", lambda m: m.mode == "IDLE")
    t = time.monotonic()
    time.sleep(2.0)
    states = bus.got("state", since=t + 0.2)
    window = states[-1].mono - states[0].mono
    rate = (len(states) - 1) / window
    assert 18 <= rate <= 22, rate
    s = states[-1].msg
    assert s.mode == "IDLE"
    assert [a.name for a in s.actuators] == ["left_wheel", "right_wheel", "pan"]
    assert all(not a.has_measured for a in s.actuators)
    assert sensor(s, "range").value == 200 and sensor(s, "range").valid
    assert sensor(s, "pack").value == pytest.approx(11.1) and sensor(s, "pack").valid
    assert s.pack_v == pytest.approx(11.1)
    assert s.loop_time_ms == pytest.approx(1.5)
    assert 0 <= s.firmware_link_age_ms < 100
    assert list(s.flags) == []
    assert actuator(s, "pan").applied == 90
    assert bus.events("SERIAL_LOST") == [] and len(bus.events("SERIAL_OPEN")) <= 1, "port stays open"

    # One State per T line: in simulated mode sim_time_ns is the firmware's millis().
    sent = set(fake.t_sent)
    got = [m.msg.header.sim_time_ns // 1_000_000 for m in bus.got("state") if m.msg.mode != "NO_LINK"]
    assert len(got) == len(set(got)), "one State per T line"
    assert set(got) <= sent
    lo, hi = min(got), max(got)
    assert set(got) == {ms for ms in sent if lo <= ms <= hi}

    # Range 999 = no echo.
    fake.cm = 999
    bus.wait("state", lambda m: sensor(m, "range").value == 999 and not sensor(m, "range").valid)
    fake.cm = 200


def test_mode_priority_and_flags(rig):
    fake, bus = rig.fake, rig.bus
    cases = [
        (MOVING, "MOVING", ["MOVING"]),
        (BLOCKED | MOVING, "BLOCKED", ["MOVING", "BLOCKED"]),
        (LOW_PACK | BLOCKED | MOVING, "LOW_PACK", ["MOVING", "BLOCKED", "LOW_PACK"]),
        (CMD_TIMEOUT, "IDLE", ["CMD_TIMEOUT"]),
        (0, "IDLE", []),
    ]
    for flags, mode, names in cases:
        fake.extra_flags = flags
        t = time.monotonic()
        s = bus.wait("state", lambda m: list(m.flags) == names, since=t)
        assert s.mode == mode, (flags, s.mode)
    # NO_LINK outranks everything.
    fake.extra_flags = LOW_PACK
    bus.wait("state", lambda m: m.mode == "LOW_PACK")
    fake.silent = True
    bus.wait("state", lambda m: m.mode == "NO_LINK", timeout=2)


def test_commanded_vs_applied(rig):
    fake, bus = rig.fake, rig.bus
    bus.take_and_wait("console")
    fake.cm = 50  # inside the stand-off: forward is refused
    t = time.monotonic()
    for _ in range(10):
        bus.intent("console", linear=0.5, angular=0.0)
        time.sleep(0.05)
    s = bus.wait("state", lambda m: m.mode == "BLOCKED", since=t)
    assert actuator(s, "left_wheel").commanded == 75 and actuator(s, "right_wheel").commanded == 75
    assert actuator(s, "left_wheel").applied == 0 and actuator(s, "right_wheel").applied == 0
    assert "BLOCKED" in s.flags
    fake.cm = 200
    t = time.monotonic()
    for _ in range(6):
        bus.intent("console", linear=-0.5, angular=0.0)
        time.sleep(0.05)
    s = bus.wait("state", lambda m: m.mode == "MOVING", since=t)
    assert actuator(s, "left_wheel").applied == -75 and actuator(s, "left_wheel").active
    assert actuator(s, "left_wheel").commanded == -75


def test_no_link_when_firmware_silent(rig):
    fake, bus = rig.fake, rig.bus
    bus.wait("state", lambda m: m.mode == "IDLE")
    fake.silent = True
    t = time.monotonic()
    first = bus.wait("state", lambda m: m.mode == "NO_LINK", since=t, timeout=2)
    lag = next(r.mono for r in bus.got("state", t) if r.msg.mode == "NO_LINK") - t
    assert 0.4 < lag < 1.1, lag
    assert not sensor(first, "range").valid and first.firmware_link_age_ms >= 500
    bus.wait_event("NO_LINK", since=t)
    time.sleep(2.0)
    no_link = [r for r in bus.got("state", t) if r.msg.mode == "NO_LINK"]
    rate = (len(no_link) - 1) / (no_link[-1].mono - no_link[0].mono)
    assert 1.6 < rate < 2.4, rate
    assert all(r.msg.mode == "NO_LINK" for r in bus.got("state", no_link[0].mono))
    stamps = [r.msg.header.source_time_ns for r in bus.got("state", t)]
    assert stamps == sorted(stamps)
    t2 = time.monotonic()
    fake.silent = False
    bus.wait("state", lambda m: m.mode == "IDLE", since=t2)
    bus.wait_event("LINK_OK", since=t2)


# ---- control ----

def test_take_deny_preempt_matrix(rig):
    bus, fake = rig.bus, rig.fake
    t = time.monotonic()
    bus.take("agent1", AGENT, "agent one")
    cs = bus.wait("control/state", lambda m: m.holder_id == "agent1", since=t)
    assert cs.holder_role == AGENT and cs.holder_label == "agent one" and not cs.stopped
    held_since = cs.held_since_ns
    assert abs(held_since - time.time_ns()) < 2e9
    assert bus.wait_event("CONTROL_TAKEN", since=t).severity == pb.SEVERITY_INFO

    # Agent vs agent: denied.
    t = time.monotonic()
    bus.take("agent2", AGENT)
    ev = bus.wait_event("CONTROL_DENIED", since=t)
    assert "agent2" in ev.text and "agent1" in ev.text

    # Re-take by the holder keeps held_since.
    t = time.monotonic()
    bus.take("agent1", AGENT, "agent one")
    bus.wait_event("CONTROL_TAKEN", since=t)
    cs = bus.wait("control/state", since=t)
    assert cs.holder_id == "agent1" and cs.held_since_ns == held_since

    # Human pre-empts the agent; the agent's drive is stopped.
    t = time.monotonic()
    bus.take("console", HUMAN, "console")
    ev = bus.wait_event("CONTROL_TAKEN", since=t)
    assert "console" in ev.text and "agent1" in ev.text
    cs = bus.wait("control/state", lambda m: m.holder_id == "console", since=t)
    assert cs.holder_role == HUMAN and cs.held_since_ns > held_since
    fake.wait_received("S", since=t)

    # Agent while a human holds: denied.
    t = time.monotonic()
    bus.take("agent1", AGENT)
    bus.wait_event("CONTROL_DENIED", since=t)
    time.sleep(0.3)
    assert bus.msgs("control/state")[-1].holder_id == "console"

    # Human from human: granted.
    t = time.monotonic()
    bus.take("console2", HUMAN, "other console")
    bus.wait("control/state", lambda m: m.holder_id == "console2", since=t)

    # Release by a non-holder is ignored; by the holder frees control and stops.
    t = time.monotonic()
    bus.release("console")
    time.sleep(0.4)
    assert not bus.events("CONTROL_RELEASED", since=t)
    assert bus.msgs("control/state")[-1].holder_id == "console2"
    t = time.monotonic()
    bus.release("console2")
    bus.wait_event("CONTROL_RELEASED", since=t)
    cs = bus.wait("control/state", lambda m: m.holder_id == "", since=t)
    assert cs.held_since_ns == 0 and cs.holder_role == pb.ROLE_UNSPECIFIED
    fake.wait_received("S", since=t)


def test_control_state_periodic(rig):
    t = time.monotonic()
    time.sleep(1.6)
    n = len(rig.bus.got("control/state", t))
    assert 3 <= n <= 4, n


def test_intents_from_non_holder_never_reach_serial(rig):
    bus, fake = rig.bus, rig.fake
    t = time.monotonic()
    for _ in range(10):  # control is free
        bus.intent("stranger", linear=1.0)
        time.sleep(0.02)
    bus.take_and_wait("console")
    for _ in range(20):
        bus.intent("stranger", linear=1.0, angular=0.5, pan=10)
        time.sleep(0.02)
    time.sleep(0.3)
    assert fake.received("V", t) == [] and fake.received("P", t) == []
    bus.intent("console", linear=0.25)
    fake.wait_received("V", since=t)


def test_exactly_one_v_per_intent(rig):
    bus, fake = rig.bus, rig.fake
    bus.take_and_wait("console")
    t = time.monotonic()
    n = 60
    for i in range(n):
        bus.intent("console", linear=(i % 5) / 5, angular=0.0)
        time.sleep(0.05)
    time.sleep(0.5)
    v = drives(fake, t)
    assert len(v) == n
    seqs = [s for _, _, s in v]
    assert seqs == list(range(seqs[0], seqs[0] + n)), "roverd's own V counter, one per relayed Intent"
    assert [(l, r) for l, r, _ in v] == [expected_wheels((i % 5) / 5, 0.0, 150) for i in range(n)]
    # Nothing is synthesised once the intents stop.
    time.sleep(1.0)
    assert len(fake.received("V", t)) == n
    s = bus.wait("state", since=time.monotonic())
    assert s.last_intent_seq == bus._seq["cmd/intent"]


def test_mixing_uses_hello_pwm_max(rig_factory):
    rig = rig_factory(fake={"pwm_max": 120})
    bus, fake = rig.bus, rig.fake
    bus.wait("info", lambda m: m.firmware_version != "")
    bus.take_and_wait("console")
    cases = [(1, 0), (0, 1), (0, -1), (0.5, 0.25), (1, 1), (0.75, 0.5), (-0.5, 0.75),
             (2, 0), (0, -3), (0.25, -0.125), (-1, -1), (0.3, 0.3)]
    t = time.monotonic()
    for lin, ang in cases:
        bus.intent("console", linear=lin, angular=ang)
        time.sleep(0.03)
    fake.wait_received("V", count=len(cases), since=t)
    got = [(l, r) for l, r, _ in drives(fake, t)]
    want = [expected_wheels(lin, ang, 120) for lin, ang in cases]
    assert got == want
    # Spot checks of the convention: positive angular turns left (right wheel faster).
    assert got[1] == (-120, 120)
    assert got[3] == (30, 90)
    assert got[5] == (24, 120)  # (30, 150) scaled together
    # Absent channels are zero.
    t = time.monotonic()
    bus.intent("console", angular=0.5)
    fake.wait_received("V", since=t)
    assert drives(fake, t)[0][:2] == (-60, 60)


def test_pan_only_on_change(rig):
    bus, fake = rig.bus, rig.fake
    bus.take_and_wait("console")
    t = time.monotonic()
    seq = [dict(pan=90), dict(pan=90), dict(pan=100), dict(), dict(pan=100.4), dict(pan=250),
           dict(linear=0.1), dict(pan=-20), dict(pan=0)]
    for ch in seq:
        bus.intent("console", **ch)
        time.sleep(0.04)
    fake.wait_received("V", count=len(seq), since=t)
    time.sleep(0.2)
    assert fake.received("P", t) == ["P 90", "P 100", "P 180", "P 0"]
    s = bus.wait("state", since=time.monotonic())
    assert actuator(s, "pan").commanded == 0 and actuator(s, "pan").applied == 0
    # Each P follows the V of the same Intent.
    order = [p.split()[0] for p in fake.received(None, t) if p[0] in "VP"]
    assert order[:2] == ["V", "P"]


def test_stop_latches_until_retake(rig):
    bus, fake = rig.bus, rig.fake
    bus.take_and_wait("console")
    for _ in range(5):
        bus.intent("console", linear=0.5)
        time.sleep(0.05)
    t = time.monotonic()
    for _ in range(3):  # a Stop is sent 3 times 50 ms apart
        bus.stop("watchdog", "operator pressed stop")
        time.sleep(0.05)
    fake.wait_received("S", since=t)
    s_time = fake.received_timed("S", t)[0][0]
    assert s_time - t < 0.2
    ev = bus.wait_event("STOP", since=t)
    assert ev.text == "watchdog: operator pressed stop"
    time.sleep(0.2)
    assert len(bus.events("STOP", since=t)) == 1
    cs = bus.wait("control/state", lambda m: m.stopped, since=t)
    assert cs.holder_id == "console"
    t2 = time.monotonic()
    for _ in range(20):
        bus.intent("console", linear=0.5)
        time.sleep(0.05)
    assert fake.received("V", t2) == []
    t3 = time.monotonic()
    bus.take("console")
    bus.wait("control/state", lambda m: not m.stopped and m.holder_id == "console", since=t3)
    bus.intent("console", linear=0.5)
    fake.wait_received("V", since=t3)


def test_lease_expiry(rig):
    bus, fake = rig.bus, rig.fake
    bus.take_and_wait("console")
    for _ in range(10):
        bus.intent("console", linear=0.2)
        time.sleep(0.05)
    last = time.monotonic()
    ev = bus.wait_event("CONTROL_EXPIRED", since=last, timeout=5)
    assert "console" in ev.text
    s_at = fake.received_timed("S", last)[0][0] - last
    assert 2.9 < s_at < 3.4, s_at
    cs = bus.wait("control/state", lambda m: m.holder_id == "", since=last)
    assert not cs.stopped
    # The expired holder's intents are now dropped.
    t = time.monotonic()
    bus.intent("console", linear=0.2)
    time.sleep(0.3)
    assert fake.received("V", t) == []


def test_bad_lines_ignored(rig):
    bus, fake = rig.bus, rig.fake
    bus.wait("state", lambda m: m.mode == "IDLE")
    fake.silent = True
    time.sleep(0.1)
    t = time.monotonic()
    good = frame("T 777000 11000 321 0 0 45 0 100 0")
    bad = bytearray(frame("T 777050 11000 123 0 0 45 0 100 0"))
    bad[-3:-1] = b"00"
    fake.send_raw(bytes(bad))
    fake.send_raw(b"\x00\xffnoise*ZZ\n$T 1 2*\n")
    fake.send_raw(b"x" * 400 + b"\n")
    fake.send_raw(good[:9])
    time.sleep(0.05)
    fake.send_raw(good[9:])
    s = bus.wait("state", lambda m: sensor(m, "range").value == 321, since=t)
    assert actuator(s, "pan").applied == 45
    time.sleep(0.2)
    assert not any(sensor(m, "range").value == 123 for m in bus.msgs("state", t))
    ev = bus.wait_event("SERIAL_BAD_LINE", since=t, timeout=6)
    assert "bad line" in ev.text
    fake.silent = False
    assert fake.rx_bad == [], "everything roverd sent had a valid checksum"


def test_flag_rising_edge_one_event(rig):
    bus, fake = rig.bus, rig.fake
    bus.wait("state", lambda m: m.mode == "IDLE")
    t = time.monotonic()
    fake.extra_flags = BLOCKED | MOVING
    time.sleep(1.0)
    fake.extra_flags = 0
    time.sleep(0.3)
    blocked = bus.events("BLOCKED", since=t)
    assert len(blocked) == 1 and blocked[0].severity == pb.SEVERITY_WARNING
    assert bus.events("MOVING", since=t) == []
    fake.extra_flags = BLOCKED
    bus.wait("event", lambda e: e.code == "BLOCKED", since=time.monotonic())
    assert len(bus.events("BLOCKED", since=t)) == 2
    fake.extra_flags = 0


def test_firmware_error_event(rig):
    t = time.monotonic()
    rig.fake.send_line("E 7 pan out of range")
    ev = rig.bus.wait_event("FIRMWARE_ERROR", since=t)
    assert ev.text == "7 pan out of range"


def test_source_time(rig_factory):
    # Start near the 32-bit wrap of millis(): it wraps about 1.5 s in.
    rig = rig_factory(fake={"millis_start": 2**32 - 1500})
    bus, fake = rig.bus, rig.fake
    time.sleep(3.0)
    t = time.monotonic()
    fake.reboot()  # millis() starts over
    time.sleep(1.5)
    rx = [r for r in bus.got("state") if r.msg.mode != "NO_LINK"]
    stamps = [r.msg.header.source_time_ns for r in rx]
    assert all(b > a for a, b in zip(stamps, stamps[1:])), "strictly increasing"
    for r in rx:
        lag_ms = (r.wall_ns - r.msg.header.source_time_ns) / 1e6
        assert -2 < lag_ms < 150, lag_ms
    # Consecutive States 50 ms apart in firmware time are about 50 ms apart on the host clock.
    steady = [r.msg.header.source_time_ns for r in rx if r.mono < t - 0.5]
    gaps = [(b - a) / 1e6 for a, b in zip(steady, steady[1:])]
    assert all(30 < g < 80 for g in gaps[5:]), gaps
    assert rx[-1].msg.header.synced is False or os.path.exists("/run/systemd/timesync/synchronized")


def test_serial_reopen(rig):
    bus, fake = rig.bus, rig.fake
    bus.wait("state", lambda m: m.mode == "IDLE")
    t = time.monotonic()
    fake.replace_pty()
    bus.wait_event("SERIAL_LOST", since=t, timeout=3)
    bus.wait_event("SERIAL_OPEN", since=t, timeout=3)
    fake.wait_received("?", since=t)
    bus.wait("state", lambda m: m.mode == "IDLE", since=time.monotonic(), timeout=3)


def test_shutdown_sends_stop(rig_factory):
    rig = rig_factory()
    t = time.monotonic()
    rig.proc.send_signal(signal.SIGTERM)
    assert rig.proc.wait(5) == 0
    rig.fake.wait_received("S", since=t)


# ---- where the adapter is ----

def test_info_says_which_host_and_endpoints(rig):
    info = rig.bus.wait("info")
    assert info.hostname == socket.gethostname()
    assert list(info.endpoints) == [rig.endpoint]          # a concrete listen endpoint is reported as is


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)   # as Zenoh does: TIME_WAIT is not "in use"
        try:
            s.bind(("::", port))
        except OSError:
            return True
    return False


def test_default_port_then_fallback_when_taken(tmp_path):
    """Without ROBO_ZENOH_LISTEN roverd listens on every interface on 7447; a second roverd on the same
    machine warns and listens on a free port instead. The only test that binds the fixed port."""
    if _port_in_use(7447):
        pytest.skip("port 7447 is in use on this machine")
    first = start_roverd(tmp_path, port=7447, listen=False, name="first")
    second = None
    try:
        info = first.bus.wait("info")
        assert info.endpoints and all(e.endswith(":7447") for e in info.endpoints), list(info.endpoints)
        assert "listening on tcp/[::]:7447" in first.log_text()

        ns = "t" + uuid.uuid4().hex[:12]
        fake = FakeFirmware(tmp_path / "second-tty")
        env = dict(os.environ, ROBO_NS=ns, ROBO_ZENOH_MODE="peer", ROBO_ZENOH_SCOUTING="0")
        env.pop("ROBO_ZENOH_LISTEN", None)
        env.pop("ROBO_ZENOH_CONNECT", None)
        log = tmp_path / "second.log"
        with open(log, "wb") as fh:
            proc = subprocess.Popen([str(ROVERD_BIN), "--serial", str(tmp_path / "second-tty")], env=env,
                                    stdout=subprocess.DEVNULL, stderr=fh)
        try:
            deadline = time.monotonic() + 10
            m = None
            while time.monotonic() < deadline and m is None:
                assert proc.poll() is None, log.read_text()
                m = re.search(r"listening on tcp/\[::\]:(\d+)", log.read_text())
                time.sleep(0.05)
            assert m, log.read_text()
            port = int(m.group(1))
            text = log.read_text()
            assert port != 7447 and "cannot listen on tcp/[::]:7447" in text, text
            bus = Bus(f"tcp/127.0.0.1:{port}", ns)
            try:
                info2 = bus.wait("info", timeout=5)
                assert info2.endpoints and all(e.endswith(f":{port}") for e in info2.endpoints)
            finally:
                bus.close()
            assert proc.poll() is None
            proc.send_signal(signal.SIGTERM)
            assert proc.wait(10) == 0
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            fake.close()
    finally:
        assert stop_roverd(first) == 0
