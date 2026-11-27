import http.client
import io
import json
import os
import pathlib
import re
import subprocess
import sys
import threading
import time

import pytest

HERE = pathlib.Path(__file__).resolve().parent
SIM = HERE.parent
REPO = SIM.parent
TWIN = SIM / "rover_twin.py"


def core_lib(tmp_factory):
    env = os.environ.get("ROVER_CORE_LIB")
    if env:
        return env
    for name in ("librover_core.dylib", "librover_core.so"):
        p = REPO / "build" / name
        if p.exists():
            return str(p)
    out = tmp_factory.mktemp("stub") / "librover_core.so"
    subprocess.run(["c++", "-std=c++17", "-shared", "-fPIC", "-o", str(out),
                    str(HERE / "stub" / "rover_core_stub.cpp")], check=True)
    return str(out)


@pytest.fixture(scope="session")
def lib(tmp_path_factory):
    return core_lib(tmp_path_factory)


def checksum(payload):
    x = 0
    for b in payload.encode():
        x ^= b
    return "%02X" % x


def frame(payload):
    return ("$%s*%s\n" % (payload, checksum(payload))).encode()


class Twin:
    def __init__(self, lib, tmp_path, *extra, render=False, env=None):
        self.pose_file = tmp_path / "pose.json"
        cmd = [sys.executable, str(TWIN), "--lib", lib, "--dump-pose-file", str(self.pose_file), *extra]
        if not render:
            cmd.append("--no-render")
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                     env={**os.environ, **(env or {})})
        line = self.proc.stdout.readline()
        m = re.match(r"READY pty=(\S+) http=(\d+)", line)
        if not m:
            self.proc.kill()
            raise RuntimeError("twin did not start: %r %s" % (line, self.proc.stderr.read()))
        self.pty, self.http_port = m.group(1), int(m.group(2))
        self.fd = os.open(self.pty, os.O_RDWR | os.O_NOCTTY)
        self.lines = []  # (monotonic, payload, checksum_ok)
        self.lock = threading.Lock()
        self.alive = True
        self.t = threading.Thread(target=self._read, daemon=True)
        self.t.start()
        self.seq = 0

    def _read(self):
        buf = b""
        while self.alive:
            try:
                d = os.read(self.fd, 4096)
            except OSError:
                return
            now = time.monotonic()
            buf += d
            while b"\n" in buf:
                ln, buf = buf.split(b"\n", 1)
                m = re.fullmatch(rb"\$(.*)\*([0-9A-F]{2})", ln.strip())
                if m:
                    ok = checksum(m.group(1).decode()) == m.group(2).decode()
                    with self.lock:
                        self.lines.append((now, m.group(1).decode(), ok))

    def send(self, payload):
        os.write(self.fd, frame(payload))

    def drive(self, left, right):
        self.seq += 1
        self.send("V %d %d %d" % (left, right, self.seq))

    def pose(self):
        return json.loads(self.pose_file.read_text())

    def wait_pose(self):
        for _ in range(100):
            try:
                return self.pose()
            except (FileNotFoundError, ValueError):
                time.sleep(0.05)
        raise TimeoutError

    def drive_for(self, left, right, seconds, until=None):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.drive(left, right)
            time.sleep(0.05)
            if until and until():
                return True
        return False

    def last_t(self):
        with self.lock:
            for _, p, ok in reversed(self.lines):
                if p.startswith("T ") and ok:
                    f = p.split()
                    return dict(ms=int(f[1]), mv=int(f[2]), cm=int(f[3]), left=int(f[4]), right=int(f[5]),
                                pan=int(f[6]), flags=int(f[7]), loop_us=int(f[8]), seq=int(f[9]))
        return None

    def close(self):
        self.alive = False
        self.proc.terminate()
        try:
            self.proc.wait(5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        os.close(self.fd)


@pytest.fixture
def twin(lib, tmp_path):
    t = Twin(lib, tmp_path)
    t.wait_pose()
    yield t
    t.close()


def test_hello(twin):
    twin.send("?")
    time.sleep(0.3)
    with twin.lock:
        hs = [p for _, p, ok in twin.lines if p.startswith("H rover") and ok]
    assert hs
    assert "pwm_max=150" in hs[-1] and "standoff_cm=80" in hs[-1]


def test_state_lines(twin):
    time.sleep(1.5)
    with twin.lock:
        ts = [(t, p) for t, p, ok in twin.lines if p.startswith("T ") and ok]
    assert len(ts) >= 15
    gaps = [b[0] - a[0] for a, b in zip(ts[-12:], ts[-11:])]
    assert 0.03 < sum(gaps) / len(gaps) < 0.07
    st = twin.last_t()
    assert st["mv"] == 11800 and st["flags"] == 0 and st["pan"] == 90
    assert st["cm"] == 999 or st["cm"] > 80


def test_forward_and_timeout(twin):
    p0 = twin.wait_pose()
    twin.drive_for(100, 100, 1.5)
    p1 = twin.wait_pose()
    assert p1["x"] - p0["x"] > 0.3
    assert abs(p1["y"] - p0["y"]) < 0.1 and abs(p1["yaw_deg"]) < 5
    st = twin.last_t()
    assert st["left"] == 100 and st["right"] == 100 and st["flags"] & 1
    # stream stops: firmware timeout stops the motors, the rover comes to rest
    time.sleep(1.0)
    pa = twin.wait_pose()
    time.sleep(0.5)
    pb = twin.wait_pose()
    assert abs(pb["x"] - pa["x"]) < 0.01
    st = twin.last_t()
    assert st["left"] == 0 and st["right"] == 0 and st["flags"] & 8


def test_stand_off_blocks_forward_allows_reverse(twin):
    twin.drive_for(120, 120, 8, until=lambda: (twin.last_t() or {}).get("flags", 0) & 2)
    st = twin.last_t()
    assert st["flags"] & 2, st
    time.sleep(0.8)
    twin.drive_for(120, 120, 0.3)
    p = twin.wait_pose()
    # wall inner face at 2.5 m, sensor 0.10 m ahead of centre, body radius 0.10
    assert p["x"] < 2.5 - 0.10 - 0.3
    assert 0 < st["cm"] < 80 or twin.last_t()["cm"] < 80
    x_blocked = p["x"]
    twin.drive_for(-100, -100, 1.0)
    p2 = twin.wait_pose()
    assert p2["x"] < x_blocked - 0.2


def test_pivot_directions(twin):
    p0 = twin.wait_pose()
    # right wheel faster = positive angular = turn left = yaw increases
    twin.drive_for(-60, 60, 1.5)
    p1 = twin.wait_pose()
    assert (p1["yaw_deg"] - p0["yaw_deg"] + 180) % 360 - 180 > 20
    twin.drive_for(60, -60, 3.0)
    p2 = twin.wait_pose()
    assert (p2["yaw_deg"] - p1["yaw_deg"] + 180) % 360 - 180 < -20
    assert abs(p2["x"]) < 0.15 and abs(p2["y"]) < 0.15


def test_pan(twin):
    twin.send("P 150")
    time.sleep(1.5)
    assert twin.last_t()["pan"] == 150
    assert abs(twin.wait_pose()["pan_deg"] - 150) < 3
    twin.send("P 30")
    time.sleep(0.5)
    assert 110 < twin.last_t()["pan"] < 130, "the pan moves at a limited rate"
    time.sleep(2.5)
    assert abs(twin.wait_pose()["pan_deg"] - 30) < 3


def test_bad_checksum_flag(twin):
    os.write(twin.fd, b"$V 100 100 1*00\n")
    time.sleep(0.3)
    with twin.lock:
        flags = [int(p.split()[7]) for _, p, ok in twin.lines if p.startswith("T ")]
    assert any(f & 16 for f in flags)
    assert twin.wait_pose()["left_pwm"] == 0


def test_reopen_slave(twin):
    twin.alive = False
    os.close(twin.fd)
    twin.t.join(2)
    time.sleep(0.2)
    twin.fd = os.open(twin.pty, os.O_RDWR | os.O_NOCTTY)
    twin.alive = True
    twin.t = threading.Thread(target=twin._read, daemon=True)
    twin.t.start()
    n = len(twin.lines)
    twin.send("?")
    time.sleep(0.5)
    assert any(p.startswith("H ") for _, p, _ in twin.lines[n:])


def read_part(resp):
    buf = b""
    while b"\r\n\r\n" not in buf or not buf.startswith(b"--frame"):
        buf += resp.read(1)
    head, _, _ = buf.partition(b"\r\n\r\n")
    hd = dict(l.split(": ", 1) for l in head.decode().split("\r\n")[1:])
    body = resp.read(int(hd["Content-Length"]))
    return hd, body


def test_render_streams(lib, tmp_path):
    from PIL import Image
    import numpy as np
    try:
        t = Twin(lib, tmp_path, "--start", "-1", "0", "0", render=True)
    except RuntimeError as e:
        pytest.skip("rendering unavailable: %s" % e)
    try:
        for name in ("mast", "overview"):
            c = http.client.HTTPConnection("127.0.0.1", t.http_port, timeout=20)
            c.request("GET", "/%s.mjpg" % name)
            r = c.getresponse()
            assert "multipart/x-mixed-replace" in r.getheader("Content-Type")
            hd, body = read_part(r)
            ts = int(hd["X-Timestamp-Ns"])
            assert abs(time.time_ns() - ts) < 5e9
            im = Image.open(io.BytesIO(body))
            assert im.size == (640, 480)
            assert np.asarray(im).std() > 8
            c.close()
    finally:
        t.close()
