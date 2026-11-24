"""Generate rover-cam.kicad_sch and rover-cam.kicad_sym (KiCad 10).

Re-run from the repo root:
    python3 hardware/gen_schematic.py
then export the docs images:
    K=/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli
    $K sch export svg -o /tmp/svg hardware/rover-cam.kicad_sch && cp /tmp/svg/rover-cam.svg docs/wiring.svg
    $K sch export pdf -o docs/wiring.pdf hardware/rover-cam.kicad_sch

Stock symbols are copied from the KiCad install into lib_symbols. The script
finishes with `kicad-cli sch upgrade` so the file is in KiCad 10's own format.
"""
import re
import subprocess
import uuid
from pathlib import Path

HERE = Path(__file__).parent
SYMDIR = Path("/Applications/KiCad/KiCad.app/Contents/SharedSupport/symbols")
KICAD_CLI = "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli"
ROOT = "6b0b1f2e-0000-4000-8000-000000000001"
FONT = "(font (size 1.27 1.27))"


def uid():
    return str(uuid.uuid4())


# ---------------------------------------------------------------- libraries
def stock_block(lib, name):
    t = (SYMDIR / f"{lib}.kicad_sym").read_text()
    i = t.index(f'\t(symbol "{name}"\n')
    d = 0
    for j in range(i, len(t)):
        if t[j] == "(":
            d += 1
        elif t[j] == ")":
            d -= 1
            if d == 0:
                return t[i:j + 1]


PIN_RE = re.compile(r'\(pin (\w+) \w+\s+\(at ([-\d. ]+)\)\s+\(length [\d.]+\)'
                    r'(?:\s*\(hide yes\))?\s+\(name "([^"]*)".*?\(number "([^"]*)"', re.S)


class Sym:
    def __init__(self, libname, name, text):
        self.lib_id = f"{libname}:{name}"
        self.text = text
        self.pins = {}
        for m in PIN_RE.finditer(text):
            x, y, a = (float(v) for v in m.group(2).split())
            self.pins[m.group(4)] = (x, y, m.group(3), m.group(1))
        self.props = {}
        for m in re.finditer(r'\(property "(\w+)" "[^"]*"\s+\(at ([-\d. ]+)\)(.*?)\n\t\t\)', text, re.S):
            x, y, a = (float(v) for v in m.group(2).split())
            j = re.search(r"\(justify ([^)]*)\)", m.group(3))
            self.props[m.group(1)] = (x, y, j.group(1) if j else None)

    def embedded(self):
        return self.text.replace(f'(symbol "{self.lib_id.split(":")[1]}"',
                                 f'(symbol "{self.lib_id}"', 1)


def stock(lib, name):
    return Sym(lib, name, stock_block(lib, name))


def custom(name, ref, desc, half_w, top, bottom, pins):
    """pins: (number, name, side, x, y); side in L,R,T,B; coords are the pin's connection point."""
    ang = {"L": 0, "R": 180, "T": 270, "B": 90}
    out = [f'\t(symbol "{name}"\n\t\t(exclude_from_sim no)\n\t\t(in_bom yes)\n\t\t(on_board yes)\n'
           f'\t\t(duplicate_pin_numbers_are_jumpers no)']

    def prop(k, v, x, y, hide=False, just=""):
        out.append(f'\t\t(property "{k}" "{v}"\n\t\t\t(at {x} {y} 0)\n\t\t\t(show_name no)\n'
                   f'\t\t\t(do_not_autoplace no)\n' + ("\t\t\t(hide yes)\n" if hide else "") +
                   f'\t\t\t(effects\n\t\t\t\t(font\n\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t)\n'
                   + (f"\t\t\t\t(justify {just})\n" if just else "") + "\t\t\t)\n\t\t)")
    prop("Reference", ref, -half_w, top + 1.27, just="left bottom")
    prop("Value", name, -half_w, bottom - 1.27, just="left top")
    prop("Footprint", "", 0, 0, True)
    prop("Datasheet", "", 0, 0, True)
    prop("Description", desc, 0, 0, True)
    out.append(f'\t\t(symbol "{name}_0_1"\n\t\t\t(rectangle\n\t\t\t\t(start {-half_w} {top})\n'
               f'\t\t\t\t(end {half_w} {bottom})\n\t\t\t\t(stroke\n\t\t\t\t\t(width 0.254)\n'
               f'\t\t\t\t\t(type default)\n\t\t\t\t)\n\t\t\t\t(fill\n\t\t\t\t\t(type background)\n\t\t\t\t)\n\t\t\t)\n\t\t)')
    out.append(f'\t\t(symbol "{name}_1_1"')
    for num, pname, side, x, y in pins:
        out.append(f'\t\t\t(pin passive line\n\t\t\t\t(at {x} {y} {ang[side]})\n\t\t\t\t(length 2.54)\n'
                   f'\t\t\t\t(name "{pname}"\n\t\t\t\t\t(effects\n\t\t\t\t\t\t{FONT}\n\t\t\t\t\t)\n\t\t\t\t)\n'
                   f'\t\t\t\t(number "{num}"\n\t\t\t\t\t(effects\n\t\t\t\t\t\t{FONT}\n\t\t\t\t\t)\n\t\t\t\t)\n\t\t\t)')
    out.append("\t\t)")
    out.append("\t\t(embedded_fonts no)\n\t)")
    return Sym("rover-cam", name, "\n".join(out))


BUCK = custom("BUCK_5V", "U", "5 V buck converter", 10.16, 7.62, -7.62,
              [("1", "IN+", "L", -12.7, 2.54), ("2", "IN-", "L", -12.7, -2.54),
               ("3", "+5V OUT", "R", 12.7, 2.54), ("4", "GND OUT", "R", 12.7, -2.54)])

# Shield: left D pins, top/bottom power, right servo and motor terminals.
_L = [("D4 latch clock", 7.62), ("D5 M4 PWM", 5.08), ("D6 M3 PWM", 2.54), ("D7 latch enable", 0),
      ("D8 latch data", -2.54), ("D9 servo 1", -5.08), ("D12 latch", -12.7)]
SHIELD_PINS = ([(str(i + 1), n, "L", -22.86, y) for i, (n, y) in enumerate(_L)] +
               [("8", "+5V logic", "T", -12.7, 25.4), ("9", "GND", "B", -12.7, -50.8),
                ("10", "EXT_PWR +M", "T", 0, 25.4), ("11", "EXT_PWR GND", "B", 0, -50.8),
                ("12", "M3 A", "R", 22.86, -10.16), ("13", "M3 B", "R", 22.86, -22.86),
                ("14", "M4 A", "R", 22.86, -33.02), ("15", "M4 B", "R", 22.86, -45.72),
                ("16", "SERVO1 S", "R", 22.86, 12.7), ("17", "SERVO1 +", "R", 22.86, 10.16),
                ("18", "SERVO1 -", "R", 22.86, 7.62)])
SHIELD = custom("Adafruit_MotorShield_v1", "U", "Adafruit Motor Shield v1", 20.32, 22.86, -48.26,
                SHIELD_PINS)
HCSR04 = custom("HC-SR04", "U", "Ultrasonic ranger", 12.7, 10.16, -10.16,
                [("3", "Echo", "L", -15.24, 7.62), ("2", "Trig", "L", -15.24, 2.54),
                 ("1", "VCC", "L", -15.24, -2.54), ("4", "GND", "L", -15.24, -7.62)])
USBC = custom("USB_C_Plug", "J", "USB-C plug on a cut USB-A to C cable (red = VBUS, black = GND)", 7.62, 5.08, -5.08,
              [("1", "VBUS", "L", -10.16, 2.54), ("2", "GND", "L", -10.16, -2.54)])

BAT = stock("Device", "Battery")
FUSE = stock("Device", "Fuse")
SW = stock("Switch", "SW_SPST")
RES = stock("Device", "R")
MDC = stock("Motor", "Motor_DC")
MSERVO = stock("Motor", "Motor_Servo")
PI = stock("Connector", "Raspberry_Pi_2_3")
ARD = stock("MCU_Module", "Arduino_UNO_R3")
GNDS = stock("power", "GND")
P5V = stock("power", "+5V")
FLAG = stock("power", "PWR_FLAG")
ALL_SYMS = [BAT, FUSE, SW, RES, MDC, MSERVO, PI, ARD, GNDS, P5V, FLAG, BUCK, SHIELD, HCSR04, USBC]

# ----------------------------------------------------------------- drawing
out = []
counts = {}


def f(v):
    return f"{round(v, 4):g}"


def rot(px, py, r):
    return {0: (px, py), 90: (-py, px), 180: (-px, -py), 270: (py, -px)}[r]


def pin_at(origin, r, sym, num, mir=False):
    px, py = sym.pins[num][:2]
    if mir:
        px = -px
    rx, ry = rot(px, py, r)
    return (round(origin[0] + rx, 4), round(origin[1] - ry, 4))


def origin_for(sym, num, target, r=0):
    px, py = sym.pins[num][:2]
    rx, ry = rot(px, py, r)
    return (target[0] - rx, target[1] + ry)


def next_ref(prefix):
    counts[prefix] = counts.get(prefix, 0) + 1
    return f"{prefix}{counts[prefix]:03d}" if prefix == "#PWR" else f"{prefix}{counts[prefix]:03d}"


def prop_txt(k, v, x, y, hide=False, just=None):
    return (f'\t\t(property "{k}" "{v}"\n\t\t\t(at {f(x)} {f(y)} 0)\n'
            + ("\t\t\t(hide yes)\n" if hide else "") +
            f'\t\t\t(effects\n\t\t\t\t{FONT}\n' + (f"\t\t\t\t(justify {just})\n" if just else "") + "\t\t\t)\n\t\t)")


def place(sym, ref, value, origin, r=0, mir=False, ref_pos=None, val_pos=None, hide_ref=False, hide_val=False,
          ref_just=None, val_just=None):
    ox, oy = origin
    pr, pv = sym.props.get("Reference"), sym.props.get("Value")

    def default(p):
        return (ox + p[0], oy - p[1], p[2]) if p else (ox, oy, None)
    rp = (*ref_pos, ref_just) if ref_pos else default(pr)
    vp = (*val_pos, val_just) if val_pos else default(pv)
    pins = "".join(f'\n\t\t(pin "{n}"\n\t\t\t(uuid "{uid()}")\n\t\t)' for n in sym.pins)
    out.append(
        f'\t(symbol\n\t\t(lib_id "{sym.lib_id}")\n\t\t(at {f(ox)} {f(oy)} {r})\n' + ('\t\t(mirror y)\n' if mir else '') + f'\t\t(unit 1)\n'
        f'\t\t(body_style 1)\n\t\t(exclude_from_sim no)\n\t\t(in_bom yes)\n\t\t(on_board yes)\n\t\t(dnp no)\n'
        f'\t\t(uuid "{uid()}")\n'
        + prop_txt("Reference", ref, rp[0], rp[1], hide_ref, rp[2]) + "\n"
        + prop_txt("Value", value, vp[0], vp[1], hide_val, vp[2]) + "\n"
        + prop_txt("Footprint", "", ox, oy, True) + "\n"
        + prop_txt("Datasheet", "", ox, oy, True) + "\n"
        + prop_txt("Description", "", ox, oy, True)
        + pins +
        f'\n\t\t(instances\n\t\t\t(project "rover-cam"\n\t\t\t\t(path "/{ROOT}"\n'
        f'\t\t\t\t\t(reference "{ref}")\n\t\t\t\t\t(unit 1)\n\t\t\t\t)\n\t\t\t)\n\t\t)\n\t)')


def wire(*pts):
    for a, b in zip(pts, pts[1:]):
        out.append(f'\t(wire\n\t\t(pts\n\t\t\t(xy {f(a[0])} {f(a[1])}) (xy {f(b[0])} {f(b[1])})\n\t\t)\n'
                   f'\t\t(stroke\n\t\t\t(width 0)\n\t\t\t(type default)\n\t\t)\n\t\t(uuid "{uid()}")\n\t)')


def junction(p):
    out.append(f'\t(junction\n\t\t(at {f(p[0])} {f(p[1])})\n\t\t(diameter 0)\n\t\t(color 0 0 0 0)\n\t\t(uuid "{uid()}")\n\t)')


def nc(p):
    out.append(f'\t(no_connect\n\t\t(at {f(p[0])} {f(p[1])})\n\t\t(uuid "{uid()}")\n\t)')


def label(name, p, angle=0):
    just = "right bottom" if angle == 180 else "left bottom"
    out.append(f'\t(label "{name}"\n\t\t(at {f(p[0])} {f(p[1])} {angle})\n\t\t(effects\n\t\t\t{FONT}\n'
               f'\t\t\t(justify {just})\n\t\t)\n\t\t(uuid "{uid()}")\n\t)')


def text(s, p):
    s = s.replace("\n", "\\n")
    out.append(f'\t(text "{s}"\n\t\t(exclude_from_sim no)\n\t\t(at {f(p[0])} {f(p[1])} 0)\n'
               f'\t\t(effects\n\t\t\t{FONT}\n\t\t\t(justify left top)\n\t\t)\n\t\t(uuid "{uid()}")\n\t)')


def power(kind, p):
    sym = GNDS if kind == "GND" else P5V
    place(sym, next_ref("#PWR"), kind, p, hide_ref=True)


def flag(p, r=0):
    place(FLAG, next_ref("#FLG"), "PWR_FLAG", p, r, hide_ref=True, hide_val=True)


DIRS = {"L": (-1, 0), "R": (1, 0), "U": (0, -1), "D": (0, 1)}


def tag(pt, d, kind, stub=5.08):
    """Short wire out of a pin end in direction d (L/R/U/D), ending in a net label or power symbol."""
    dx, dy = DIRS[d]
    e = (pt[0] + dx * stub, pt[1] + dy * stub)
    wire(pt, e)
    if kind in ("GND", "+5V"):
        power(kind, e)
    else:
        label(kind, e, 180 if d == "L" else 0)
    return e


# ------------------------------------------------------------ power chain
Y_VBAT, Y_5V = 55.88, 63.5          # the two horizontal buses along the top
bt = (35.56, 83.82)
place(BAT, "BT1", "3S LiPo 11.1V XT60", bt, ref_pos=(40.64, 83.82 - 2.54), val_pos=(40.64, 83.82 + 2.54),
      ref_just="left", val_just="left")
bplus, bminus = pin_at(bt, 0, BAT, "1"), pin_at(bt, 0, BAT, "2")
f1 = origin_for(FUSE, "1", (50.8, Y_VBAT), 90)
place(FUSE, "F1", "5A blade", f1, 90, ref_pos=(f1[0], f1[1] - 3.81), val_pos=(f1[0], f1[1] + 3.81))
sw1 = origin_for(SW, "1", (66.04, Y_VBAT))
place(SW, "SW1", "chassis switch", sw1, ref_pos=(sw1[0], sw1[1] - 5.08), val_pos=(sw1[0], sw1[1] + 5.08))
wire(bplus, (35.56, Y_VBAT), pin_at(f1, 90, FUSE, "1"))
wire(pin_at(f1, 90, FUSE, "2"), pin_at(sw1, 0, SW, "1"))
VBX = 86.36                          # VBAT riser
wire(pin_at(sw1, 0, SW, "2"), (81.28, Y_VBAT), (VBX, Y_VBAT))
flag((81.28, Y_VBAT))
junction((VBX, Y_VBAT))
text("VBAT: pack after fuse and switch, 10.5-12.6 V.\n5 A fuse: two stalled motors can exceed 3 A.", (30.48, 37.0))
gb = (35.56, 99.06)
wire(bminus, (35.56, 93.98), gb)
power("GND", gb)
flag((35.56, 93.98), 90)

u1 = (109.22, 116.84)
place(BUCK, "U1", "AKEYSRC 5V 3A buck", u1)
in_p, in_n = pin_at(u1, 0, BUCK, "1"), pin_at(u1, 0, BUCK, "2")
o_p, o_n = pin_at(u1, 0, BUCK, "3"), pin_at(u1, 0, BUCK, "4")
Y_VFEED = 147.32                     # VBAT feed to the divider, under the Pi
X_DIV = 210.82
wire((VBX, Y_VBAT), (VBX, in_p[1]), (VBX, Y_VFEED), (X_DIV, Y_VFEED), (X_DIV, 152.4))
junction((VBX, in_p[1]))
wire((VBX, in_p[1]), in_p)
tag(in_n, "L", "GND")

# J2: the cut USB-A to C cable
j2 = (142.24, 116.84)
place(USBC, "J2", "USB-C plug", j2)
jv, jg = pin_at(j2, 0, USBC, "1"), pin_at(j2, 0, USBC, "2")
wire(o_p, jv)
junction(jv)
wire(jv, (jv[0], Y_5V))
gt = (127.0, jg[1])
wire(o_n, gt, jg)
junction(gt)
tag(gt, "D", "GND")
text("Cut USB-A→C cable into the Pi's USB-C;\nred = +5 V, black = GND. Meter it before\nplugging in.\n"
     "U1: AKEYSRC 5 V 3 A buck (B0GYJDP96Q).", (91.44, 134.62))

# --------------------------------------------------------------- Raspberry Pi
j1 = (185.42, 101.6)
place(PI, "J1", "Raspberry Pi 4", j1, mir=True, val_pos=(j1[0] - 20.32, j1[1] + 36.83), val_just="left top")
sig = {"8": "GPIO14_TXD", "10": "GPIO15_RXD", "12": "GPIO18_RESET"}
done = set()
for num, (px, py, pname, ptype) in PI.pins.items():
    p = pin_at(j1, 0, PI, num, True)
    if num in sig:
        continue
    if pname == "5V":
        if p not in done:
            wire(p, (p[0], Y_5V))
            done.add(p)
    elif pname == "GND":
        if p not in done:
            tag(p, "D", "GND")
            done.add(p)
    elif p not in done:
        nc(p)
        done.add(p)
pi8, pi10, pi12 = (pin_at(j1, 0, PI, n, True) for n in ("8", "10", "12"))
X_PI5 = pin_at(j1, 0, PI, "2", True)[0]
text("Pi powered via USB-C (see J2).\nPi Camera v1 on the CSI ribbon.", (162.56, 153.67))

# ----------------------------------------------------------------- aLaMode
a1 = (259.08, 106.68)
place(ARD, "A1", "WyoLum aLaMode (Uno-compatible)", a1, mir=True, val_pos=(a1[0] - 10.16, a1[1] + 38.1), val_just="left top")


def ap(n):
    return pin_at(a1, 0, ARD, n, True)


pin_nets = {"15", "16", "3", "19", "20", "21", "22", "23", "24", "27", "9", "10", "11", "5"}
for num, (px, py, pname, ptype) in ARD.pins.items():
    if ptype == "no_connect" or num in ("6", "7", "29") or num in pin_nets:
        continue
    nc(ap(num))
g6, g7, g29 = ap("6"), ap("7"), ap("29")
wire(g29, g7)
junction(g6)
tag(g6, "D", "GND")
text("5V-LINK jumper ON: powered from the Pi.\nRESET is capacitor-coupled to GPIO18\non the aLaMode.\n"
     "Pi-aLaMode and aLaMode-shield links\nare stacked headers, not wires.", (248.92, 148.59))

# +5V bus: J2 -> Pi -> aLaMode -> shield -> servo -> HC-SR04
X_5 = 157.48                         # +5V riser to the HC-SR04
u2 = (320.04, 109.22)
X_SHV = pin_at(u2, 0, SHIELD, "8")[0]
X_DROP = 350.52                      # +5V drop to the servo + wire
bus5 = [(jv[0], Y_5V), (144.78, Y_5V), (X_5, Y_5V), (X_PI5, Y_5V), (ap("5")[0], Y_5V), (X_SHV, Y_5V),
        (X_DROP, Y_5V)]
wire(*bus5)
for pt in bus5[1:-1]:
    junction(pt)
power("+5V", (144.78, Y_5V))
wire(ap("5"), (ap("5")[0], Y_5V))

# --------------------------------------------- Pi serial and reset to the aLaMode
L8, L10 = 68.58, 73.66
X8, X10, XR8, XR10 = 213.36, 218.44, 281.94, 287.02
wire(pi8, (X8, pi8[1]), (X8, L8), (XR8, L8), (XR8, ap("15")[1]), ap("15"))
wire(pi10, (X10, pi10[1]), (X10, L10), (XR10, L10), (XR10, ap("16")[1]), ap("16"))
wire(pi12, ap("3"))
label("GPIO14_TXD", (260.35, L8))
label("GPIO15_RXD", (260.35, L10))
label("GPIO18_RESET", (223.52, pi12[1]))

# ------------------------------------------------------------ motor shield
place(SHIELD, "U2", "Adafruit Motor Shield v1, PWR jumper REMOVED", u2,
      val_pos=(u2[0] - 20.32, u2[1] + 64.77), val_just="left top")
dmap = {"1": "19", "2": "20", "3": "21", "4": "22", "5": "23", "6": "24", "7": "27"}
dname = {"1": "D4", "2": "D5", "3": "D6", "4": "D7", "5": "D8", "6": "D9", "7": "D12"}
for n, an in dmap.items():
    pa, pb = ap(an), pin_at(u2, 0, SHIELD, n)
    assert abs(pa[1] - pb[1]) < 1e-6, (n, pa, pb)
    wire(pa, pb)
    label(dname[n], (pa[0] + 17.78, pa[1]))
wire(pin_at(u2, 0, SHIELD, "8"), (X_SHV, Y_5V))
wire((VBX, Y_VBAT), (96.52, Y_VBAT))
label("VBAT", (96.52, Y_VBAT))
wire((96.52, Y_VBAT), (u2[0], Y_VBAT), pin_at(u2, 0, SHIELD, "10"))
tag(pin_at(u2, 0, SHIELD, "9"), "D", "GND")
tag(pin_at(u2, 0, SHIELD, "11"), "D", "GND")

# Servo
s_s, s_p, s_n = (pin_at(u2, 0, SHIELD, n) for n in ("16", "17", "18"))
sv = origin_for(MSERVO, "2", (s_p[0] + 15.24, s_p[1]))
place(MSERVO, "M3", "Parallax standard servo 4-6 V", sv, ref_pos=(sv[0] - 5.08, sv[1] - 7.62), ref_just="left")
v_s, v_p, v_n = (pin_at(sv, 0, MSERVO, n) for n in ("1", "2", "3"))
wire(s_s, v_s)
wire(s_p, (X_DROP, s_p[1]), v_p)
wire((X_DROP, Y_5V), (X_DROP, s_p[1]))
junction((X_DROP, s_p[1]))
gs = (s_n[0] + 2.54, s_n[1])
wire(s_n, gs, v_n)
junction(gs)
tag(gs, "D", "GND")

# Motors
for ref, val, a, b in (("M1", "left TT motor", "12", "13"), ("M2", "right TT motor", "14", "15")):
    pa, pb = pin_at(u2, 0, SHIELD, a), pin_at(u2, 0, SHIELD, b)
    m = origin_for(MDC, "1", (pa[0] + 15.24, pa[1]))
    place(MDC, ref, val, m)
    wire(pa, pin_at(m, 0, MDC, "1"))
    wire(pb, pin_at(m, 0, MDC, "2"))

# ------------------------------------------------------ divider + HC-SR04
r1 = (X_DIV, 156.21)
place(RES, "R1", "10k", r1, ref_pos=(r1[0] + 2.54, r1[1] - 1.27), val_pos=(r1[0] + 2.54, r1[1] + 1.27), ref_just="left", val_just="left")
r2 = (X_DIV, 168.91)
place(RES, "R2", "3.3k", r2, ref_pos=(r2[0] + 2.54, r2[1] - 1.27), val_pos=(r2[0] + 2.54, r2[1] + 1.27), ref_just="left", val_just="left")
mid, low = pin_at(r1, 0, RES, "2"), pin_at(r2, 0, RES, "1")
jm = (X_DIV, (mid[1] + low[1]) / 2)
wire(mid, jm, low)
junction(jm)
tag(pin_at(r2, 0, RES, "2"), "D", "GND")
text("Pack voltage divider to A0.", (166.37, 172.72))

u3 = (256.54, 182.88)
place(HCSR04, "U3", "HC-SR04", u3)
h_e, h_t, h_v, h_g = (pin_at(u3, 0, HCSR04, n) for n in ("3", "2", "1", "4"))
X_A0, X_A1, X_A2 = 223.52, 228.6, 233.68
wire(ap("9"), (X_A0, ap("9")[1]), (X_A0, jm[1]), jm)
wire(ap("10"), (X_A1, ap("10")[1]), (X_A1, h_t[1]), h_t)
wire(ap("11"), (X_A2, ap("11")[1]), (X_A2, h_e[1]), h_e)
wire((X_5, Y_5V), (X_5, h_v[1]), h_v)
junction((X_5, Y_5V))
label("PACK_SENSE", (229.87, ap("9")[1]))
label("TRIG", (229.87, h_t[1]))
label("ECHO", (235.0, h_e[1]))
tag(h_g, "L", "GND")
text("HC-SR04 VCC comes from the A3-A5 header 5 V row\n(the A0-A2 row is 3.3 V).", (236.22, 200.66))

text("Notes\n"
     "VBAT = pack after fuse and switch, 10.5-12.6 V.\n"
     "All grounds are common: run EXT_PWR GND and buck IN- straight\nto the pack negative in heavy wire.\n"
     "Servo 1 header power is the shield's 5 V logic rail, not EXT_PWR.\n"
     "5 A fuse because two stalled motors can exceed 3 A.", (292.1, 190.5))

# ------------------------------------------------------------------- output
sch = (f'(kicad_sch\n\t(version 20250114)\n\t(generator "eeschema")\n\t(generator_version "9.0")\n'
       f'\t(uuid "{ROOT}")\n\t(paper "A3")\n\t(title_block\n\t\t(title "rover-cam wiring")\n'
       f'\t\t(date "2026-11-24")\n\t\t(rev "B")\n\t)\n\t(lib_symbols\n'
       + "\n".join(s.embedded() for s in ALL_SYMS) + "\n\t)\n"
       + "\n".join(out) +
       '\n\t(sheet_instances\n\t\t(path "/"\n\t\t\t(page "1")\n\t\t)\n\t)\n\t(embedded_fonts no)\n)\n')
(HERE / "rover-cam.kicad_sch").write_text(sch)

lib = ('(kicad_symbol_lib\n\t(version 20251024)\n\t(generator "kicad_symbol_editor")\n'
       '\t(generator_version "10.0")\n' + "\n".join(s.text for s in (BUCK, SHIELD, HCSR04, USBC)) + "\n)\n")
(HERE / "rover-cam.kicad_sym").write_text(lib)
(HERE / "sym-lib-table").write_text(
    '(sym_lib_table\n\t(version 7)\n\t(lib (name "rover-cam")(type "KiCad")'
    '(uri "${KIPRJMOD}/rover-cam.kicad_sym")(options "")(descr "rover-cam custom symbols"))\n)\n')
if not (HERE / "rover-cam.kicad_pro").exists():
    (HERE / "rover-cam.kicad_pro").write_text(
        '{\n  "meta": {\n    "filename": "rover-cam.kicad_pro",\n    "version": 3\n  },\n'
        '  "sheets": [\n    ["' + ROOT + '", "Root"]\n  ]\n}\n')
subprocess.run([KICAD_CLI, "sch", "upgrade", str(HERE / "rover-cam.kicad_sch")], check=False)
