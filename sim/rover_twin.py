#!/usr/bin/env python3
"""Simulated twin of the rover: the real firmware core (host library) plus a MuJoCo rover.

The Pi-side adapter opens the pty slave exactly as it would open the serial port.
"""
import argparse
import ctypes
import errno
import json
import math
import os
import pathlib
import queue
import signal
import socketserver
import sys
import termios
import threading
import time
import tty
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

if sys.platform == "darwin":
    os.environ.setdefault("MUJOCO_GL", "cgl")
else:
    os.environ.setdefault("MUJOCO_GL", "egl")

import mujoco  # noqa: E402
import numpy as np  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent

# Nominal electrical parameters; none measured.
L293D_DROP_V = 2.6
MOTOR_RAD_S_PER_V = 3.5       # no-load speed per volt (TT motor, ~200 rpm at 6 V)
MOTOR_STALL_NM_PER_V = 0.025  # stall torque per volt at the wheel
RANGE_MAX_CM = 400
RANGE_NONE_CM = 999
RANGE_PERIOD_S = 0.060
LOOP_US = 300                 # nominal firmware loop time reported to the core
STEP_S = 0.001
RENDER_FPS = 15
RENDER_W, RENDER_H = 640, 480
JPEG_QUALITY = 80
MAX_CATCHUP_S = 0.1


def load_core(path):
    lib = ctypes.CDLL(str(path))
    lib.rc_host_new.restype = ctypes.c_void_p
    lib.rc_host_free.argtypes = [ctypes.c_void_p]
    lib.rc_host_rx.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t]
    lib.rc_host_tick.argtypes = [
        ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint16, ctypes.c_uint16, ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_int16), ctypes.POINTER(ctypes.c_int16), ctypes.POINTER(ctypes.c_uint8)]
    lib.rc_host_tx.restype = ctypes.c_size_t
    lib.rc_host_tx.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t]
    return lib


class Stream:
    """Latest JPEG of one camera, shared with the HTTP handler threads."""

    def __init__(self):
        self.cond = threading.Condition()
        self.frame = None
        self.seq = 0
        self.clients = 0

    def publish(self, jpeg, ts_ns):
        with self.cond:
            self.frame = (jpeg, ts_ns)
            self.seq += 1
            self.cond.notify_all()


def make_handler(streams, stop):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        def log_message(self, *a):
            pass

        def do_GET(self):
            st = streams.get(self.path.split("?")[0])
            if st is None:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            with st.cond:
                st.clients += 1
                seen = st.seq
            try:
                while not stop.is_set():
                    with st.cond:
                        st.cond.wait_for(lambda: st.seq != seen or stop.is_set(), timeout=1.0)
                        if st.seq == seen or st.frame is None:
                            continue
                        seen = st.seq
                        jpeg, ts = st.frame
                    self.wfile.write(
                        b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n"
                        b"X-Timestamp-Ns: %d\r\n\r\n" % (len(jpeg), ts) + jpeg + b"\r\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                with st.cond:
                    st.clients -= 1

    return Handler


class Server(ThreadingHTTPServer):
    daemon_threads = True


class Encoder(threading.Thread):
    def __init__(self, streams):
        super().__init__(daemon=True)
        self.q = queue.Queue(maxsize=4)
        self.streams = streams

    def run(self):
        import io
        from PIL import Image
        while True:
            item = self.q.get()
            if item is None:
                return
            name, rgb, ts = item
            buf = io.BytesIO()
            Image.fromarray(rgb).save(buf, "JPEG", quality=JPEG_QUALITY)
            self.streams[name].publish(buf.getvalue(), ts)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pty-link", help="symlink to create/replace, pointing at the pty slave")
    ap.add_argument("--http-port", type=int, default=0, help="MJPEG port on 127.0.0.1 (0 = free port)")
    ap.add_argument("--pack-mv", type=int, default=11800)
    ap.add_argument("--no-render", action="store_true")
    ap.add_argument("--start", nargs=3, type=float, metavar=("X", "Y", "YAW_DEG"), default=[0, 0, 0])
    ap.add_argument("--dump-pose-file", help="JSON state file, rewritten ~5 times per second")
    ap.add_argument("--lib", help="firmware core host library")
    ap.add_argument("--model", default=str(HERE / "rover.xml"))
    args = ap.parse_args()

    libpath = args.lib
    if not libpath:
        for cand in ("librover_core.dylib", "librover_core.so"):
            p = REPO / "build" / cand
            if p.exists():
                libpath = p
                break
    if not libpath or not pathlib.Path(libpath).exists():
        sys.exit("firmware library not found; run: make -f mk/firmware.mk rover-core-lib")
    core = load_core(libpath)
    rc = core.rc_host_new()

    model = mujoco.MjModel.from_xml_path(args.model)
    data = mujoco.MjData(model)
    jid = {n: model.joint(n) for n in ("root", "wheel_l", "wheel_r", "pan")}
    qadr = {n: j.qposadr[0] for n, j in jid.items()}
    vadr = {n: j.dofadr[0] for n, j in jid.items()}
    x0, y0, yaw0 = args.start
    qa = qadr["root"]
    data.qpos[qa:qa + 2] = (x0, y0)
    half = math.radians(yaw0) / 2
    data.qpos[qa + 3:qa + 7] = (math.cos(half), 0, 0, math.sin(half))
    mujoco.mj_forward(model, data)
    rover_bid = model.body("rover").id
    site_id = model.site("range").id

    # pty, raw, slave kept open here so the adapter may close and reopen it
    master, slave = os.openpty()
    tty.setraw(slave)
    slave_path = os.ttyname(slave)
    os.set_blocking(master, False)
    link = None
    if args.pty_link:
        link = pathlib.Path(args.pty_link)
        try:
            link.unlink()
        except FileNotFoundError:
            pass
        link.symlink_to(slave_path)

    stop = threading.Event()
    for s in (signal.SIGTERM, signal.SIGINT):
        signal.signal(s, lambda *a: stop.set())

    streams = {"/mast.mjpg": Stream(), "/overview.mjpg": Stream()}
    cams = {"/mast.mjpg": "mast", "/overview.mjpg": "overview"}
    port = 0
    renderer = encoder = None
    if not args.no_render:
        server = Server(("127.0.0.1", args.http_port), make_handler(streams, stop))
        port = server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        renderer = mujoco.Renderer(model, RENDER_H, RENDER_W)
        encoder = Encoder(streams)
        encoder.start()

    print(f"READY pty={slave_path} http={port}", flush=True)

    pack_v = args.pack_mv / 1000.0
    v_motor = max(pack_v - L293D_DROP_V, 0.0)
    left = ctypes.c_int16(0)
    right = ctypes.c_int16(0)
    pan = ctypes.c_uint8(90)
    txbuf = ctypes.create_string_buffer(512)
    tx_pending = b""
    range_cm = RANGE_NONE_CM
    next_range = 0.0
    next_render = 0.0
    next_dump = 0.0
    kv, kt = MOTOR_RAD_S_PER_V, MOTOR_STALL_NM_PER_V
    act_l, act_r, act_pan = 0, 1, 2

    def measure_range():
        mat = data.site_xmat[site_id].reshape(3, 3)
        d = np.ascontiguousarray(mat[:, 0])
        gid = np.zeros(1, dtype=np.int32)
        dist = mujoco.mj_ray(model, data, data.site_xpos[site_id].copy(), d, None, True,
                             rover_bid, gid)
        if dist < 0 or dist * 100 > RANGE_MAX_CM:
            return RANGE_NONE_CM
        return int(round(dist * 100))

    def dump(t_sim):
        q = data.qpos
        w, qx, qy, qz = q[qa + 3:qa + 7]
        yaw = math.degrees(math.atan2(2 * (w * qz + qx * qy), 1 - 2 * (qy * qy + qz * qz)))
        state = {"t": t_sim, "x": float(q[qa]), "y": float(q[qa + 1]), "yaw_deg": yaw,
                 "pan_deg": math.degrees(q[qadr["pan"]]) + 90, "range_cm": range_cm,
                 "left_pwm": left.value, "right_pwm": right.value, "pan_cmd": pan.value,
                 "wheel_l": float(data.qvel[vadr["wheel_l"]]), "wheel_r": float(data.qvel[vadr["wheel_r"]])}
        tmp = args.dump_pose_file + ".tmp"
        with open(tmp, "w") as f:
            json.dump(state, f)
        os.replace(tmp, args.dump_pose_file)

    wall0 = time.monotonic()
    sim_t = 0.0
    try:
        while not stop.is_set():
            # bytes from the adapter
            try:
                rx = os.read(master, 4096)
                if rx:
                    core.rc_host_rx(rc, rx, len(rx))
            except BlockingIOError:
                pass
            except OSError as e:
                if e.errno not in (errno.EIO, errno.EAGAIN):
                    raise

            target = time.monotonic() - wall0
            if target - sim_t > MAX_CATCHUP_S:
                sim_t = target - MAX_CATCHUP_S
            while sim_t < target:
                if sim_t >= next_range:
                    range_cm = measure_range()
                    next_range = sim_t + RANGE_PERIOD_S
                core.rc_host_tick(rc, int(sim_t * 1000) & 0xFFFFFFFF, args.pack_mv, range_cm, LOOP_US,
                                  ctypes.byref(left), ctypes.byref(right), ctypes.byref(pan))
                for side, act, pwm in (("wheel_l", act_l, left.value), ("wheel_r", act_r, right.value)):
                    if pwm == 0:
                        tau = 0.0
                    else:
                        v = math.copysign(abs(pwm) / 255.0 * v_motor, pwm)
                        tau = kt * (v - data.qvel[vadr[side]] / kv)
                    data.ctrl[act] = tau
                data.ctrl[act_pan] = math.radians(min(pan.value, 180) - 90)
                mujoco.mj_step(model, data)
                sim_t += STEP_S
                while True:
                    n = core.rc_host_tx(rc, txbuf, len(txbuf))
                    if not n:
                        break
                    tx_pending += txbuf.raw[:n]
            if tx_pending:
                try:
                    n = os.write(master, tx_pending)
                    tx_pending = tx_pending[n:]
                except (BlockingIOError, OSError):
                    if len(tx_pending) > 8192:
                        tx_pending = b""

            if renderer and sim_t >= next_render:
                next_render = sim_t + 1.0 / RENDER_FPS
                for path, st in streams.items():
                    if st.clients > 0 and not encoder.q.full():
                        renderer.update_scene(data, camera=cams[path])
                        encoder.q.put((path, renderer.render().copy(), time.time_ns()))
            if args.dump_pose_file and sim_t >= next_dump:
                next_dump = sim_t + 0.1
                dump(sim_t)
            time.sleep(0.001)
    finally:
        stop.set()
        if renderer:
            server.shutdown()
            encoder.q.put(None)
        core.rc_host_free(rc)
        if link is not None:
            try:
                link.unlink()
            except FileNotFoundError:
                pass


if __name__ == "__main__":
    main()
