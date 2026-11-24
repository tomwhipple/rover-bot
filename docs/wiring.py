"""Draw docs/wiring.svg, the rover wiring schematic.

    uv run --with schemdraw --with matplotlib docs/wiring.py [--png out.png]
"""
import sys
from pathlib import Path

import schemdraw
import schemdraw.elements as elm

OUT = Path(__file__).with_name("wiring.svg")
png = sys.argv[sys.argv.index("--png") + 1] if "--png" in sys.argv else None
if png:
    schemdraw.use("matplotlib")

P = elm.IcPin
d = schemdraw.Drawing(show=False)
d.config(fontsize=11, unit=2.5)
add = d.add


def ground(at):
    return add(elm.Ground().theta(0).at(at))


def vbat(at):
    """Net flag for the fused, switched pack voltage."""
    return add(elm.Vdd().theta(0).at(at).label("VBAT"))


def ic(pins, at, anchor, **kw):
    """Side pins are given top to bottom (schemdraw lists them bottom up)."""
    sides = [p for p in pins if p.side in ("left", "right")][::-1]
    pins = sides + [p for p in pins if p.side not in ("left", "right")]
    w, h = kw.pop("w"), kw.pop("h", None)
    if h is None:
        n = max(sum(p.side == s for p in pins) for s in ("left", "right"))
        h = (n - 1) * kw.get("pinspacing", 1.0) + 2 * kw.get("edgepadH", 0.5)
    kw["size"] = (w, h)
    return add(elm.Ic(pins=pins, **kw).theta(0).anchor(anchor).at(at))


def wire(a, b, **kw):
    return add(elm.Line(**kw).at(a).to(b))


# --- Power: 3S pack -> fuse -> switch -> VBAT -------------------------------
batt = add(elm.Battery().up().at((0, 0)).reverse()
           .label("3S LiPo\n11.1 V nom\n12.6 V full\nXT60", loc="bottom", ofst=0.4))
ground(batt.start)
top = add(elm.Line().up(0.75).at(batt.end))
add(elm.Fuse().right().at(top.end).label("5 A blade fuse"))
add(elm.Switch().right().label("chassis\nswitch"))
vb = add(elm.Dot())
vbat(vb.center)

# --- 5 V buck -> Pi USB-C ----------------------------------------------------
ubec = ic([P(name="IN+", side="left", anchorname="inp"),
           P(name="IN−", side="left", anchorname="inn"),
           P(name="+5V", side="right", anchorname="out"),
           P(name="GND", side="right", anchorname="gnd")],
          at=(vb.center[0] + 1.5, vb.center[1]), anchor="inp",
          pinspacing=1.0, edgepadH=0.5, w=5.2,
          label="5 V 3 A buck\nAdafruit UBEC\n#1385")
wire(vb.center, ubec.inp)
g = add(elm.Line().left(0.75).at(ubec.inn))
ground(g.end)

pi = ic([P(name="USB-C VBUS", side="left", anchorname="vbus"),
         P(name="USB-C GND", side="left", anchorname="usbgnd"),
         P(name="CSI", side="top", anchorname="csi"),
         P(name="2/4  5V", side="right", anchorname="h5v"),
         P(name="6  GND", side="right", anchorname="hgnd"),
         P(name="8  TXD GPIO14", side="right", anchorname="htx"),
         P(name="10  RXD GPIO15", side="right", anchorname="hrx"),
         P(name="12  GPIO18", side="right", anchorname="hrst")],
        at=(ubec.out[0] + 4.5, ubec.out[1]), anchor="vbus",
        pinspacing=1.0, edgepadH=0.8, w=10.5,
        label="Raspberry Pi 4\n(GPIO pin numbers)")
wire(ubec.out, pi.vbus)
wire(ubec.gnd, pi.usbgnd)
add(elm.Label().at(((ubec.out[0] + pi.vbus[0]) / 2, ubec.out[1] + 0.9))
    .label("USB-A→C cable,\nA end cut off", fontsize=9))

cam = add(elm.Line().up(1.5).at(pi.csi).label("ribbon", loc="bottom", fontsize=9))
add(elm.Ic(pins=[P(name="", side="bottom", anchorname="c")], size=(3.2, 1.0),
           label="Pi Camera v1").theta(0).anchor("c").at(cam.end))

# --- aLaMode, plugged onto Pi GPIO pins 1-26 --------------------------------
ala = ic([P(name="5V (5V-LINK ON)", side="left", anchorname="l5v"),
          P(name="GND", side="left", anchorname="lgnd"),
          P(name="D0 RXD", side="left", anchorname="lrx"),
          P(name="D1 TXD", side="left", anchorname="ltx"),
          P(name="RESET (C-coupled)", side="left", anchorname="lrst"),
          P(name="D4", side="right", anchorname="d4"),
          P(name="D5", side="right", anchorname="d5"),
          P(name="D6", side="right", anchorname="d6"),
          P(name="D7", side="right", anchorname="d7"),
          P(name="D8", side="right", anchorname="d8"),
          P(name="D9", side="right", anchorname="d9"),
          P(name="D12", side="right", anchorname="d12"),
          P(name="5V", side="right", anchorname="r5v"),
          P(name="GND", side="right", anchorname="rgnd"),
          P(name="A0", side="bottom", anchorname="a0"),
          P(name="A1", side="bottom", anchorname="a1"),
          P(name="A2", side="bottom", anchorname="a2"),
          P(name="5V*", side="bottom", anchorname="b5v"),
          P(name="GND", side="bottom", anchorname="bgnd")],
         at=(pi.h5v[0] + 3.0, pi.h5v[1]), anchor="l5v",
         pinspacing=1.0, edgepadH=1.0, w=9.0,
         label="WyoLum aLaMode\nATmega328P, Optiboot")
for a, b in (("h5v", "l5v"), ("hgnd", "lgnd"), ("htx", "lrx"),
             ("hrx", "ltx"), ("hrst", "lrst")):
    wire(getattr(pi, a), getattr(ala, b))

# --- Motor shield v1, stacked on the aLaMode's Arduino headers --------------
sh = ic([P(name="D4 latch clock", side="left", anchorname="d4"),
         P(name="D5 M4 PWM", side="left", anchorname="d5"),
         P(name="D6 M3 PWM", side="left", anchorname="d6"),
         P(name="D7 latch enable", side="left", anchorname="d7"),
         P(name="D8 latch data", side="left", anchorname="d8"),
         P(name="D9 servo 1", side="left", anchorname="d9"),
         P(name="D12 latch", side="left", anchorname="d12"),
         P(name="5V logic", side="left", anchorname="l5v"),
         P(name="GND", side="left", anchorname="lgnd"),
         P(name="EXT_PWR +M", side="top", anchorname="extp"),
         P(name="EXT_PWR GND", side="bottom", anchorname="extn"),
         P(name="SERVO 1  S", side="right", anchorname="ss", slot="3/9"),
         P(name="SERVO 1  +", side="right", anchorname="sp", slot="2/9"),
         P(name="SERVO 1  −", side="right", anchorname="sn", slot="1/9"),
         P(name="M3", side="right", anchorname="m3a", slot="5/9"),
         P(name="M3", side="right", anchorname="m3b", slot="6/9"),
         P(name="M4", side="right", anchorname="m4a", slot="8/9"),
         P(name="M4", side="right", anchorname="m4b", slot="9/9")],
        at=(ala.d4[0] + 2.5, ala.d4[1]), anchor="d4",
        pinspacing=1.0, edgepadH=1.2, w=10.0,
        label="Adafruit Motor Shield v1\nPWR jumper REMOVED")
for n in ("d4", "d5", "d6", "d7", "d8", "d9", "d12"):
    wire(getattr(ala, n), getattr(sh, n))
wire(ala.r5v, sh.l5v)
wire(ala.rgnd, sh.lgnd)
up = add(elm.Line().up(0.75).at(sh.extp))
vbat(up.end)
dn = add(elm.Line().down(0.75).at(sh.extn))
ground(dn.end)

# Motors: one per pair of terminals
for a, b, name in (("m3a", "m3b", "left\nTT motor"), ("m4a", "m4b", "right\nTT motor")):
    pa, pb = getattr(sh, a), getattr(sh, b)
    x = pa[0] + 1.5
    wire(pa, (x, pa[1]))
    wire(pb, (x, pb[1]))
    add(elm.Motor().endpoints((x, pa[1]), (x, pb[1])).label(name, loc="bottom", ofst=0.3))

servo = ic([P(name="signal", side="left", anchorname="s"),
            P(name="+", side="left", anchorname="p"),
            P(name="−", side="left", anchorname="n")],
           at=(sh.ss[0] + 2.0, sh.ss[1]), anchor="s",
           pinspacing=sh.ss[1] - sh.sp[1], edgepadH=0.4, w=3.6,
           label="pan servo\nParallax std\n4–6 V")
for a, b in (("ss", "s"), ("sp", "p"), ("sn", "n")):
    wire(getattr(sh, a), getattr(servo, b))

# --- Pack divider on A0 and the ultrasonic sensor ---------------------------
r1top = (ala.a0[0] - 2.5, ala.a0[1] - 1.0)
vbat(r1top)
add(elm.Resistor().down().at(r1top).label("10 kΩ", loc="bottom"))
mid = add(elm.Dot())
r2 = add(elm.Resistor().down().label("3.3 kΩ", loc="bottom"))
ground(r2.end)
add(elm.Wire("|-").at(ala.a0).to(mid.center))

us = ic([P(name="Trig", side="top", anchorname="trig"),
         P(name="Echo", side="top", anchorname="echo"),
         P(name="VCC", side="top", anchorname="vcc"),
         P(name="GND", side="top", anchorname="gnd")],
        at=(ala.a1[0], ala.a1[1] - 4.0), anchor="trig",
        pinspacing=1.0, w=4.4, h=2.4,
        label="\nHC-SR04\nfacing forward", lblloc="center")
for a, b in (("a1", "trig"), ("a2", "echo"), ("b5v", "vcc"), ("bgnd", "gnd")):
    wire(getattr(ala, a), getattr(us, b))

add(elm.Label().at((sh.extn[0] - 1.0, us.trig[1] - 2.2)).label(
    "VBAT: pack after fuse and switch, 10.5–12.6 V. All grounds are common;\n"
    "run EXT_PWR GND and buck IN− straight to the pack negative in heavy wire.\n"
    "Cut USB cable: red = +5 V, black = GND. Meter it before plugging in.\n"
    "5V*: aLaMode A3–A5 header 5 V row (the A0–A2 row is 3.3 V).\n"
    "Pi–aLaMode and aLaMode–shield links are the stacked headers, not wires.\n"
    "Servo 1 header power is the shield's 5 V logic rail, not EXT_PWR.",
    halign="left", fontsize=9))

d.save(png if png else str(OUT), **({"dpi": 110} if png else {}))
