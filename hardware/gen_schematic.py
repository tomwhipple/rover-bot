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


UBEC = custom("UBEC_5V", "U", "5 V buck converter", 10.16, 7.62, -7.62,
              [("1", "IN+", "L", -12.7, 2.54), ("2", "IN-", "L", -12.7, -2.54),
               ("3", "+5V OUT", "R", 12.7, 2.54), ("4", "GND OUT", "R", 12.7, -2.54)])

# Shield: left D pins, top/bottom power, right servo and motor terminals.
_L = [("D4 latch clock", 7.62), ("D5 M4 PWM", 5.08), ("D6 M3 PWM", 2.54), ("D7 latch enable", 0),
      ("D8 latch data", -2.54), ("D9 servo 1", -5.08), ("D12 latch", -7.62)]
SHIELD_PINS = ([(str(i + 1), n, "L", -22.86, y) for i, (n, y) in enumerate(_L)] +
               [("8", "+5V logic", "T", -12.7, 25.4), ("9", "GND", "B", -12.7, -50.8),
                ("10", "EXT_PWR +M", "T", 0, 25.4), ("11", "EXT_PWR GND", "B", 0, -50.8),
                ("12", "M3 A", "R", 22.86, -10.16), ("13", "M3 B", "R", 22.86, -22.86),
                ("14", "M4 A", "R", 22.86, -33.02), ("15", "M4 B", "R", 22.86, -45.72),
                ("16", "SERVO1 S", "R", 22.86, 17.78), ("17", "SERVO1 +", "R", 22.86, 10.16),
                ("18", "SERVO1 -", "R", 22.86, 2.54)])
SHIELD = custom("Adafruit_MotorShield_v1", "U", "Adafruit Motor Shield v1", 20.32, 22.86, -48.26,
                SHIELD_PINS)
HCSR04 = custom("HC-SR04", "U", "Ultrasonic ranger", 15.24, 7.62, -7.62,
                [("1", "VCC", "T", -10.16, 10.16), ("2", "Trig", "T", 0, 10.16),
                 ("3", "Echo", "T", 10.16, 10.16), ("4", "GND", "B", 0, -10.16)])

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
ALL_SYMS = [BAT, FUSE, SW, RES, MDC, MSERVO, PI, ARD, GNDS, P5V, FLAG, UBEC, SHIELD, HCSR04]

# ----------------------------------------------------------------- drawing
out = []
counts = {}


def f(v):
    return f"{round(v, 4):g}"


def rot(px, py, r):
    return {0: (px, py), 90: (-py, px), 180: (-px, -py), 270: (py, -px)}[r]


def pin_at(origin, r, sym, num):
    px, py = sym.pins[num][:2]
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


def place(sym, ref, value, origin, r=0, ref_pos=None, val_pos=None, hide_ref=False, hide_val=False,
          ref_just=None, val_just=None):
    ox, oy = origin
    pr, pv = sym.props.get("Reference"), sym.props.get("Value")

    def default(p):
        return (ox + p[0], oy - p[1], p[2]) if p else (ox, oy, None)
    rp = (*ref_pos, ref_just) if ref_pos else default(pr)
    vp = (*val_pos, val_just) if val_pos else default(pv)
    pins = "".join(f'\n\t\t(pin "{n}"\n\t\t\t(uuid "{uid()}")\n\t\t)' for n in sym.pins)
    out.append(
        f'\t(symbol\n\t\t(lib_id "{sym.lib_id}")\n\t\t(at {f(ox)} {f(oy)} {r})\n\t\t(unit 1)\n'
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
bt = (35.56, 83.82)
place(BAT, "BT1", "3S LiPo 11.1V XT60", bt, ref_pos=(40.64, 83.82 - 2.54), val_pos=(40.64, 83.82 + 2.54),
      ref_just="left", val_just="left")
bplus, bminus = pin_at(bt, 0, BAT, "1"), pin_at(bt, 0, BAT, "2")
f1 = origin_for(FUSE, "1", (50.8, 55.88), 90)
place(FUSE, "F1", "5A blade", f1, 90, ref_pos=(f1[0], f1[1] - 3.81), val_pos=(f1[0], f1[1] + 3.81))
sw1 = origin_for(SW, "1", (66.04, 55.88))
place(SW, "SW1", "chassis switch", sw1, ref_pos=(sw1[0], sw1[1] - 5.08), val_pos=(sw1[0], sw1[1] + 5.08))
wire(bplus, (35.56, 55.88), pin_at(f1, 90, FUSE, "1"))
wire(pin_at(f1, 90, FUSE, "2"), pin_at(sw1, 0, SW, "1"))
vb = (96.52, 55.88)
wire(pin_at(sw1, 0, SW, "2"), (88.9, 55.88), vb)
label("VBAT", vb)
flag((88.9, 55.88))
text("VBAT: pack after fuse and switch, 10.5-12.6 V.\n5 A fuse: two stalled motors can exceed 3 A.", (30.48, 37.0))
gb = (35.56, 99.06)
wire(bminus, (35.56, 93.98), gb)
power("GND", gb)
flag((35.56, 93.98), 90)

u1 = (71.12, 116.84)
place(UBEC, "U1", "Adafruit UBEC #1385 5V 3A", u1)
tag(pin_at(u1, 0, UBEC, "1"), "L", "VBAT")
tag(pin_at(u1, 0, UBEC, "2"), "L", "GND")
o3 = pin_at(u1, 0, UBEC, "3")
wire(o3, (o3[0] + 2.54, o3[1]))
tag((o3[0] + 2.54, o3[1]), "R", "+5V", 2.54)
# no PWR_FLAG on +5V: A1's stock 5V pin is a power output and already drives the net
tag(pin_at(u1, 0, UBEC, "4"), "R", "GND")
text("+5V goes to the Pi USB-C through a USB-A to C cable\nwith the A end cut off: red = +5 V, black = GND.\n"
     "Meter it before plugging in.", (55.88, 133.35))

# --------------------------------------------------------------- Raspberry Pi
j1 = (139.7, 101.6)
place(PI, "J1", "Raspberry Pi 4", j1)
sig = {"8": "GPIO14_TXD", "10": "GPIO15_RXD", "12": "GPIO18_RESET"}
done = set()
for num, (px, py, pname, ptype) in PI.pins.items():
    p = pin_at(j1, 0, PI, num)
    if num in sig:
        tag(p, "L", sig[num])
    elif pname == "5V":
        if p not in done:
            tag(p, "U", "+5V")
            done.add(p)
    elif pname == "GND":
        if p not in done:
            tag(p, "D", "GND")
            done.add(p)
    elif p not in done:
        nc(p)
        done.add(p)
text("Power enters the Pi via USB-C (see U1).\nPi Camera v1 on the CSI ribbon.", (116.84, 148.59))

# ----------------------------------------------------------------- aLaMode
a1 = (226.06, 104.14)
place(ARD, "A1", "WyoLum aLaMode (Uno-compatible)", a1)
ala_nets = {"15": "GPIO14_TXD", "16": "GPIO15_RXD", "3": "GPIO18_RESET", "19": "D4", "20": "D5", "21": "D6",
            "22": "D7", "23": "D8", "24": "D9", "27": "D12", "9": "PACK_SENSE", "10": "TRIG", "11": "ECHO"}
for num, (px, py, pname, ptype) in ARD.pins.items():
    p = pin_at(a1, 0, ARD, num)
    if ptype == "no_connect" or num in ("6", "7", "29"):
        continue
    side = "L" if px < 0 else "R"
    if num in ala_nets:
        tag(p, side, ala_nets[num])
    elif num == "5":
        tag(p, "U", "+5V")
    else:
        nc(p)
g6, g7, g29 = (pin_at(a1, 0, ARD, n) for n in ("6", "7", "29"))
wire(g29, g7)
junction(g6)
tag(g6, "D", "GND")
text("5V-LINK jumper ON: powered from the Pi.\nRESET is capacitor-coupled to GPIO18 on the aLaMode.\n"
     "Pi-aLaMode and aLaMode-shield links are stacked headers, not wires.", (196.85, 148.59))

# ------------------------------------------------------------ motor shield
u2 = (325.12, 88.9)
place(SHIELD, "U2", "Adafruit Motor Shield v1, PWR jumper REMOVED", u2,
      val_pos=(u2[0] - 20.32, u2[1] + 64.77), val_just="left top")
dnet = {"1": "D4", "2": "D5", "3": "D6", "4": "D7", "5": "D8", "6": "D9", "7": "D12"}
for n, nm in dnet.items():
    tag(pin_at(u2, 0, SHIELD, n), "L", nm)
tag(pin_at(u2, 0, SHIELD, "8"), "U", "+5V")
tag(pin_at(u2, 0, SHIELD, "9"), "D", "GND")
tag(pin_at(u2, 0, SHIELD, "10"), "U", "VBAT")
tag(pin_at(u2, 0, SHIELD, "11"), "D", "GND")

# Servo
sv = origin_for(MSERVO, "2", (363.22, pin_at(u2, 0, SHIELD, "17")[1]))
place(MSERVO, "M3", "Parallax standard servo 4-6 V", sv, ref_pos=(sv[0] - 7.62, sv[1] - 7.62), ref_just="left")
s_s, s_p, s_n = (pin_at(u2, 0, SHIELD, n) for n in ("16", "17", "18"))
v_s, v_p, v_n = (pin_at(sv, 0, MSERVO, n) for n in ("1", "2", "3"))
jx = s_s[0] + 7.62
wire(s_s, (jx, s_s[1]), (jx, v_s[1]), v_s)
wire(s_p, v_p)
wire(s_n, (jx, s_n[1]), (jx, v_n[1]), v_n)
label("+5V", (s_p[0] + 1.27, s_p[1]))
label("GND", (s_n[0] + 1.27, s_n[1]))

# Motors
for ref, val, a, b in (("M1", "left TT motor", "12", "13"), ("M2", "right TT motor", "14", "15")):
    pa, pb = pin_at(u2, 0, SHIELD, a), pin_at(u2, 0, SHIELD, b)
    m = origin_for(MDC, "1", (370.84, pa[1]))
    place(MDC, ref, val, m)
    wire(pa, pin_at(m, 0, MDC, "1"))
    wire(pb, pin_at(m, 0, MDC, "2"))

# ------------------------------------------------------ divider + HC-SR04
r1 = (60.96, 190.5)
place(RES, "R1", "10k", r1, ref_pos=(r1[0] + 2.54, r1[1] - 1.27), val_pos=(r1[0] + 2.54, r1[1] + 1.27), ref_just="left", val_just="left")
tag(pin_at(r1, 0, RES, "1"), "U", "VBAT")
r2 = (60.96, 205.74)
place(RES, "R2", "3.3k", r2, ref_pos=(r2[0] + 2.54, r2[1] - 1.27), val_pos=(r2[0] + 2.54, r2[1] + 1.27), ref_just="left", val_just="left")
mid = pin_at(r1, 0, RES, "2")
low = pin_at(r2, 0, RES, "1")
wire(mid, low)
jy = (mid[1] + low[1]) / 2
junction((60.96, jy))
wire((60.96, jy), (71.12, jy))
label("PACK_SENSE", (71.12, jy))
tag(pin_at(r2, 0, RES, "2"), "D", "GND")
text("Pack voltage divider to A0.", (50.8, 172.72))

u3 = (162.56, 205.74)
place(HCSR04, "U3", "HC-SR04", u3)
tag(pin_at(u3, 0, HCSR04, "1"), "U", "+5V")
tag(pin_at(u3, 0, HCSR04, "2"), "U", "TRIG")
tag(pin_at(u3, 0, HCSR04, "3"), "U", "ECHO")
tag(pin_at(u3, 0, HCSR04, "4"), "D", "GND")
text("HC-SR04 VCC comes from the A3-A5 header 5 V row\n(the A0-A2 row is 3.3 V).", (127.0, 236.22))

text("Notes\n"
     "VBAT = pack after fuse and switch, 10.5-12.6 V.\n"
     "All grounds are common: run EXT_PWR GND and buck IN- straight\nto the pack negative in heavy wire.\n"
     "Servo 1 header power is the shield's 5 V logic rail, not EXT_PWR.\n"
     "5 A fuse because two stalled motors can exceed 3 A.", (215.9, 190.5))

# ------------------------------------------------------------------- output
sch = (f'(kicad_sch\n\t(version 20250114)\n\t(generator "eeschema")\n\t(generator_version "9.0")\n'
       f'\t(uuid "{ROOT}")\n\t(paper "A3")\n\t(title_block\n\t\t(title "rover-cam wiring")\n'
       f'\t\t(date "2026-11-24")\n\t\t(rev "A")\n\t)\n\t(lib_symbols\n'
       + "\n".join(s.embedded() for s in ALL_SYMS) + "\n\t)\n"
       + "\n".join(out) +
       '\n\t(sheet_instances\n\t\t(path "/"\n\t\t\t(page "1")\n\t\t)\n\t)\n\t(embedded_fonts no)\n)\n')
(HERE / "rover-cam.kicad_sch").write_text(sch)

lib = ('(kicad_symbol_lib\n\t(version 20251024)\n\t(generator "kicad_symbol_editor")\n'
       '\t(generator_version "10.0")\n' + "\n".join(s.text for s in (UBEC, SHIELD, HCSR04)) + "\n)\n")
(HERE / "rover-cam.kicad_sym").write_text(lib)
(HERE / "sym-lib-table").write_text(
    '(sym_lib_table\n\t(version 7)\n\t(lib (name "rover-cam")(type "KiCad")'
    '(uri "${KIPRJMOD}/rover-cam.kicad_sym")(options "")(descr "rover-cam custom symbols"))\n)\n')
if not (HERE / "rover-cam.kicad_pro").exists():
    (HERE / "rover-cam.kicad_pro").write_text(
        '{\n  "meta": {\n    "filename": "rover-cam.kicad_pro",\n    "version": 3\n  },\n'
        '  "sheets": [\n    ["' + ROOT + '", "Root"]\n  ]\n}\n')
subprocess.run([KICAD_CLI, "sch", "upgrade", str(HERE / "rover-cam.kicad_sch")], check=False)
