"""Runs the real roverd against the fake firmware on a pty and a loopback Zenoh session.

ROVERD_BIN points at the binary (default: build/roverd/roverd in the repo); robo_v1_pb2 must be
importable (mk/roverd.mk generates it and sets PYTHONPATH).
"""
from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import IO

import pytest
import zenoh

import robo_v1_pb2 as pb

sys.path.insert(0, str(Path(__file__).parent))
from fake_firmware import FakeFirmware  # noqa: E402

REPO = Path(__file__).resolve().parents[3]
ROVERD_BIN = Path(os.environ.get("ROVERD_BIN", REPO / "build/roverd/roverd"))

TYPES = {
    "info": pb.Info,
    "state": pb.State,
    "event": pb.Event,
    "control/state": pb.ControlState,
    "control/request": pb.ControlRequest,
    "cmd/intent": pb.Intent,
    "cmd/stop": pb.Stop,
    "cmd/home": pb.Home,
}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_port(port: int, proc: subprocess.Popen, timeout: float = 10.0) -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if proc.poll() is not None:
            raise AssertionError(f"roverd exited with {proc.returncode}")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.05)
    raise AssertionError(f"roverd never listened on {port}")


@dataclass
class Rx:
    mono: float      # time.monotonic() at receipt
    wall_ns: int     # time.time_ns() at receipt
    msg: object


class Bus:
    """A controller-side session: records everything under robot/<id>/ and publishes requests."""

    def __init__(self, endpoint: str, ns: str, robot: str = "rover") -> None:
        cfg = zenoh.Config()
        cfg.insert_json5("mode", '"peer"')
        cfg.insert_json5("connect/endpoints", json.dumps([endpoint]))
        cfg.insert_json5("scouting/multicast/enabled", "false")
        cfg.insert_json5("scouting/gossip/enabled", "false")
        self.prefix = f"{ns}/robot/{robot}/"
        self.session = zenoh.open(cfg)
        self.lock = threading.Lock()
        self.rx: dict[str, list[Rx]] = defaultdict(list)
        self._seq: dict[str, int] = defaultdict(int)
        self._sub = self.session.declare_subscriber(self.prefix + "**", self._on_sample)

    def _on_sample(self, sample: zenoh.Sample) -> None:
        topic = str(sample.key_expr)[len(self.prefix):]
        cls = TYPES.get(topic)
        if cls is None:
            return
        msg = cls()
        msg.ParseFromString(sample.payload.to_bytes())
        with self.lock:
            self.rx[topic].append(Rx(time.monotonic(), time.time_ns(), msg))

    def close(self) -> None:
        self.session.close()

    # ---- receive ----

    def got(self, topic: str, since: float = 0.0) -> list[Rx]:
        with self.lock:
            return [r for r in self.rx[topic] if r.mono >= since]

    def msgs(self, topic: str, since: float = 0.0) -> list:
        return [r.msg for r in self.got(topic, since)]

    def events(self, code: str | None = None, since: float = 0.0) -> list:
        return [e for e in self.msgs("event", since) if code is None or e.code == code]

    def wait(self, topic: str, pred=lambda m: True, since: float = 0.0, timeout: float = 3.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            for r in self.got(topic, since):
                if pred(r.msg):
                    return r.msg
            time.sleep(0.01)
        raise AssertionError(f"no {topic} matching within {timeout} s; last: {self.msgs(topic, since)[-3:]}")

    def wait_event(self, code: str, since: float = 0.0, timeout: float = 3.0):
        return self.wait("event", lambda e: e.code == code, since, timeout)

    # ---- send ----

    def _header(self, topic: str) -> pb.Header:
        self._seq[topic] += 1
        return pb.Header(source_time_ns=time.time_ns(), seq=self._seq[topic])

    def put(self, topic: str, msg) -> None:
        self.session.put(self.prefix + topic, msg.SerializeToString())

    def request(self, cid: str, role: int, action: int, label: str = "") -> None:
        self.put("control/request", pb.ControlRequest(
            header=self._header("control/request"), controller_id=cid, role=role, action=action, label=label))

    def take(self, cid: str, role: int = pb.ROLE_HUMAN, label: str = "") -> None:
        self.request(cid, role, pb.ControlRequest.ACTION_TAKE, label)

    def release(self, cid: str, role: int = pb.ROLE_HUMAN) -> None:
        self.request(cid, role, pb.ControlRequest.ACTION_RELEASE)

    def intent(self, cid: str, seq: int | None = None, **channels: float) -> None:
        h = self._header("cmd/intent")
        if seq is not None:
            h.seq = seq
        self.put("cmd/intent", pb.Intent(header=h, controller_id=cid, channels=channels))

    def stop(self, sender: str, reason: str = "") -> None:
        self.put("cmd/stop", pb.Stop(header=self._header("cmd/stop"), sender_id=sender, reason=reason))

    def home(self, cid: str) -> None:
        self.put("cmd/home", pb.Home(header=self._header("cmd/home"), controller_id=cid))

    def take_and_wait(self, cid: str, role: int = pb.ROLE_HUMAN, label: str = "") -> None:
        t = time.monotonic()
        self.take(cid, role, label)
        self.wait("control/state", lambda m: m.holder_id == cid and not m.stopped, since=t)


@dataclass
class Rig:
    fake: FakeFirmware
    bus: Bus
    proc: subprocess.Popen
    log: Path
    log_fh: IO[bytes]
    started: float
    endpoint: str

    def log_text(self) -> str:
        return self.log.read_text(errors="replace")


def start_roverd(tmp_path: Path, fake_kwargs: dict | None = None, args: list[str] | None = None,
                 port: int | None = None, listen: bool = True, name: str = "roverd"):
    """listen=False leaves ROBO_ZENOH_LISTEN unset (the adapter's default); then `port` is where it is
    expected to listen."""
    ns = "t" + uuid.uuid4().hex[:12]
    port = port or free_port()
    fake = FakeFirmware(tmp_path / f"{name}-tty", **(fake_kwargs or {}))
    env = dict(os.environ, ROBO_NS=ns, ROBO_ZENOH_MODE="peer", ROBO_ZENOH_SCOUTING="0")
    if listen:
        env["ROBO_ZENOH_LISTEN"] = f"tcp/127.0.0.1:{port}"
    else:
        env.pop("ROBO_ZENOH_LISTEN", None)
    env.pop("ROBO_ZENOH_CONNECT", None)
    log = tmp_path / f"{name}.log"
    cmd = [str(ROVERD_BIN), "--serial", str(tmp_path / f"{name}-tty"),
           "--camera", "mast=http://127.0.0.1:8081/stream", "--camera", "overview=http://127.0.0.1:8082/x",
           "--simulated", "--description-commit", "abc123"]
    started = time.monotonic()
    log_fh = open(log, "wb")
    proc = subprocess.Popen(cmd + (args or []), env=env, stdout=subprocess.DEVNULL, stderr=log_fh)
    try:
        wait_port(port, proc)
    except AssertionError:
        fake.close()
        log_fh.close()
        raise
    bus = Bus(f"tcp/127.0.0.1:{port}", ns)
    rig = Rig(fake, bus, proc, log, log_fh, started, f"tcp/127.0.0.1:{port}")
    try:
        bus.wait("control/state", timeout=5)
    except AssertionError:
        stop_roverd(rig)
        raise
    return rig


def stop_roverd(rig: Rig) -> int:
    if rig.proc.poll() is None:
        rig.proc.send_signal(signal.SIGTERM)
    try:
        rc = rig.proc.wait(10)
    except subprocess.TimeoutExpired:
        rig.proc.kill()
        rc = rig.proc.wait()
    rig.bus.close()
    rig.fake.close()
    rig.log_fh.close()
    return rc


@pytest.fixture
def rig_factory(tmp_path):
    rigs: list[Rig] = []

    def make(**kw) -> Rig:
        rig = start_roverd(tmp_path, kw.get("fake"), kw.get("args"))
        rigs.append(rig)
        return rig

    yield make
    for rig in rigs:
        rc = stop_roverd(rig)
        text = rig.log_text()
        assert "AddressSanitizer" not in text and "runtime error:" not in text, text[-4000:]
        assert rc == 0, f"roverd exited {rc}\n{text[-4000:]}"


@pytest.fixture
def rig(rig_factory) -> Rig:
    return rig_factory()
