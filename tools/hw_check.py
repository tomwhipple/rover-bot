#!/usr/bin/env python3
"""The on-hardware command-path check of the real rover (`make hw-check`). The drive motors must be
unpowered: the firmware must report LOW_PACK.

Talks to roverd on the default bus namespace as an agent controller and checks, from the messages on
the wire: the adapter's Info and the rest state; take; pan moves and their rate; a range scan across
the pan; that a drive command is refused for low pack; HOME; a Stop from another identity; lease
expiry; State timing and the Pi's clock against this one; the mast camera; and (--restart) a restart
of the roverd service. It ends with HOME and a release, and leaves no publisher behind.

Safety: it refuses to start unless the rover reports LOW_PACK with the pack under 10.5 V, and aborts
(motion zero, Stop, release) the moment a State arrives without LOW_PACK. The only motion it sends is
the low-pack refusal step's, under that guard. The pan stays within +-60 degrees. A human holding
control makes it stop before it sends anything.

Needs robo-control on PYTHONPATH (its root and build/gen/py); the Makefile sets it.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import robo_v1_pb2 as pb
from robo import bus as busmod
from robo.controller import Controller, RobotDescription

REPO = Path(__file__).resolve().parent.parent
LOW_PACK_V = 10.5
PAN_LIMIT = 60.0
PAN_RATE = 62.5  # deg/s, the firmware's
LABEL = "hw check (agent)"


class Abort(Exception):
    pass


def ms(ns: float) -> float:
    return ns / 1e6


@dataclass
class Rx:
    t_ns: int  # local receive time (UTC ns)
    msg: Any


class Recorder:
    """Everything the rover publishes, with local receive times. Bus callbacks land here."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.states: list[Rx] = []
        self.events: list[Rx] = []
        self.controls: list[Rx] = []
        self.info: Rx | None = None
        self.low_pack_lost: pb.State | None = None

    def on_message(self, key: str, msg: Any) -> None:
        topic = busmod.topic_of(key)[1]
        rx = Rx(time.time_ns(), msg)
        with self.lock:
            if topic == "state":
                self.states.append(rx)
                if msg.mode != "NO_LINK" and ("LOW_PACK" not in msg.flags or msg.pack_v >= LOW_PACK_V):
                    self.low_pack_lost = self.low_pack_lost or msg
            elif topic == "event":
                self.events.append(rx)
            elif topic == "control/state":
                self.controls.append(rx)
            elif topic == "info":
                self.info = rx

    def state(self) -> Rx | None:
        with self.lock:
            return self.states[-1] if self.states else None

    def control(self) -> Any:
        with self.lock:
            return self.controls[-1].msg if self.controls else None

    def states_since(self, t_ns: int) -> list[Rx]:
        with self.lock:
            return [r for r in self.states if r.t_ns >= t_ns]

    def events_since(self, t_ns: int) -> list[Rx]:
        with self.lock:
            return [r for r in self.events if r.t_ns >= t_ns]

    def wait(self, pred: Callable[[], Any], timeout: float, what: str) -> Any:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            self.check_safe()
            v = pred()
            if v:
                return v
            time.sleep(0.01)
        raise TimeoutError(f"timed out after {timeout:.1f} s waiting for {what}")

    def wait_event(self, code: str, since: int, timeout: float) -> Rx:
        def find() -> Rx | None:
            return next((r for r in self.events_since(since) if r.msg.code == code), None)
        return self.wait(find, timeout, f"event {code}")

    def check_safe(self) -> None:
        if self.low_pack_lost is not None:
            s = self.low_pack_lost
            raise Abort(f"LOW_PACK cleared: mode {s.mode}, flags {list(s.flags)}, pack {s.pack_v:.2f} V")


def actuator(s: pb.State, name: str) -> Any:
    return next((a for a in s.actuators if a.name == name), None)


def sensor(s: pb.State, name: str) -> Any:
    return next((x for x in s.sensors if x.name == name), None)


def pan_of(s: pb.State) -> float:
    a = actuator(s, "pan")
    return a.applied if a else float("nan")


def ev_str(r: Rx) -> str:
    return f"{r.msg.code}: {r.msg.text}"


@dataclass
class Result:
    num: int
    name: str
    status: str = "FAIL"
    evidence: list[str] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)

    def note(self, line: str) -> None:
        self.evidence.append(line)
        print(f"    {line}", flush=True)


class Check:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.desc = RobotDescription.load(REPO / "robo" / "robot.json")
        self.rec = Recorder()
        self.bus = busmod.Bus()
        self.bus.subscribe(busmod.robot_key(self.desc.id, "**"), self.rec.on_message)
        self.ctl = Controller(self.bus, self.desc, role=pb.ROLE_AGENT, label=LABEL)
        self.results: list[Result] = []
        self._ticking = threading.Event()
        self._quit = threading.Event()
        self._ticker = threading.Thread(target=self._tick_loop, name="intent", daemon=True)
        self._ticker.start()

    # -- the controller's 20 Hz intent stream -------------------------------------------------------
    def _tick_loop(self) -> None:
        period = 1.0 / 20
        nxt = time.monotonic()
        while not self._quit.is_set():
            if self.rec.low_pack_lost is not None:
                self._zero_motion()
            if self._ticking.is_set():
                self.ctl.tick()
            nxt += period
            time.sleep(max(0.0, nxt - time.monotonic()))

    def _zero_motion(self) -> None:
        self.ctl.set_motion_engaged(False)
        for c in self.desc.motion_channels:
            self.ctl.set_motion(c.name, 0.0)

    def result(self, num: int, name: str) -> Result:
        print(f"\n[{num}] {name}", flush=True)
        r = Result(num, name)
        self.results.append(r)
        return r

    # -- helpers ------------------------------------------------------------------------------------
    def fresh_state(self) -> pb.State:
        rx = self.rec.wait(lambda: (s := self.rec.state()) and time.time_ns() - s.t_ns < 500e6 and s,
                           3, "a fresh State")
        return rx.msg

    def take(self) -> None:
        c = self.rec.control()
        if c and c.holder_id and c.holder_id != self.ctl.id and c.holder_role == pb.ROLE_HUMAN:
            raise Abort(f"a human holds control ({c.holder_label}); not taking it")
        self.ctl.take()
        self._ticking.set()
        self.rec.wait(lambda: (c := self.rec.control()) and c.holder_id == self.ctl.id and not c.stopped,
                      3, "ControlState naming this controller as holder")

    def release(self) -> None:
        self._zero_motion()
        self.ctl.release()
        self._ticking.clear()
        self.rec.wait(lambda: (c := self.rec.control()) and not c.holder_id, 3, "ControlState with no holder")

    def move_pan(self, target: float, timeout: float = 4.0) -> tuple[float, float]:
        """Pan to target; returns (seconds from leaving the start to arriving, by State source times;
        seconds from the command to arrival, by this clock)."""
        if abs(target) > PAN_LIMIT:
            raise ValueError(f"pan {target} beyond +-{PAN_LIMIT}")
        start = pan_of(self.fresh_state())
        t_cmd = time.time_ns()
        self.ctl.set_position("pan", target)
        arrived = self.rec.wait(
            lambda: next((r for r in self.rec.states_since(t_cmd) if abs(pan_of(r.msg) - target) < 0.5), None),
            timeout + abs(target - start) / PAN_RATE, f"pan at {target}")
        states = self.rec.states_since(t_cmd)
        left = next((r for r in states if abs(pan_of(r.msg) - start) >= 0.5), arrived)
        # The last State still at the start bounds the departure from below.
        before = [r for r in states if r.t_ns < left.t_ns and abs(pan_of(r.msg) - start) < 0.5]
        t0 = before[-1].msg.header.source_time_ns if before else left.msg.header.source_time_ns
        return ((arrived.msg.header.source_time_ns - t0) / 1e9, (arrived.t_ns - t_cmd) / 1e9)

    # -- the checks ---------------------------------------------------------------------------------
    def check_rest(self) -> None:
        r = self.result(1, "Info and the rest state")
        info = self.rec.wait(lambda: self.rec.info, 5, "Info").msg
        s = self.fresh_state()
        r.note(f"Info: adapter {info.adapter_version!r}, firmware {info.firmware_version!r}, "
               f"description_commit {info.description_commit!r}, hostname {info.hostname!r}, "
               f"endpoints {list(info.endpoints)}, cameras {dict(info.cameras)}, simulated {info.simulated}")
        r.note(f"State: mode {s.mode}, flags {list(s.flags)}, pack {s.pack_v:.2f} V, pan {pan_of(s):g}, "
               f"wheels applied {actuator(s, 'left_wheel').applied:g}/{actuator(s, 'right_wheel').applied:g}, "
               f"at_home {s.at_home}")
        r.data = {"info": str(info).replace("\n", " "), "pan": pan_of(s), "at_home": s.at_home}
        ok = bool(info.description_commit) and not info.simulated
        if abs(pan_of(s)) < 0.5 and s.at_home is False:
            ok = False
            r.note("pan 0 and motors 0 but at_home false")
        elif abs(pan_of(s)) >= 0.5:
            r.note("(the pan is not at home at the start; at_home is checked again after HOME)")
        r.status = "PASS" if ok else "FAIL"

    def check_take(self) -> None:
        r = self.result(2, "take control as an agent")
        t0 = time.time_ns()
        self.take()
        c = self.rec.control()
        ev = self.rec.wait_event("CONTROL_TAKEN", t0, 3)
        r.note(f"ControlState: holder {c.holder_id!r} role {c.holder_role} label {c.holder_label!r} "
               f"stopped {c.stopped}")
        r.note(f"Event {ev_str(ev)}")
        r.status = "PASS" if c.holder_id == self.ctl.id and c.holder_role == pb.ROLE_AGENT else "FAIL"

    def check_pan(self) -> None:
        r = self.result(3, "pan +30, -30, 0")
        ok = True
        for target in (30.0, -30.0, 0.0):
            t_src, t_wall = self.move_pan(target)
            s = self.fresh_state()
            a = actuator(s, "pan")
            r.note(f"pan -> {target:+g}: commanded {a.commanded:g}, applied {a.applied:g}; "
                   f"moved in {t_src:.3f} s by State times, {t_wall:.3f} s from command to arrival here")
            r.data[f"{target:+g}"] = {"src_s": t_src, "wall_s": t_wall}
            ok = ok and abs(a.applied - target) < 0.5 and abs(a.commanded - target) < 0.5
        move = r.data["-30"]["src_s"]
        r.note(f"the 60 degree move (+30 -> -30): {move:.3f} s, {60 / move:.1f} deg/s "
               f"(firmware: {PAN_RATE} deg/s, {60 / PAN_RATE:.3f} s; State every 50 ms)")
        ok = ok and abs(60 / move - PAN_RATE) < 0.15 * PAN_RATE
        r.status = "PASS" if ok else "FAIL"

    def check_scan(self) -> None:
        r = self.result(4, "range across the pan, -60..+60 in 15 degree steps")
        rows: list[dict[str, Any]] = []
        for target in (-15.0, -30.0, -45.0):  # out to the first step, 15 degrees at a time
            self.move_pan(target)
        for target in (float(d) for d in range(-60, 61, 15)):
            self.move_pan(target)
            time.sleep(0.15)
            t0 = time.time_ns()
            time.sleep(self.args.dwell)
            samples = [sensor(x.msg, "range") for x in self.rec.states_since(t0)]
            vals = [x.value for x in samples if x and x.valid]
            rows.append({"pan": target, "n": len(samples), "valid": len(vals),
                         "median_cm": statistics.median(vals) if vals else None,
                         "min_cm": min(vals) if vals else None, "max_cm": max(vals) if vals else None})
        r.note("pan_deg  range_cm(median)  min  max  valid/n")
        for row in rows:
            med = "-" if row["median_cm"] is None else f"{row['median_cm']:.0f}"
            mn = "-" if row["min_cm"] is None else f"{row['min_cm']:.0f}"
            mx = "-" if row["max_cm"] is None else f"{row['max_cm']:.0f}"
            r.note(f"{row['pan']:+6.0f}   {med:>8}  {mn:>4} {mx:>4}  {row['valid']}/{row['n']}")
        r.data["scan"] = rows
        for target in (45.0, 30.0, 15.0, 0.0):
            self.move_pan(target)
        r.status = "PASS" if len(rows) == 9 and all(row["n"] > 0 for row in rows) else "FAIL"

    def check_refusal(self) -> None:
        """Forward 0.3, then reverse 0.3, 2 s each. Forward inside the stand-off is refused as blocked
        first (the firmware reports one refusal code, blocked before low pack); reverse is not subject to
        the stand-off, so only the low-pack limit can refuse it."""
        r = self.result(5, "drive refused for low pack (linear +0.3, then -0.3, 2 s each)")
        ok = True
        for linear in (0.3, -0.3):
            s = self.fresh_state()
            guard = (s.mode == "LOW_PACK" and "LOW_PACK" in s.flags and s.pack_v < LOW_PACK_V)
            r.note(f"linear {linear:+g}: precondition: mode {s.mode}, flags {list(s.flags)}, pack {s.pack_v:.2f} V "
                   f"-> {'holds' if guard else 'DOES NOT HOLD'}")
            if not guard:
                r.status = "SKIP"
                raise Abort("the low-pack precondition does not hold: no motion is sent")
            seq0 = s.last_intent_seq
            t0 = time.time_ns()
            try:
                self.ctl.set_motion("linear", linear)
                self.ctl.set_motion_engaged(True)
                end = time.monotonic() + 2.0
                while time.monotonic() < end:
                    self.rec.check_safe()
                    for x in self.rec.states_since(t0):
                        for w in ("left_wheel", "right_wheel"):
                            if actuator(x.msg, w).applied != 0:
                                raise Abort(f"{w} applied {actuator(x.msg, w).applied} during the refusal step")
                    time.sleep(0.02)
            finally:
                self._zero_motion()
            time.sleep(0.4)
            states = self.rec.states_since(t0)
            cmd = sorted({(actuator(x.msg, "left_wheel").commanded, actuator(x.msg, "right_wheel").commanded)
                          for x in states})
            app = sorted({(actuator(x.msg, "left_wheel").applied, actuator(x.msg, "right_wheel").applied)
                          for x in states})
            modes = sorted({x.msg.mode for x in states})
            flags = sorted({f for x in states for f in x.msg.flags})
            seqs = [x.msg.last_intent_seq for x in states]
            evs = [ev_str(e) for e in self.rec.events_since(t0)]
            r.note(f"  {len(states)} States: commanded (left, right) {cmd}; applied {app}; modes {modes}; flags "
                   f"{flags}; last_intent_seq {seq0} -> {seqs[-1] if seqs else '-'}")
            r.note("  events: " + ("; ".join(evs) or "none"))
            r.data[f"{linear:+g}"] = {"commanded": cmd, "applied": app, "modes": modes, "flags": flags,
                                      "events": evs}
            pwm = linear * 150
            asked = any(abs(c[0] - pwm) < 1 and abs(c[1] - pwm) < 1 for c in cmd)
            ok = ok and app == [(0.0, 0.0)] and modes == ["LOW_PACK"] and asked
            if linear < 0:
                ok = ok and any(e.startswith("FIRMWARE_ERROR: 4") for e in evs)
        r.status = "PASS" if ok else "FAIL"

    def check_home(self) -> None:
        r = self.result(6, "HOME from pan +40")
        self.move_pan(15.0)
        self.move_pan(40.0)
        t0 = time.time_ns()
        assert self.ctl.home()
        home = self.rec.wait_event("HOME", t0, 3)
        homed = self.rec.wait_event("HOMED", t0, 4)
        s = self.rec.wait(lambda: next((x for x in self.rec.states_since(homed.t_ns) if x.msg.at_home), None),
                          2, "a State with at_home").msg
        dt = (homed.msg.header.source_time_ns - home.msg.header.source_time_ns) / 1e9
        r.note(f"events: {ev_str(home)}; {ev_str(homed)}")
        r.note(f"HOME -> HOMED {dt:.3f} s by event times ({(homed.t_ns - t0) / 1e9:.3f} s from sending Home); "
               f"then pan {pan_of(s):g}, at_home {s.at_home} (40 degrees at {PAN_RATE} deg/s = {40 / PAN_RATE:.2f} s)")
        r.data = {"home_to_homed_s": dt}
        r.status = "PASS" if s.at_home and abs(pan_of(s)) < 0.5 else "FAIL"

    def check_stop(self) -> None:
        r = self.result(7, "Stop from another (non-holder) identity")
        other = Controller(self.bus, self.desc, role=pb.ROLE_AGENT, label="hw check stop (agent)", subscribe=False)
        seq0 = self.fresh_state().last_intent_seq
        sent0 = self.ctl.intents_sent
        t0 = time.time_ns()
        other.stop("hw check: stop from a non-holder", wait=True)
        ev = self.rec.wait_event("STOP", t0, 3)
        c = self.rec.wait(lambda: (c := self.rec.control()) and c.stopped and c, 3, "ControlState.stopped")
        r.note(f"Stop sender {other.id}; Event {ev_str(ev)}")
        r.note(f"ControlState: stopped {c.stopped}, holder {c.holder_id!r} (still this controller: "
               f"{c.holder_id == self.ctl.id})")
        time.sleep(1.5)
        seqs = {x.msg.last_intent_seq for x in self.rec.states_since(ev.t_ns + 200_000_000)}
        sent = self.ctl.intents_sent - sent0
        r.note(f"over 1.5 s this controller sent {sent} more Intents; State.last_intent_seq: before the Stop "
               f"{seq0}, after it {sorted(seqs)}")
        r.data = {"seq_before": seq0, "seqs_after": sorted(seqs), "intents_sent_after": sent}
        r.status = "PASS" if c.stopped and len(seqs) == 1 and sent > 20 else "FAIL"

    def check_expiry(self) -> None:
        r = self.result(8, "lease expiry: no Intent for 4 s")
        self._ticking.clear()
        time.sleep(0.06)
        t_last = time.time_ns()
        ev = self.rec.wait_event("CONTROL_EXPIRED", t_last, 4.5)
        c = self.rec.wait(lambda: (c := self.rec.control()) and not c.holder_id and c, 2, "a free ControlState")
        r.note(f"Event {ev_str(ev)} {(ev.t_ns - t_last) / 1e9:.2f} s after the last Intent")
        r.note(f"ControlState: holder {c.holder_id!r}, stopped {c.stopped}")
        time.sleep(max(0.0, 4.0 - (time.time_ns() - t_last) / 1e9))
        r.status = "PASS" if not c.holder_id else "FAIL"

    def home_and_release(self) -> None:
        """Take (clears a latched stop), HOME, wait for HOMED (or at_home), release."""
        print("\n[-] HOME and release", flush=True)
        t0 = time.time_ns()
        self.take()
        if self.ctl.home():
            try:
                self.rec.wait_event("HOMED", t0, 4)
            except TimeoutError:
                pass
        s = self.rec.wait(lambda: next((x for x in self.rec.states_since(t0) if x.msg.at_home), None),
                          4, "at_home").msg
        self.release()
        c = self.rec.control()
        print(f"    pan {pan_of(s):g}, at_home {s.at_home}; then released: ControlState holder {c.holder_id!r}",
              flush=True)

    def check_timing(self) -> None:
        r = self.result(9, "timing and clocks")
        with self.rec.lock:
            states = list(self.rec.states)
        # The longest run without a gap over 0.5 s (a restart makes one).
        runs, cur = [], [states[0]]
        for a, b in zip(states, states[1:]):
            if b.t_ns - a.t_ns > 500e6:
                runs.append(cur)
                cur = []
            cur.append(b)
        runs.append(cur)
        run = max(runs, key=len)
        span = (run[-1].msg.header.source_time_ns - run[0].msg.header.source_time_ns) / 1e9
        rate = (len(run) - 1) / span if span > 0 else float("nan")
        gaps = [ms(b.t_ns - a.t_ns) for a, b in zip(run, run[1:])]
        src_gaps = [ms(b.msg.header.source_time_ns - a.msg.header.source_time_ns) for a, b in zip(run, run[1:])]
        loops = [x.msg.loop_time_ms for x in run]
        ages = [x.msg.firmware_link_age_ms for x in run]
        offs = [ms(x.msg.header.source_time_ns - x.t_ns) for x in run]
        misses = max(x.msg.deadline_misses for x in run)
        synced = {x.msg.header.synced for x in run}

        def q(v: list[float], p: float) -> float:
            return sorted(v)[min(len(v) - 1, int(p * len(v)))]
        r.note(f"State rate {rate:.2f} Hz over {span:.1f} s ({len(run)} States); arrival gaps here: median "
               f"{statistics.median(gaps):.1f} ms, p95 {q(gaps, 0.95):.1f}, max {max(gaps):.1f}; by source "
               f"time: median {statistics.median(src_gaps):.1f} ms, max {max(src_gaps):.1f}")
        r.note(f"firmware loop time: median {statistics.median(loops):.3f} ms, max {max(loops):.3f} ms; "
               f"deadline_misses max {misses}")
        r.note(f"firmware_link_age_ms: median {statistics.median(ages):.1f}, max {max(ages):.1f}")
        r.note(f"source_time - receive time here: median {statistics.median(offs):+.1f} ms "
               f"(min {min(offs):+.1f}, max {max(offs):+.1f}); Header.synced {sorted(synced)}; this host "
               f"reports synced {busmod.clock_synced()} (the offset holds this host's clock error as well as the "
               f"latency)")
        packs = [x.msg.pack_v for x in run]
        r.note(f"pack_v: median {statistics.median(packs):.2f} V, min {min(packs):.2f}, max {max(packs):.2f}, "
               f"{len(set(packs))} distinct values")
        r.data = {"rate_hz": rate, "loop_ms_max": max(loops), "deadline_misses": misses,
                  "link_age_ms_median": statistics.median(ages), "offset_ms_median": statistics.median(offs),
                  "offset_ms_min": min(offs), "offset_ms_max": max(offs), "synced": sorted(synced)}
        r.status = "PASS" if 19.0 <= rate <= 21.0 and synced == {True} else "FAIL"

    def check_camera(self) -> None:
        r = self.result(10, "mast camera")
        info = self.rec.info.msg if self.rec.info else None
        url = dict(info.cameras).get("mast") if info else None
        if not url:
            r.note("no mast camera in Info")
            return
        frames: list[tuple[float, dict[str, str], bytes]] = []
        with urllib.request.urlopen(url, timeout=5) as resp:
            ctype = resp.headers.get("Content-Type", "")
            boundary = ctype.split("boundary=")[-1].strip()
            r.note(f"GET {url}: {resp.status}, Content-Type {ctype!r}")
            end = time.monotonic() + self.args.camera_s
            while time.monotonic() < end:
                line = resp.readline()
                if not line:
                    break
                if line.strip() != b"--" + boundary.encode():
                    continue
                headers: dict[str, str] = {}
                while (h := resp.readline().strip()):
                    k, _, v = h.decode("latin-1").partition(":")
                    headers[k.strip()] = v.strip()
                body = resp.read(int(headers["Content-Length"]))
                frames.append((time.time(), headers, body))
        from io import BytesIO
        from PIL import Image
        sizes = set()
        for _, _, body in frames:
            with Image.open(BytesIO(body)) as im:
                im.load()
                sizes.add(im.size)
        span = frames[-1][0] - frames[0][0] if len(frames) > 1 else 0
        stamps = [float(h["X-Timestamp"]) for _, h, _ in frames if "X-Timestamp" in h]
        lag = [t - float(h["X-Timestamp"]) for t, h, _ in frames if "X-Timestamp" in h]
        r.note(f"{len(frames)} frames decoded, sizes {sorted(sizes)}, {(len(frames) - 1) / span:.1f} fps here")
        r.note(f"part headers: {sorted(frames[0][1])}; example {frames[0][1]}")
        if len(stamps) > 1:
            r.note(f"X-Timestamp (s, UTC) on every part: {len(stamps) == len(frames)}; by it "
                   f"{(len(stamps) - 1) / (stamps[-1] - stamps[0]):.1f} fps; arrival here - X-Timestamp median "
                   f"{statistics.median(lag) * 1e3:+.0f} ms")
        out = Path(self.args.frame)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(frames[len(frames) // 2][2])
        r.note(f"saved {out}")
        r.data = {"frames": len(frames), "sizes": sorted(sizes), "headers": sorted(frames[0][1])}
        r.status = "PASS" if frames and sizes else "FAIL"

    def check_restart(self) -> None:
        r = self.result(11, "restart roverd with a subscriber running")
        self.take()
        self.move_pan(20.0)
        self.release()
        time.sleep(0.5)
        s0 = self.fresh_state()
        pan0, seq0 = pan_of(s0), s0.header.seq
        t0 = time.time_ns()
        cmd = ["ssh", self.args.pi_host, "sudo systemctl restart roverd"]
        r.note(f"pan before {pan0:g}; running: {' '.join(cmd)}")
        subprocess.run(cmd, check=True, timeout=30)
        # The new process numbers its States from 1 again.
        first = self.rec.wait(lambda: next((x for x in self.rec.states_since(t0) if x.msg.header.seq < seq0), None),
                              30, "a State from the new roverd")
        linked = self.rec.wait(lambda: next((x for x in self.rec.states_since(first.t_ns)
                                             if x.msg.mode != "NO_LINK"), None), 10, "a linked State")
        time.sleep(2.5)
        before = [x for x in self.rec.states_since(t0 - 1_000_000_000) if x.msg.header.seq >= seq0]
        after = self.rec.states_since(first.t_ns)
        gap = (first.t_ns - before[-1].t_ns) / 1e9
        pans = sorted({pan_of(x.msg) for x in before + after if x.msg.mode != "NO_LINK"})
        no_link = sum(x.msg.mode == "NO_LINK" for x in after)
        evs = self.rec.events_since(t0 - 200_000_000)
        r.note(f"State gap {gap:.2f} s: last from the old roverd at +{(before[-1].t_ns - t0) / 1e9:.2f} s, first "
               f"from the new at +{(first.t_ns - t0) / 1e9:.2f} s (mode {first.msg.mode}), first linked at "
               f"+{(linked.t_ns - t0) / 1e9:.2f} s from the restart command; NO_LINK States after: {no_link}")
        r.note(f"pan in linked States across the restart: {pans}")
        r.note("events: " + "; ".join(f"+{(e.t_ns - t0) / 1e9:.2f} s {ev_str(e)}" for e in evs))
        codes = [e.msg.code for e in evs]
        r.data = {"gap_s": gap, "pans": pans, "events": [ev_str(e) for e in evs]}
        r.status = "PASS" if pans == [pan0] and "READY" in codes else "FAIL"
        self.home_and_release()

    # -- the run ------------------------------------------------------------------------------------
    def run(self) -> int:
        print(f"hw-check: namespace {busmod.namespace()}, robot {self.desc.id}, controller {self.ctl.id} ({LABEL})")
        s = self.fresh_state()
        print(f"precondition: mode {s.mode}, flags {list(s.flags)}, pack {s.pack_v:.2f} V")
        if not (s.mode == "LOW_PACK" and "LOW_PACK" in s.flags and s.pack_v < LOW_PACK_V):
            print("hw-check: the rover does not report LOW_PACK: the drive motors may be powered. Nothing sent.")
            return 2
        self.rec.wait(lambda: self.rec.control(), 2, "ControlState")
        c = self.rec.control()
        if c.holder_id:
            print(f"hw-check: control is held by {c.holder_label!r} ({c.holder_id}); not taking it. Nothing sent.")
            return 2
        failed: str | None = None
        try:
            self.check_rest()
            self.check_take()
            self.check_pan()
            self.check_scan()
            self.check_refusal()
            self.check_home()
            self.check_stop()
            self.check_expiry()
            self.home_and_release()
            self.check_camera()
            if self.args.restart:
                self.check_restart()
            self.check_timing()
        except Abort as e:
            failed = f"ABORTED: {e}"
        except Exception as e:  # any failure ends the run the safe way
            failed = f"ERROR: {type(e).__name__}: {e}"
        finally:
            if failed:
                print(f"\nhw-check {failed}", flush=True)
                self._zero_motion()
                self.ctl.stop("hw check aborted", wait=True)
                if self.ctl.holds_control:
                    self.ctl.release()
                self._ticking.clear()
            self._quit.set()
            self._ticker.join(1)
            time.sleep(0.3)
            c = self.rec.control()
            print(f"\nfinal ControlState: holder {c.holder_id!r}, stopped {c.stopped}" if c else "")
            self.bus.close()
        print("\nsummary")
        for r in self.results:
            print(f"  [{r.num:2}] {r.status:4}  {r.name}")
        if failed:
            print(f"  {failed}")
        if self.args.json:
            Path(self.args.json).parent.mkdir(parents=True, exist_ok=True)
            Path(self.args.json).write_text(json.dumps(
                {"controller": self.ctl.id, "failed": failed,
                 "results": [r.__dict__ for r in self.results]}, indent=2, default=str))
        return 1 if failed or any(r.status == "FAIL" for r in self.results) else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--restart", action="store_true", help="also restart the roverd service (ssh PI_HOST, sudo)")
    ap.add_argument("--pi-host", default=os.environ.get("PI_HOST", "rover.local"))
    ap.add_argument("--dwell", type=float, default=0.8, help="seconds of range samples per scan step")
    ap.add_argument("--camera-s", type=float, default=3.0, help="seconds of camera stream to read")
    ap.add_argument("--frame", default=str(REPO / "build" / "hw-check" / "mast.jpg"), help="where to save a frame")
    ap.add_argument("--json", default=str(REPO / "build" / "hw-check" / "result.json"))
    args = ap.parse_args()
    if busmod.namespace() != "robo":
        print(f"hw-check: ROBO_NS={busmod.namespace()}: the real rover is on the default namespace, robo")
        return 2
    return Check(args).run()


if __name__ == "__main__":
    sys.exit(main())
