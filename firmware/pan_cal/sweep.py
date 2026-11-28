"""Measure the pan servo from the rover's own camera, with firmware/pan_cal on the aLaMode.

Run on the laptop: python sweep.py OUT_DIR CENTRE_US [left|right|both] [LOW_US HIGH_US]
Needs numpy and opencv, ssh to rover.local, pancal.py in the Pi user's home, roverd stopped and
`rover snap` working. It steps the pulse width outward from CENTRE_US (100 us, then 50 us beyond
500 us from the centre), photographs each position and reads the turn between two pictures from
the homography of matched features. A step that turns less than half the usual means the pan has
met something: the sweep backs off and stops. Results: OUT_DIR/sweep.json and the pictures.
"""
import subprocess, sys, json, time, math, os
import numpy as np, cv2
D = sys.argv[1]; os.makedirs(D, exist_ok=True)
CENTRE = int(sys.argv[2])
WHICH = sys.argv[3] if len(sys.argv) > 3 else "both"
LOW, HIGH = (int(sys.argv[4]), int(sys.argv[5])) if len(sys.argv) > 5 else (600, 2400)
HFOV_DEG = 53.5    # the camera's horizontal field of view (OV5647, whole sensor)
W = H = F0 = 0     # picture size and focal length in pixels, set from the first picture
def ssh(cmd, binary=False):
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", "rover.local", cmd], capture_output=True, timeout=40)
    return r.stdout if binary else r.stdout.decode()
def move(us, wait):
    out = ssh(f'python3 ~/pancal.py "U {us}" {wait}')
    for _ in range(20):
        if f"us={us} target={us}" in out: return
        time.sleep(0.5); out = ssh('python3 ~/pancal.py "?"')
    raise AssertionError(out)
def snap(us):
    p = f"{D}/{us}.jpg"; open(p, "wb").write(ssh("rover snap 2>/dev/null", binary=True))
    global W, H, F0
    img = cv2.imread(p, cv2.IMREAD_GRAYSCALE)
    H, W = img.shape
    F0 = (W / 2) / math.tan(math.radians(HFOV_DEG / 2))
    return img
sift = cv2.SIFT_create(3000)
def homography(a, b):
    ka, da = sift.detectAndCompute(a, None); kb, db = sift.detectAndCompute(b, None)
    m = cv2.BFMatcher().knnMatch(da, db, k=2)
    good = [x for x, y in m if x.distance < 0.75 * y.distance]
    if len(good) < 12: return None, len(good), 0.0
    pa = np.array([ka[g.queryIdx].pt for g in good], dtype=np.float32); pb = np.array([kb[g.trainIdx].pt for g in good], dtype=np.float32)
    Hm, mask = cv2.findHomography(pa, pb, cv2.RANSAC, 3.0)
    if Hm is None: return None, len(good), 0.0
    inl = mask.ravel().astype(bool)
    return Hm, int(inl.sum()), float(np.median(pb[inl, 0] - pa[inl, 0]))
def yaw(Hm, f):
    K = np.array([[f, 0, W / 2], [0, f, H / 2], [0, 0, 1.0]])
    R = np.linalg.inv(K) @ Hm @ K
    U, S, Vt = np.linalg.svd(R); R = U @ Vt
    if np.linalg.det(R) < 0: R = -R
    ang = math.degrees(math.acos(max(-1, min(1, (np.trace(R) - 1) / 2))))
    return ang
log: dict = json.load(open(f"{D}/sweep.json")) if os.path.exists(f"{D}/sweep.json") else {"centre": CENTRE, "sides": {}}
for side, sign in [x for x in (("left", +1), ("right", -1)) if WHICH in ("both", x[0])]:
    move(CENTRE, 2.5); time.sleep(0.5)
    prev_img, prev_us = snap(CENTRE), CENTRE
    steps: list[dict] = []
    total = 0.0
    rates: list[float] = []
    us = CENTRE
    while True:
        step = 100 if abs(us - CENTRE) < 500 else 50
        nxt = us + sign * step
        if not LOW <= nxt <= HIGH: print(side, "reached the sweep's pulse limit at", us); break
        move(nxt, step / 200 + 0.6)
        img = snap(nxt)
        Hm, n, dx = homography(prev_img, img)
        if Hm is None:
            print(side, nxt, "no match (inliers", n, ") -> backing off"); move(us, 1.5); break
        a = yaw(Hm, F0); rate = a / step
        steps.append({"from": us, "to": nxt, "deg": round(a, 2), "inliers": n, "dx": round(dx, 1), "H": Hm.tolist()})
        print(f"{side}: {us}->{nxt} us  turned {a:5.2f} deg  ({rate*100:.2f} deg/100us, {n} inliers, dx {dx:+.0f}px)", flush=True)
        if len(rates) >= 2 and rate < 0.5 * np.median(rates):
            print(side, "STOP: step turned less than half the usual -> end stop near", nxt, "; backing off"); move(us, 1.5)
            steps[-1]["stalled"] = True; break
        rates.append(rate); total += a; us, prev_img = nxt, img
        if total >= 95: print(side, "passed 95 deg at", us); break
    log["sides"][side] = {"steps": steps, "last_good_us": us, "total_deg_f0": round(total, 2)}
    json.dump(log, open(f"{D}/sweep.json", "w"))
move(CENTRE, 6)
print("back at centre")
