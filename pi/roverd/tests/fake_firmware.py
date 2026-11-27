"""A scripted stand-in for the rover firmware on a pty, speaking serial protocol v2
(robo-control docs/interfaces.md section 4)."""
from __future__ import annotations

import os
import select
import threading
import time
import tty
from pathlib import Path

MOVING, BLOCKED, LOW_PACK, CMD_TIMEOUT, BAD_LINE = 1, 2, 4, 8, 16


def checksum(payload: bytes) -> int:
    x = 0
    for b in payload:
        x ^= b
    return x


def frame(payload: str) -> bytes:
    p = payload.encode()
    return b"$" + p + b"*%02X\n" % checksum(p)


def unframe(line: bytes) -> str | None:
    line = line.rstrip(b"\r")
    if not line.startswith(b"$") or len(line) < 4 or line[-3:-2] != b"*":
        return None
    payload = line[1:-3]
    try:
        if int(line[-2:], 16) != checksum(payload) or line[-2:] != line[-2:].upper():
            return None
    except ValueError:
        return None
    return payload.decode()


class FakeFirmware:
    """Answers `?` with H, applies V/P/S with the firmware's limits and emits T every 50 ms.

    Everything it received is kept in `rx` as (monotonic time, payload); lines with a bad
    checksum in `rx_bad`. `t_sent` holds the millis() of every T line sent.
    """

    PERIOD_S = 0.05

    def __init__(self, link: Path, *, version: str = "2.0.0-fake", pwm_max: int = 150,
                 standoff_cm: int = 80, min_mv: int = 10500, timeout_ms: int = 300,
                 millis_start: int = 0) -> None:
        self.link = Path(link)
        self.version = version
        self.pwm_max = pwm_max
        self.standoff_cm = standoff_cm
        self.min_mv = min_mv
        self.timeout_ms = timeout_ms
        self.lock = threading.RLock()
        self.rx: list[tuple[float, str]] = []
        self.rx_bad: list[bytes] = []
        self.t_sent: list[int] = []
        # Sensor inputs and test controls.
        self.mv = 11100
        self.cm = 200
        self.loop_us = 1500
        self.extra_flags = 0
        self.silent = False
        self.answer_hello = True
        self.hold_pan = False  # a pan that never reaches its target: P is received but not applied
        # Firmware state.
        self._boot = time.monotonic()
        self._millis_start = millis_start
        self._cmd = (0, 0)
        self._seq = 0
        self._pan = 0
        self._last_v = None
        self._timed_out = False
        self._bad_since_t = False
        self._buf = b""
        self._stop = threading.Event()
        self._open_pty()
        self._send(f"H rover {self.version} pwm_max={self.pwm_max} standoff_cm={self.standoff_cm} "
                   f"min_mv={self.min_mv} timeout_ms={self.timeout_ms}")
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    # ---- pty ----

    def _open_pty(self) -> None:
        self.master, self.slave = os.openpty()
        tty.setraw(self.slave)
        os.set_blocking(self.master, False)
        tmp = self.link.with_name(self.link.name + ".tmp")
        if tmp.is_symlink():
            tmp.unlink()
        tmp.symlink_to(os.ttyname(self.slave))
        os.replace(tmp, self.link)

    def replace_pty(self) -> None:
        """Unplug: close this pty and appear on a new one behind the same link."""
        with self.lock:
            os.close(self.master)
            os.close(self.slave)
            self._open_pty()
            self._buf = b""

    def close(self) -> None:
        self._stop.set()
        self._thread.join(2)
        with self.lock:
            for fd in (self.master, self.slave):
                try:
                    os.close(fd)
                except OSError:
                    pass

    # ---- test controls ----

    def millis(self) -> int:
        return (self._millis_start + int((time.monotonic() - self._boot) * 1000)) & 0xFFFFFFFF

    def reboot(self, millis_start: int = 0) -> None:
        with self.lock:
            self._boot = time.monotonic()
            self._millis_start = millis_start
            self._cmd = (0, 0)
            self._seq = 0
            self._pan = 0
            self._last_v = None
            self._send(f"H rover {self.version} pwm_max={self.pwm_max} standoff_cm={self.standoff_cm} "
                       f"min_mv={self.min_mv} timeout_ms={self.timeout_ms}")

    def send_raw(self, data: bytes) -> None:
        with self.lock:
            os.write(self.master, data)

    def send_line(self, payload: str) -> None:
        self.send_raw(frame(payload))

    def received(self, kind: str | None = None, since: float = 0.0) -> list[str]:
        with self.lock:
            return [p for t, p in self.rx if t >= since and (kind is None or p.split(" ")[0] == kind)]

    def received_timed(self, kind: str, since: float = 0.0) -> list[tuple[float, str]]:
        with self.lock:
            return [(t, p) for t, p in self.rx if t >= since and p.split(" ")[0] == kind]

    def wait_received(self, kind: str, count: int = 1, since: float = 0.0, timeout: float = 3.0) -> list[str]:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            got = self.received(kind, since)
            if len(got) >= count:
                return got
            time.sleep(0.01)
        raise AssertionError(f"fake firmware: wanted {count} {kind!r}, got {self.received(kind, since)}")

    # ---- firmware ----

    def _send(self, payload: str) -> None:
        try:
            os.write(self.master, frame(payload))
        except OSError:
            pass

    def _error(self, code: int, text: str) -> None:
        self._send(f"E {code} {text}")

    def _on_line(self, line: bytes) -> None:
        payload = unframe(line)
        if payload is None:
            self.rx_bad.append(line)
            self._bad_since_t = True
            return
        self.rx.append((time.monotonic(), payload))
        tok = payload.split(" ")
        try:
            if tok[0] == "?" and len(tok) == 1:
                if self.answer_hello:
                    self._send(f"H rover {self.version} pwm_max={self.pwm_max} "
                               f"standoff_cm={self.standoff_cm} min_mv={self.min_mv} "
                               f"timeout_ms={self.timeout_ms}")
            elif tok[0] == "V" and len(tok) == 4:
                left, right, seq = (int(x) for x in tok[1:])
                clamp = lambda v: max(-self.pwm_max, min(self.pwm_max, v))  # noqa: E731
                self._cmd = (clamp(left), clamp(right))
                self._seq = seq
                self._last_v = time.monotonic()
                self._timed_out = False
            elif tok[0] == "P" and len(tok) == 2:
                deg = int(tok[1])
                if not -90 <= deg <= 90:
                    self._error(3, "pan out of range")
                elif not self.hold_pan:
                    self._pan = deg
            elif tok[0] == "S" and len(tok) == 1:
                self._cmd = (0, 0)
                self._last_v = None
            else:
                self._error(2, "unknown command")
        except ValueError:
            self._error(1, "malformed")

    def _tick(self) -> None:
        now = time.monotonic()
        if self._last_v is not None and now - self._last_v > self.timeout_ms / 1000:
            if self._cmd != (0, 0):
                self._timed_out = True
            self._cmd = (0, 0)
            self._last_v = None
        left, right = self._cmd
        flags = 0
        if self.mv < self.min_mv:
            left = right = 0
            flags |= LOW_PACK
        if self.cm < self.standoff_cm and left + right > 0:
            self._cmd = (0, 0)
            left = right = 0
            flags |= BLOCKED
        if left or right:
            flags |= MOVING
        if self._timed_out:
            flags |= CMD_TIMEOUT
        if self._bad_since_t:
            flags |= BAD_LINE
            self._bad_since_t = False
        flags |= self.extra_flags
        ms = self.millis()
        self.t_sent.append(ms)
        self._send(f"T {ms} {self.mv} {self.cm} {left} {right} {self._pan} {flags} {self.loop_us} {self._seq}")

    def _run(self) -> None:
        next_tick = time.monotonic()
        while not self._stop.is_set():
            timeout = max(0.0, next_tick - time.monotonic())
            with self.lock:
                master = self.master
            try:
                readable, _, _ = select.select([master], [], [], timeout)
            except (OSError, ValueError):
                readable = []
                time.sleep(0.01)
            with self.lock:
                if readable and master == self.master:
                    try:
                        data = os.read(self.master, 4096)
                    except (BlockingIOError, InterruptedError):
                        data = b""
                    except OSError:
                        data = b""
                        time.sleep(0.01)
                    self._buf += data
                    while b"\n" in self._buf:
                        line, self._buf = self._buf.split(b"\n", 1)
                        self._on_line(line)
                if time.monotonic() >= next_tick:
                    next_tick += self.PERIOD_S
                    if time.monotonic() > next_tick:
                        next_tick = time.monotonic() + self.PERIOD_S
                    if not self.silent:
                        self._tick()
