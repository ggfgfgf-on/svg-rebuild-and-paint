"""Dusk lighthouse, 1200x900, thick-paint study.
Built for the svg-rebuild-and-paint deterministic thick-paint engine.
"""
from __future__ import annotations
import json, math, sys, time
from pathlib import Path

SKILL = Path(r"D:\deepseek-harness-dsh-v0.2.2rc1\.dsh-home\skills\svg-rebuild-and-paint")
sys.path.insert(0, str(SKILL / "scripts"))
import cv2
import numpy as np
import plan_io
import shape_ops

OUT = Path(r"C:\Users\chesand\AppData\Local\Temp\cold-v13\out")
OUT.mkdir(parents=True, exist_ok=True)
W, H = 1200, 900
HOR = 598.0
SUNX, SUNY = 344.0, 548.0
LX, TOWER_TOP, TOWER_BASE = 884.0, 342.0, 654.0
LIGHTX, LIGHTY = 884.0, 286.0
rng = np.random.default_rng(20260613)

doc = {"canvas": [W, H], "layers": [], "strokes": []}

def c8(c):
    return [int(np.clip(round(float(v)), 0, 255)) for v in c]

def jc(col, amount=5, warm=0):
    return c8(np.asarray(col, np.float32) + rng.uniform(-amount, amount, 3) + np.array([warm*0.5, warm*0.25, -warm*0.2], np.float32))

def add(layer, pts, size, color, opacity=1.0, preset="round_hard", hardness=0.72, pressure=1.0, tag=None):
    ps = []
    for p in pts:
        x, y = float(p[0]), float(p[1])
        pr = float(p[2]) if len(p) > 2 else pressure
        ps.append([round(x, 2), round(y, 2), round(float(np.clip(pr, 0.0, 1.0)), 3)])
    st = {"layer": layer, "preset": preset, "size": round(float(size), 2),
          "opacity": round(float(np.clip(opacity, 0, 1)), 3),
          "color": c8(color), "hardness": round(float(np.clip(hardness, 0, 1)), 3),
          "points": ps}
    if tag:
        st["tag"] = tag
    doc["strokes"].append(st)

def poly(layer, pts, color, color2=None, size=18, spacing=None, opacity=1.0,
         preset="round_hard", hardness=0.72, jitter=0.28, edge=True,
         gradient_angle=0.0, seed=0, holes=None, pressure=1.0):
    shape_ops.fill_polygon(doc, layer, pts, color=c8(color), color2=(c8(color2) if color2 is not None else None),
        size=float(size), spacing=(float(spacing) if spacing else None),
        opacity=float(opacity), preset=preset, jitter=float(jitter),
        hardness=float(hardness), edge=bool(edge), gradient_angle=float(gradient_angle),
        seed=int(seed), holes=holes, pressure=float(pressure))

def tapered(layer, d, color, size, size_end, steps=22, opacity=1.0,
            preset="round_hard", hardness=0.7, copies=1, spread=0.0, seed=0,
            pressure=1.0, pressure_end=None):
    shape_ops.tapered_stroke(doc, layer, d, color=c8(color), size=float(size),
        size_end=float(size_end), steps=int(steps), opacity=float(opacity),
        preset=preset, hardness=float(hardness), copies=int(copies), spread=float(spread),
        seed=int(seed), pressure=float(pressure),
        pressure_end=(float(pressure_end) if pressure_end is not None else None))

def curved(x, y, length, angle, bend=0.0, lift=0.0):
    dx, dy = math.cos(angle)*length*0.5, math.sin(angle)*length*0.5
    nx, ny = -math.sin(angle), math.cos(angle)
    return [(x-dx, y-dy), (x+dx, y+dy)]

def smoothstep(a, b, x):
    t = np.clip((x-a)/(b-a+1e-9), 0.0, 1.0)
    return t*t*(3.0-2.0*t)

def lerp(a, b, t):
    return a + (b-a)*t

def fbm(shape, octaves=5, fx=1.0, fy=1.0, base=4, seed=1):
    h, w = shape
    gen = np.random.default_rng(seed)
    out = np.zeros((h, w), np.float32)
    amp, total = 1.0, 0.0
    for o in range(octaves):
        gx = max(2, int(base*(2**o)*fx))
        gy = max(2, int(base*(2**o)*fy))
        out += cv2.resize(gen.random((gy, gx)).astype(np.float32), (w, h), interpolation=cv2.INTER_CUBIC)*amp
        total += amp
        amp *= 0.52
    out /= total
    return (out-out.min())/(np.ptp(out)+1e-6)

def ellipse_field(cx, cy, rx, ry, ang=0.0, power=1.55):
    ca, sa = math.cos(math.radians(ang)), math.sin(math.radians(ang))
    xr = (xx-cx)*ca + (yy-cy)*sa
    yr = -(xx-cx)*sa + (yy-cy)*ca
    return np.exp(-power*((xr/rx)**2 + (yr/ry)**2))

yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)

# ------------- analytic fields -------------
tn = np.clip(yy/HOR, 0, 1)
stops = [0.0, 0.20, 0.42, 0.66, 0.84, 1.0]
cols = np.array([[25,25,61],[48,43,88],[92,65,110],[166,91,112],[229,133,103],[249,184,123]], np.float32)
sky = np.empty((H,W,3), np.float32)
for c in range(3):
    sky[...,c] = np.interp(tn, stops, cols[:,c])
d_sun = np.sqrt((xx-SUNX)**2 + ((yy-SUNY)*0.92)**2)
sky += np.exp(-(d_sun/360.0)**2)[...,None]*np.array([54,28,10],np.float32)
sky += np.exp(-(d_sun/138.0)**2)[...,None]*np.array([70,40,13],np.float32)
sky += np.exp(-((xx-835)/520.0)**2 - ((yy-300)/390.0)**2)[...,None]*np.array([-10,-6,12],np.float32)
sky = np.clip(sky, 0, 255)

# cloud envelope: three major masses + a long diagonal veil
c1 = ellipse_field(315, 430, 325, 93, -7, 1.6)
c2 = ellipse_field(936, 204, 318, 125, 8, 1.55)
c3 = ellipse_field(570, 516, 265, 62, -2, 1.8)
c4 = ellipse_field(1035, 410, 235, 76, 14, 1.75)
streak = np.exp(-((yy-(294-0.095*(xx-430)+24*np.sin(xx/175.0)))/52.0)**2) * smoothstep(310, 500, xx) * (1-smoothstep(1080, 1200, xx))
env = np.maximum.reduce([c1*0.98, c2*0.92, c3*0.68, c4*0.70, streak*0.62])
env *= smoothstep(28, 125, yy) * (1-smoothstep(565, 595, yy))
n1 = fbm((H,W), 5, fx=0.45, fy=1.8, base=4, seed=11)
n2 = fbm((H,W), 4, fx=1.9, fy=2.6, base=5, seed=19)
dens = env*(0.44 + 0.78*n1) + 0.10*n2 - 0.06
dens = cv2.GaussianBlur(dens.astype(np.float32), (0,0), 2.2)
dens = np.clip(dens, 0, 1.4)
cloudmask = smoothstep(0.34, 0.47, dens)

# cloud lighting
db = cv2.GaussianBlur(dens, (0,0), 6.0)
gy, gx = np.gradient(db)
mag = np.sqrt(gx*gx + gy*gy) + 0.002
nx, ny = -gx/mag, -gy/mag
lv = np.array([SUNX-760.0, SUNY-260.0], np.float32); lv /= np.linalg.norm(lv)
lit = np.clip(0.52 + 0.58*(nx*lv[0] + ny*lv[1]), 0.02, 1.0)
lit = 0.62*lit + 0.38*np.clip(1.0 - (yy-180)/500.0, 0, 1)
warm = np.clip(0.34 + 0.78*np.exp(-d_sun/460.0) + 0.10*(1-(yy/HOR)), 0, 1)
c_shadow = np.array([48,44,82], np.float32)
c_mid = np.array([117,83,115], np.float32)
c_warm = np.array([249,166,135], np.float32)
c_gold = np.array([255,210,153], np.float32)
cloud = c_shadow[None,None,:]*(1-lit[...,None]) + c_mid[None,None,:]*(lit[...,None]*(1-warm[...,None]))
cloud += c_warm[None,None,:]*np.clip(lit[...,None]*warm[...,None]*1.25,0,1)
cloud += c_gold[None,None,:]*np.clip((lit[...,None]-0.62)*1.8,0,1)*warm[...,None]*0.55
cloud = np.clip(cloud,0,255)

# sea field
tt = np.clip((yy-HOR)/(H-HOR), 0, 1)
sea = np.empty((H,W,3), np.float32)
sea0 = np.array([121,101,113], np.float32)
sea1 = np.array([25,61,76], np.float32)
sea2 = np.array([7,23,42], np.float32)
for c in range(3):
    a = np.interp(tt, [0,0.34,1], [sea0[c],sea1[c],sea2[c]])
    sea[...,c] = a
band_phase = tt*31.0 + 0.004*xx + 0.7*np.sin(xx/120.0)
sea += (np.sin(band_phase*math.pi*2.0)*5.0*(1.0-0.4*tt))[...,None]*np.array([0.4,0.55,0.8],np.float32)
refl_w = 35.0 + 220.0*tt
refl = np.exp(-((xx-SUNX)/refl_w)**2) * (0.28 + 0.72*np.clip(1.0-tt*1.12,0,1))
sparkle = 0.45 + 0.55*np.clip(np.sin(tt*120.0 + xx*0.035 + 1.7*np.sin(xx/47.0)),0,1)
sea += (refl*sparkle)[...,None]*np.array([158,97,48],np.float32)
sea += np.exp(-((yy-HOR)/24.0)**2)[...,None]*np.array([42,29,16],np.float32)
sea = np.clip(sea,0,255)

def samp(arr, x, y):
    xi = int(np.clip(x, 0, W-1)); yi = int(np.clip(y, 0, H-1))
    return arr[yi, xi]

# ------------- opaque underpainting -------------
poly("under", [(0,0),(W,0),(W,H),(0,H)], (24,25,51), (12,20,38), size=74,
     spacing=14, opacity=1.0, preset="sponge", edge=False, jitter=0.5, seed=3)
for y in range(-40, H+40, 30):
    local = samp(sky, W*0.5, min(y, HOR-2)) if y < HOR else samp(sea, W*0.5, max(y,HOR+2))
    for x in range(-90, W+60, 210):
        jx, jy = x+rng.uniform(-15,15), y+rng.uniform(-7,7)
        add("under", curved(jx,jy,rng.uniform(255,340), rng.uniform(-0.035,0.035)),
            rng.uniform(38,57), jc(local, 7), 1.0,
            rng.choice(["round_hard","sponge"]), 0.7)

# ------------- sky: broad atmosphere and swirling brushwork -------------
for y in range(-24, int(HOR)+12, 16):
    for x in range(-38, W+38, 27):
        px, py = x+rng.uniform(-11,11), y+rng.uniform(-6,6)
        c = samp(sky, px, py).copy()
        c += rng.uniform(-5,5,3)
        ang = -0.015 + 0.11*np.sin(py/95.0) + 0.05*np.sin(px/170.0) + rng.uniform(-0.035,0.035)
        ln = rng.uniform(42, 118)
        add("sky", curved(px,py,ln,ang,0), rng.uniform(12,25), jc(c,4),
            rng.uniform(0.58,0.86), rng.choice(["round_soft","sponge","dry"]), rng.uniform(0.45,0.75))
# long translucent atmospheric bands
for i in range(115):
    py = rng.uniform(25, HOR-28)
    px = rng.uniform(-80, W+40)
    c = samp(sky, px, py).copy()
    c = np.clip(c + rng.uniform(-9,13), 0, 255)
    add("sky", [(px-180,py+rng.uniform(-20,20)), (px+120,py+rng.uniform(-12,12)), (px+390,py+rng.uniform(-3,3))],
        rng.uniform(3,9), c, rng.uniform(0.10,0.23), rng.choice(["dry","bristle"]), rng.uniform(0.4,0.8))

# sun core, painted before clouds so edges can cross it
def ellipse_pts(cx,cy,rx,ry,n=48,ang=0.0):
    a = np.linspace(0, math.tau, n, endpoint=False)
    ca,sa = math.cos(math.radians(ang)), math.sin(math.radians(ang))
    x,y = rx*np.cos(a), ry*np.sin(a)
    return [(cx+x*ca-y*sa, cy+x*sa+y*ca) for x,y in zip(x,y)]
poly("sun_core", ellipse_pts(SUNX,SUNY,34,28), (255,214,153), (255,164,105), size=12,
     spacing=7, opacity=0.95, preset="round_soft", hardness=0.32, edge=True, jitter=0.22, seed=21)
poly("sun_core", ellipse_pts(SUNX,SUNY,18,15,36), (255,242,204), (255,199,135), size=8,
     spacing=4, opacity=1.0, preset="round_hard", hardness=0.7, edge=True, jitter=0.15, seed=22)

# ------------- clouds: layered directional paint -------------
for pass_i in range(3):
    step = 11 + pass_i*4
    for y in range(48, 590, step):
        for x in range(-25, W+30, step):
            px, py = x+rng.uniform(-step*.65,step*.65), y+rng.uniform(-step*.55,step*.55)
            xi, yi = int(np.clip(px,0,W-1)), int(np.clip(py,0,H-1))
            if cloudmask[yi,xi] < 0.24 and rng.random() > 0.17:
                continue
            if dens[yi,xi] < 0.31:
                continue
            c = samp(cloud,px,py).copy()
            # paint in denser, chunkier passages on the first pass
            if pass_i == 0:
                c = c*0.86 + samp(sky,px,py)*0.14
                sz = rng.uniform(16,31); op = rng.uniform(0.72,0.93)
                pr = rng.choice(["sponge","round_hard","bristle"])
            elif pass_i == 1:
                c = np.clip(c*1.06 + rng.uniform(-5,7,3),0,255)
                sz = rng.uniform(8,17); op = rng.uniform(0.62,0.88)
                pr = rng.choice(["sponge","dry","round_soft"])
            else:
                c = np.clip(c*1.09 + np.array([4,2,-1]),0,255)
                sz = rng.uniform(4,9); op = rng.uniform(0.45,0.72)
                pr = rng.choice(["dry","bristle","chalk"])
            xi2, yi2 = int(np.clip(px+3,0,W-1)), int(np.clip(py+3,0,H-1))
            ang = math.atan2(gy[yi2,xi2],gx[yi2,xi2]) + math.pi/2 + rng.uniform(-0.22,0.22)
            ln = rng.uniform(13,44)*(1.2 if pass_i==0 else 0.8)
            add("cloud", curved(px,py,ln,ang), sz, jc(c,6),
                op, pr, rng.uniform(0.44,0.82))

# warm rim on cloud undersides
for i in range(1300):
    px = rng.uniform(20,1180); py = rng.uniform(150,575)
    xi, yi = int(px), int(py)
    if cloudmask[yi,xi] < 0.55 or lit[yi,xi] < 0.64:
        continue
    edge = smoothstep(0.48,0.68,dens[yi,xi]) * (1-smoothstep(0.85,1.05,dens[yi,xi]))
    if edge < 0.12 and rng.random() > 0.28:
        continue
    ang = math.atan2(gy[yi,xi],gx[yi,xi]) + math.pi/2 + rng.uniform(-0.45,0.45)
    c = samp(cloud,px,py)*0.35 + np.array([255,196,145],np.float32)*0.65
    add("cloud_rim", curved(px,py,rng.uniform(9,33),ang), rng.uniform(2.5,7.5),
        jc(c,5), rng.uniform(0.30,0.66), rng.choice(["dry","bristle","round_soft"]), rng.uniform(0.5,0.9))

# feathery cloud fringes, dragged with a dry brush
for i in range(1500):
    px = rng.uniform(20,1180); py = rng.uniform(80,590)
    xi, yi = int(px), int(py)
    dd = dens[yi,xi]
    if dd < 0.24 or dd > 0.52:
        continue
    ang = math.atan2(gy[yi,xi],gx[yi,xi]) + math.pi/2 + rng.uniform(-0.34,0.34)
    ln = rng.uniform(18,92)
    c = samp(cloud,px,py)*0.55 + samp(sky,px,py)*0.45
    c = np.clip(c + rng.uniform(-8,10,3),0,255)
    add("cloud_wisp", curved(px,py,ln,ang), rng.uniform(1.4,5.0), jc(c,6),
        rng.uniform(0.10,0.42), rng.choice(["dry","dry","chalk","bristle"]), rng.uniform(0.3,0.75))

# visible dry-brush ridges in the open sky, skipping cloud bodies
for i in range(1050):
    px = rng.uniform(-30,1230); py = rng.uniform(20,588)
    xi, yi = int(np.clip(px,0,W-1)), int(np.clip(py,0,H-1))
    if cloudmask[yi,xi] > 0.18:
        continue
    ang = 0.02 + 0.10*np.sin(py/92.0 + px/310.0) + rng.uniform(-0.06,0.06)
    ln = rng.uniform(35,160)
    c = samp(sky,px,py) + rng.uniform(-18,18,3)
    add("sky_texture", [(px-ln*.5,py+rng.uniform(-2,2)),(px,py+rng.uniform(-1.5,1.5)),(px+ln*.5,py+rng.uniform(-2,2))],
        rng.uniform(3.0,11.0), jc(c,4), rng.uniform(0.08,0.26),
        rng.choice(["dry","bristle","chalk","sponge"]), rng.uniform(0.35,0.75))

# sky glow bloom after clouds
for rad, col, op, cnt in [(115,(255,169,109),0.075,3),(55,(255,201,135),0.10,3),(25,(255,235,183),0.16,2)]:
    poly("sun_glow", ellipse_pts(SUNX,SUNY,rad,rad*0.86,56), col, None, size=max(12,rad*0.35),
         spacing=max(7,rad*0.20), opacity=op, preset="airbrush", hardness=0.15,
         edge=False, jitter=0.18, seed=31+rad)
# translucent horizon bands
for i in range(95):
    py = HOR-rng.uniform(8,65); px = rng.uniform(-40,W+30)
    add("sun_glow", [(px-150,py+rng.uniform(-5,5)),(px+140,py+rng.uniform(-3,3)),(px+330,py+rng.uniform(-2,2))],
        rng.uniform(8,20), (255,173,116), rng.uniform(0.018,0.06), "airbrush", 0.2)

# ------------- sea -------------
for y in range(int(HOR)-2, H+16, 11):
    tt0 = np.clip((y-HOR)/(H-HOR),0,1)
    for x in range(-55, W+55, 21+int(8*tt0)):
        px, py = x+rng.uniform(-16,16), y+rng.uniform(-5,5)
        c = samp(sea,px,py).copy() + rng.uniform(-4,4,3)
        ln = rng.uniform(35,80) + 105*tt0*rng.uniform(0.45,1.2)
        ang = rng.uniform(-0.03,0.03) + 0.055*np.sin(px/55.0 + py/17.0)
        add("sea", curved(px,py,ln,ang), rng.uniform(7,14)+11*tt0, jc(c,4),
            rng.uniform(0.62,0.9), rng.choice(["round_hard","sponge","bristle"]), rng.uniform(0.5,0.8))

# warm reflection: short broken calligraphic marks, never a ladder
for i in range(1750):
    v = rng.random()**1.4
    py = HOR + 3 + v*275 + rng.uniform(-2.5,2.5)
    tt0 = (py-HOR)/300.0
    spread = 34 + 235*tt0
    # reflections break into bands and wedges, so x is not a continuous column
    wedge = 0.32 + 0.68*(0.5+0.5*math.sin(py*0.19 + tt0*9.0))
    px = SUNX + rng.normal(0, spread*0.31*(0.55+0.45*wedge))
    if rng.random() > np.exp(-((px-SUNX)/(spread*1.38))**2):
        continue
    if rng.random() > 0.34 + 0.50*(1-tt0):
        continue
    c = np.array([255,183,111],np.float32)*(1-0.42*tt0) + samp(sea,px,py)*0.33
    ln = rng.uniform(5,18) + 52*tt0*rng.uniform(0.25,1.0)
    add("sea_light", [(px-ln*0.5,py+rng.uniform(-1.5,1.5)),
                      (px+ln*0.50,py+rng.uniform(-1.8,1.8))],
        rng.uniform(1.2,3.0)+3.0*tt0, jc(c,9),
        rng.uniform(0.12,0.40), rng.choice(["round_soft","round_soft","dry","airbrush"]),
        rng.uniform(0.3,0.65))
# the hottest broken water immediately under the sun
for i in range(220):
    py = HOR + 3 + rng.uniform(0,50)
    px = SUNX + rng.normal(0, 22 + 40*(py-HOR)/50)
    ln = rng.uniform(4,16)
    add("sea_light", [(px-ln/2,py),(px+ln/2,py+rng.uniform(-1,1))], rng.uniform(1.0,2.6),
        (255,232,178), rng.uniform(0.12,0.38), "round_soft", 0.35)
# crisp wave crests
for i in range(900):
    py = HOR + 6 + (rng.random()**1.2)*285
    tt0 = (py-HOR)/300.0
    px = rng.uniform(-30,W+30)
    c = samp(sea,px,py)*0.68 + np.array([96,123,132],np.float32)*0.32
    ln = rng.uniform(18,70)+90*tt0
    add("sea_light", curved(px,py,ln,rng.uniform(-0.03,0.025)), rng.uniform(1.2,3.3)+2*tt0,
        jc(c,5), rng.uniform(0.18,0.48), "bristle", 0.65)
# soft luminous wash to bind the broken reflection together
for i in range(210):
    v = rng.random()**1.35
    py = HOR + 4 + v*265
    tt0 = (py-HOR)/300.0
    spread = 35 + 215*tt0
    px = SUNX + rng.normal(0, spread*0.34)
    if rng.random() > np.exp(-((px-SUNX)/(spread*1.45))**2):
        continue
    ln = rng.uniform(24,95)*(0.5+0.9*tt0)
    add("sea_light", [(px-ln*.5,py),(px+ln*.5,py+rng.uniform(-1,1))],
        rng.uniform(11,27)+14*tt0, (255,180,106), rng.uniform(0.014,0.040),
        "airbrush", 0.2)
# a few heavy dark swells in foreground
for y,col,ln in [(712,(14,59,74),190),(770,(10,45,61),260),(832,(7,33,51),340),(878,(5,26,43),400)]:
    for x in range(-100,W+100,int(ln*0.56)):
        add("sea", [(x,y+rng.uniform(-12,12)),(x+ln*0.5,y+rng.uniform(-7,7)),(x+ln,y+rng.uniform(-2,2))],
            rng.uniform(14,23), jc(col,5), rng.uniform(0.30,0.58), "round_soft", 0.45)
# dark water crosses the reflection and breaks the ladder
for i in range(64):
    py = rng.uniform(690,886)
    tt0=(py-HOR)/302.0
    px = SUNX + rng.normal(0, 95+150*tt0)
    ln = rng.uniform(40,120)+140*tt0
    add("sea_break", [(px-ln*.5,py+rng.uniform(-3,3)),(px,py+rng.uniform(-1,1)),(px+ln*.5,py+rng.uniform(-3,3))],
        rng.uniform(7,17)+9*tt0, jc((11,42,57),5), rng.uniform(0.10,0.28),
        rng.choice(["round_soft","airbrush"]), 0.40)
# horizon haze
for i in range(120):
    px = rng.uniform(-30,W+30)
    add("sea_haze", [(px-150,HOR+rng.uniform(-2,4)),(px+150,HOR+rng.uniform(-1,4))],
        rng.uniform(2,8), (246,170,123), rng.uniform(0.04,0.16), "airbrush", 0.22)

# ------------- cliff and rock masses -------------
# Dark angular headland. The rock is built as blocky planes; brush ridges follow
# the plane slopes so the surface reads as stone rather than straw.
cliff = [(690,900),(724,836),(716,792),(744,746),(768,706),(792,686),(805,664),
         (846,652),(878,643),(918,643),(954,648),(990,658),(1025,675),(1058,701),
         (1093,714),(1124,744),(1160,779),(1200,803),(1200,900)]
poly("cliff", cliff, (16,22,39), (63,48,54), size=24, spacing=6.2, opacity=1.0,
     preset="oil_bristle", hardness=0.8, edge=True, jitter=0.25,
     gradient_angle=90, seed=41)
# major planes: shadowed left, warm central mass, deep-blue right shoulder
poly("cliff", [(690,900),(724,836),(716,792),(744,746),(768,706),(792,686),(805,664),
               (829,656),(836,710),(806,762),(780,823),(757,900)],
     (12,18,34), (78,55,57), size=19, spacing=4.8, preset="rough_wall_with_impasto",
     edge=True, jitter=0.20, gradient_angle=315, seed=43)
poly("cliff", [(829,656),(858,648),(916,645),(956,656),(940,704),(912,756),(926,826),
               (908,900),(764,900),(786,820),(806,758),(836,710)],
     (41,35,43), (151,88,64), size=16, spacing=4.5, preset="oil_bristle",
     edge=True, jitter=0.18, gradient_angle=310, seed=45)
poly("cliff", [(956,656),(996,670),(1036,692),(1080,726),(1120,770),(1200,808),
               (1200,900),(908,900),(926,826),(912,756),(940,704)],
     (14,22,39), (72,52,57), size=18, spacing=4.9, preset="rough_wall_with_impasto",
     edge=True, jitter=0.22, gradient_angle=320, seed=47)
# the plateau under the tower, painted with a lit top plane and a dark front lip
poly("cliff", [(756,682),(792,660),(840,647),(918,644),(970,653),(1006,674),
               (987,692),(930,700),(864,700),(806,691),(772,686)],
     (47,41,43), (137,84,61), size=13, spacing=3.9, preset="oil_bristle",
     edge=True, jitter=0.18, gradient_angle=300, seed=49)
# broad planar brush ridges, oriented with the face
for i in range(560):
    py = rng.uniform(657, 898)
    px = rng.uniform(706, 1194)
    if px < 728 or (px < 800 and py < 704) or (px > 1120 and py < 744):
        continue
    if py < 712:
        ang = -0.12 + rng.uniform(-0.16,0.11)
    elif px < 842:
        ang = 0.54 + rng.uniform(-0.16,0.16)
    elif px < 982:
        ang = 0.15 + rng.uniform(-0.13,0.13)
    else:
        ang = 0.42 + rng.uniform(-0.14,0.14)
    ln = rng.uniform(32,150)
    litface = 0.5*np.exp(-((px-848)/260.0)**2) + 0.55*np.exp(-((py-665)/72.0)**2)
    if rng.random() < 0.22 + 0.62*litface:
        c = np.array([171,99,67],np.float32)*(0.45+0.55*rng.random()) + np.array([73,52,51],np.float32)*0.32
        op = rng.uniform(0.24,0.54)
    else:
        c = np.array([25,31,45],np.float32)*(0.55+0.5*rng.random()) + np.array([59,44,50],np.float32)*0.28
        op = rng.uniform(0.20,0.52)
    add("rock_detail", curved(px,py,ln,ang,0), rng.uniform(7,21)+8*max(0,1-(py-680)/220),
        jc(c,7), op, rng.choice(["oil_bristle","dry","sponge","round_soft"]), rng.uniform(0.5,0.9))
# exposed rim light, mostly along the left/top of the headland
for i in range(235):
    t = rng.random()
    px = 748 + t*265 + rng.uniform(-14,14)
    py = 681 - 39*math.sin(math.pi*np.clip(t,0,1)) + rng.uniform(-8,9)
    if py > 700: continue
    ang = -0.18 + 0.38*t + rng.uniform(-0.12,0.12)
    ln = rng.uniform(10,48)
    c = rng.choice([(185,114,73),(214,139,87),(139,91,66),(98,74,63)])
    add("rock_detail", curved(px,py,ln,ang), rng.uniform(1.8,5.4), jc(c,7),
        rng.uniform(0.24,0.64), rng.choice(["dry","bristle","round_soft"]), 0.6)
# deep seams between the major planes
fracture_paths = [
    "M 832 704 Q 854 752 838 822 Q 828 850 846 890",
    "M 920 704 Q 906 755 932 803 Q 953 845 934 894",
    "M 982 724 Q 1000 766 986 812 Q 972 850 998 892",
    "M 1084 760 Q 1101 807 1086 850 Q 1078 875 1099 899",
    "M 768 762 Q 790 803 776 850",
    "M 862 686 Q 870 724 858 745",
]
for i,d in enumerate(fracture_paths):
    tapered("rock_detail", d, (8,14,26), rng.uniform(4.0,7.5), 0.35, steps=18,
            opacity=rng.uniform(0.38,0.58), preset="round_soft", hardness=0.38)
    if i % 2 == 0:
        tapered("rock_detail", d, (92,61,54), rng.uniform(1.3,2.6), 0.25, steps=16,
                opacity=0.30, preset="dry", hardness=0.45)
# chisel-cut edges on the lit planes: short, broken, never long whiskers
plane_edges = [
    [(758,735),(806,704),(840,692)],
    [(800,805),(838,770),(878,754)],
    [(896,758),(938,738),(976,728)],
    [(918,842),(956,804),(992,782)],
    [(1006,768),(1054,748),(1090,735)],
    [(844,892),(884,858),(920,848)],
]
for edge in plane_edges:
    for j in range(len(edge)-1):
        ax,ay = edge[j]; bx,by = edge[j+1]
        for k in range(13):
            t = (k+rng.random())/13.0
            px,py = ax+(bx-ax)*t, ay+(by-ay)*t
            ang = math.atan2(by-ay,bx-ax) + rng.uniform(-0.08,0.08)
            add("rock_detail", curved(px,py,rng.uniform(8,28),ang), rng.uniform(2.2,6.2),
                jc(rng.choice([(196,126,80),(161,101,72),(108,76,61),(71,60,57)]),7),
                rng.uniform(0.30,0.68), rng.choice(["dry","bristle","round_soft"]), 0.6)
# broad chisel marks: a few opaque facet strokes instead of pasted blocks
rock_zones = [
    (708, 730, 824, 898, 0.70, (20,25,41), (68,49,54), 78),
    (786, 690, 968, 900, 0.13, (59,44,44), (164,96,66), 112),
    (928, 708, 1192, 900, 0.36, (20,26,42), (86,57,55), 82),
]
for z,(x0,y0,x1,y1,base_ang,dark,light,cnt) in enumerate(rock_zones):
    for i in range(cnt):
        px = rng.uniform(x0,x1); py = rng.uniform(y0,y1)
        if py < 718 and px > 980: continue
        ang = base_ang + rng.uniform(-0.16,0.16)
        ln = rng.uniform(34,145)
        is_lit = rng.random() < (0.53 if z==1 else 0.35)
        c = np.asarray(light if is_lit else dark,np.float32)
        c = c*(0.66+0.46*rng.random()) + np.array([34,28,31],np.float32)*0.28
        add("rock_detail", curved(px,py,ln,ang,0), rng.uniform(9,26), jc(c,7),
            rng.uniform(0.38,0.78), rng.choice(["oil_bristle","dry","sponge","round_soft"]),
            rng.uniform(0.48,0.92))
        if is_lit and rng.random() < 0.45:
            hx,hy = px-rng.uniform(2,7), py-rng.uniform(2,6)
            add("rock_detail", curved(hx,hy,ln*rng.uniform(0.45,0.86),ang,0),
                rng.uniform(1.6,4.6), jc(np.clip(c*1.25+14,0,255),6),
                rng.uniform(0.22,0.50), "dry", 0.6)
# sparse grass only on the very top rim; short blades keep the rock legible
for i in range(30):
    bx = rng.uniform(790,1018); by = rng.uniform(637,658)
    if bx > 850 and bx < 918 and by > 646: continue  # leave the foundation clear
    for k in range(int(rng.integers(3,6))):
        dx = rng.uniform(-10,10); hh = rng.uniform(6,23)
        d = f"M {bx+dx:.1f} {by:.1f} Q {bx+dx+rng.uniform(-5,5):.1f} {by-hh*.58:.1f} {bx+dx+rng.uniform(-9,9):.1f} {by-hh:.1f}"
        c = rng.choice([(39,48,33),(56,58,34),(80,68,37),(107,82,42)])
        tapered("grass", d, c, rng.uniform(1.3,2.7), rng.uniform(0.18,0.55), steps=5,
                opacity=rng.uniform(0.62,0.92), preset="liner", hardness=0.55)

# second structure pass: replace the plank-like underpaint with faceted stone
cliff2 = [(690,900),(718,842),(710,805),(730,766),(742,735),(764,708),(786,688),
          (802,666),(836,652),(878,643),(918,643),(958,650),(994,662),(1028,678),
          (1058,700),(1090,716),(1125,748),(1162,782),(1200,808),(1200,900)]
poly("cliff", cliff2, (13,19,35), (68,50,55), size=23, spacing=5.4, opacity=1.0,
     preset="rough_wall_with_impasto", hardness=0.82, edge=True, jitter=0.24,
     gradient_angle=90, seed=201)
# plateau / shelf
poly("cliff", [(752,685),(786,660),(834,646),(922,644),(976,656),(1012,677),
               (994,696),(940,704),(872,703),(806,694),(768,690)],
     (58,46,43), (150,91,65), size=12, spacing=3.8, preset="oil_bristle",
     edge=True, jitter=0.18, gradient_angle=305, seed=203)
# left face turned toward the open sea
poly("cliff", [(690,900),(718,842),(710,805),(730,766),(742,735),(764,708),(786,688),
               (802,666),(820,660),(836,710),(810,754),(784,802),(766,860),(756,900)],
     (11,18,34), (94,62,57), size=18, spacing=4.5, preset="rough_wall_with_impasto",
     edge=True, jitter=0.20, gradient_angle=315, seed=205)
# central warm buttress
poly("cliff", [(820,660),(836,650),(885,644),(930,648),(956,666),(929,710),(913,762),
               (922,832),(906,900),(770,900),(786,828),(804,772),(818,715)],
     (39,34,42), (153,89,64), size=15, spacing=4.0, preset="oil_bristle",
     edge=True, jitter=0.18, gradient_angle=310, seed=207)
# right shoulder falling into shadow
poly("cliff", [(930,648),(974,656),(1008,674),(1044,698),(1070,730),(1100,748),
               (1140,782),(1200,818),(1200,900),(906,900),(922,832),(913,762),
               (929,710),(956,666)],
     (11,19,35), (67,51,56), size=19, spacing=4.7, preset="rough_wall_with_impasto",
     edge=True, jitter=0.21, gradient_angle=320, seed=209)
# deep seams, with a warm chisel edge on the lit side
seams = [
    [(820,660),(818,715),(804,772),(786,828),(770,900)],
    [(950,663),(929,710),(913,762),(922,832),(906,900)],
    [(1008,674),(1044,698),(1070,730),(1100,748)],
]
for si,line in enumerate(seams):
    for j in range(len(line)-1):
        x0,y0=line[j]; x1,y1=line[j+1]
        add("rock_detail", [(x0,y0),(x1,y1)], 10.0 if si<2 else 7.0,
            (8,14,26), 0.58, "round_soft", 0.35)
        if j < 3:
            add("rock_detail", [(x0-3,y0+1),(x1-3,y1+1)], 2.3,
                (186,117,76), 0.44, "dry", 0.5)
# irregular outcrops: each has a lit top edge and a dark underside
boulders = [
    [(752,690),(772,668),(803,664),(813,680),(799,698),(770,702)],
    [(806,680),(824,658),(852,651),(863,666),(851,692),(824,697)],
    [(952,656),(980,650),(1001,663),(996,682),(971,688),(952,679)],
    [(706,801),(730,774),(754,780),(765,806),(748,831),(722,829)],
    [(724,842),(748,816),(772,821),(780,849),(759,870),(733,862)],
    [(827,732),(854,716),(881,727),(884,755),(862,773),(838,759)],
    [(888,744),(914,729),(938,741),(937,770),(914,782),(893,768)],
    [(1002,724),(1028,709),(1053,723),(1054,748),(1031,762),(1009,746)],
    [(1040,800),(1070,778),(1101,792),(1103,824),(1073,839),(1048,819)],
]
for bi,pts in enumerate(boulders):
    lit = 1.0 if bi in (0,1,5,7) else 0.55
    c0 = np.array([45,40,46],np.float32)*(1-lit) + np.array([85,58,53],np.float32)*lit
    c1 = np.array([109,73,61],np.float32)*(1-lit) + np.array([195,120,78],np.float32)*lit
    poly("rock_detail", pts, c0, c1, size=9, spacing=3.5, opacity=0.92,
         preset="oil_bristle", edge=True, jitter=0.16, gradient_angle=315, seed=220+bi)
    # lit upper-left rim
    for j in range(1,3):
        ax,ay=pts[j]; bx,by=pts[j+1]
        add("rock_detail", [(ax,ay+1),(bx,by+1)], 3.6, (198,129,84), 0.50, "dry", 0.55)
    # shadowed lower-right foot
    for j in (3,4,5):
        if j+1 >= len(pts): continue
        ax,ay=pts[j]; bx,by=pts[j+1]
        add("rock_detail", [(ax,ay),(bx,by)], 6.0, (10,17,30), 0.45, "round_soft", 0.34)
# fine planar texture, mostly vertical/diagonal along the faces
for i in range(260):
    zone = rng.random()
    if zone < 0.34:
        px,py=rng.uniform(716,820),rng.uniform(700,890); ang=1.32+rng.uniform(-0.2,0.2)
        base=(74,55,57)
    elif zone < 0.78:
        px,py=rng.uniform(820,950),rng.uniform(700,890); ang=1.50+rng.uniform(-0.24,0.18)
        base=(138,84,64)
    else:
        px,py=rng.uniform(956,1188),rng.uniform(728,892); ang=1.10+rng.uniform(-0.22,0.18)
        base=(63,49,56)
    ln=rng.uniform(22,88)
    c=np.asarray(base,np.float32)*(0.64+0.52*rng.random())
    add("rock_detail", curved(px,py,ln,ang,0), rng.uniform(4.0,12.0), jc(c,7),
        rng.uniform(0.22,0.55), rng.choice(["oil_bristle","dry","sponge","round_soft"]),
        rng.uniform(0.45,0.88))
# a broken central ridge and a few horizontal ledges
tapered("rock_detail", "M 918 700 Q 901 758 906 808 Q 913 853 902 900", (16,23,37),
        8.0, 2.0, steps=18, opacity=0.45, preset="round_soft", hardness=0.4)
tapered("rock_detail", "M 914 702 Q 899 758 904 808 Q 910 853 899 900", (185,114,74),
        2.0, 0.4, steps=18, opacity=0.34, preset="dry", hardness=0.5)
for x0,x1,yy in [(790,854,778),(834,918,810),(880,970,848),(930,1050,866)]:
    add("rock_detail", [(x0,yy),(x1,yy+rng.uniform(-9,4))], 3.0, (13,20,33), 0.34, "round_soft", 0.35)
# short grass on the new shelf, framing rather than covering the tower
for i in range(23):
    bx = rng.uniform(790,1024); by = rng.uniform(640,658)
    if 846 < bx < 922: continue
    for k in range(int(rng.integers(3,5))):
        dx=rng.uniform(-8,8); hh=rng.uniform(6,20)
        d=f"M {bx+dx:.1f} {by:.1f} Q {bx+dx+rng.uniform(-4,4):.1f} {by-hh*.56:.1f} {bx+dx+rng.uniform(-8,8):.1f} {by-hh:.1f}"
        tapered("grass", d, rng.choice([(35,45,32),(54,55,33),(77,66,37),(100,79,43)]),
                rng.uniform(1.2,2.5), rng.uniform(0.15,0.5), steps=5,
                opacity=rng.uniform(0.6,0.9), preset="liner", hardness=0.55)

# final cliff surface: cold slate planes, short dabs, restrained ochre edges
cliff3 = [(690,900),(718,842),(710,805),(730,766),(742,735),(764,708),(786,688),
          (802,666),(836,652),(878,643),(918,643),(958,650),(994,662),(1028,678),
          (1058,700),(1090,716),(1125,748),(1162,782),(1200,808),(1200,900)]
poly("cliff", cliff3, (17,23,38), (56,50,54), size=18, spacing=3.7, opacity=1.0,
     preset="sponge", hardness=0.84, edge=True, jitter=0.40, gradient_angle=90, seed=301)
# top shelf, cool earth with a warm leading edge
poly("cliff", [(752,685),(786,660),(834,646),(922,644),(976,656),(1012,677),
               (994,696),(940,704),(872,703),(806,694),(768,690)],
     (56,49,46), (127,86,65), size=10, spacing=3.2, preset="sponge",
     edge=True, jitter=0.30, gradient_angle=305, seed=303)
# plane masses; muted and close in value so the edges do the describing
poly("cliff", [(690,900),(718,842),(710,805),(730,766),(742,735),(764,708),(786,688),
               (802,666),(820,660),(836,710),(810,754),(784,802),(766,860),(756,900)],
     (13,20,35), (75,60,60), size=14, spacing=3.4, preset="sponge",
     edge=True, jitter=0.34, gradient_angle=315, seed=305)
poly("cliff", [(820,660),(836,650),(885,644),(930,648),(956,666),(929,710),(913,762),
               (922,832),(906,900),(770,900),(786,828),(804,772),(818,715)],
     (40,37,44), (114,84,68), size=12, spacing=3.1, preset="sponge",
     edge=True, jitter=0.30, gradient_angle=310, seed=307)
poly("cliff", [(930,648),(974,656),(1008,674),(1044,698),(1070,730),(1100,748),
               (1140,782),(1200,818),(1200,900),(906,900),(922,832),(913,762),
               (929,710),(956,666)],
     (13,21,36), (60,50,56), size=15, spacing=3.5, preset="sponge",
     edge=True, jitter=0.34, gradient_angle=320, seed=309)
# (facet blocks are carried by the plane fills and the short dabs below)
# broken light along the skyline and the central ridge
skyline = [(752,685),(786,660),(834,646),(878,643),(922,644),(958,650),(994,662),(1012,677)]
for j in range(len(skyline)-1):
    ax,ay=skyline[j]; bx,by=skyline[j+1]
    for k in range(5):
        t=(k+rng.random())/5.0
        px,py=ax+(bx-ax)*t, ay+(by-ay)*t
        ang=math.atan2(by-ay,bx-ax)+rng.uniform(-0.05,0.05)
        add("rock_detail", curved(px,py,rng.uniform(7,20),ang), rng.uniform(1.6,3.8),
            jc(rng.choice([(212,143,94),(179,116,82),(131,91,72)]),6),
            rng.uniform(0.30,0.68), "dry", 0.58)
central_ridge=[(918,701),(907,755),(910,806),(920,850),(904,900)]
for j in range(len(central_ridge)-1):
    ax,ay=central_ridge[j]; bx,by=central_ridge[j+1]
    for k in range(5):
        t=(k+rng.random())/5.0
        px,py=ax+(bx-ax)*t,ay+(by-ay)*t
        add("rock_detail", [(px-2,py),(px+2,py+rng.uniform(7,16))], rng.uniform(1.5,3.2),
            rng.choice([(183,118,77),(95,73,64)]), rng.uniform(0.22,0.52), "dry", 0.5)
# short, dense rock dabs: many directions, no long parallel hatching
for i in range(640):
    px=rng.uniform(710,1190); py=rng.uniform(660,895)
    if py < 710 and px > 980: continue
    if px < 810: ang=1.28+rng.uniform(-0.40,0.32)
    elif px < 955: ang=1.46+rng.uniform(-0.50,0.42)
    else: ang=0.88+rng.uniform(-0.45,0.42)
    ln=rng.uniform(4,25)
    palette=[(21,29,44),(41,42,51),(67,59,59),(93,74,65),(139,93,69),(171,111,75)]
    c=rng.choice(palette)
    add("rock_detail", curved(px,py,ln,ang,0), rng.uniform(2.4,8.0), jc(c,6),
        rng.uniform(0.14,0.46), rng.choice(["sponge","dry","round_soft","bristle"]),
        rng.uniform(0.35,0.82))
# thin fractures and occasional ledges
for i in range(13):
    px=rng.uniform(735,1170); py=rng.uniform(690,850)
    if px < 800 and py < 720: continue
    ln=rng.uniform(28,72); ang=1.2+rng.uniform(-0.45,0.35)
    add("rock_detail", [(px,py),(px+math.cos(ang)*ln,py+math.sin(ang)*ln)],
        rng.uniform(1.2,3.4), (8,15,27), rng.uniform(0.20,0.44), "dry", 0.5)
for yy in (724,762,804,851):
    add("rock_detail", [(780,yy),(850,yy+rng.uniform(-6,5))], 2.0, (60,52,53), 0.20, "bristle", 0.45)
    add("rock_detail", [(930,yy+6),(1010,yy+rng.uniform(-5,7))], 1.6, (15,22,35), 0.24, "bristle", 0.45)
# sparse grass tufts along the shelf edge
for i in range(20):
    bx = rng.uniform(790,1024); by = rng.uniform(640,657)
    if 846 < bx < 922: continue
    for k in range(int(rng.integers(3,5))):
        dx=rng.uniform(-7,7); hh=rng.uniform(5,19)
        d=f"M {bx+dx:.1f} {by:.1f} Q {bx+dx+rng.uniform(-4,4):.1f} {by-hh*.56:.1f} {bx+dx+rng.uniform(-7,7):.1f} {by-hh:.1f}"
        tapered("grass", d, rng.choice([(34,44,32),(52,54,33),(74,64,36),(97,77,41)]),
                rng.uniform(1.1,2.4), rng.uniform(0.14,0.45), steps=5,
                opacity=rng.uniform(0.58,0.88), preset="liner", hardness=0.55)

# ------------- lighthouse: body, gallery, lantern, roof -------------
def halfwidth(y):
    return 35.0 + (62.0-35.0)*(y-TOWER_TOP)/(TOWER_BASE-TOWER_TOP)
def tower_poly(y0,y1):
    return [(LX-halfwidth(y0),y0),(LX+halfwidth(y0),y0),(LX+halfwidth(y1),y1),(LX-halfwidth(y1),y1)]

# stone plinth
poly("lighthouse", [(LX-66,TOWER_BASE-14),(LX+66,TOWER_BASE-14),(LX+72,TOWER_BASE+10),(LX-72,TOWER_BASE+10)],
     (56,48,51),(98,73,60),size=13,spacing=5.0,preset="oil_bristle",edge=True,jitter=0.2,gradient_angle=315,seed=61)
# white sections + red bands
bands = [(TOWER_TOP,418,(229,218,191),(255,237,199),(196,177,163)),
         (418,476,(176,57,52),(220,92,72),(116,42,53)),
         (476,548,(230,219,193),(255,240,205),(167,195,190)),
         (548,612,(169,51,51),(215,86,72),(112,39,51)),
         (612,TOWER_BASE-12,(225,212,186),(246,232,202),(151,169,170))]
for bi,(y0,y1,c,c2,sh) in enumerate(bands):
    poly("lighthouse", tower_poly(y0,y1), c, c2, size=13, spacing=4.5, opacity=1.0,
         preset="round_hard", hardness=0.75, edge=True, jitter=0.18, gradient_angle=300, seed=70+bi)
    # colored paint striations across the band
    for k in range(26):
        yy0 = rng.uniform(y0+2,y1-2)
        hw = halfwidth(yy0)
        x0 = LX-rng.uniform(2,hw*0.95); x1 = LX+rng.uniform(0,hw*0.95)
        if x1 <= x0: x0,x1 = x1,x0
        c = rng.choice([c,c2,sh])
        add("lighthouse", [(x0,yy0+rng.uniform(-2,2)),((x0+x1)/2,yy0+rng.uniform(-1,1)),(x1,yy0+rng.uniform(-2,2))],
            rng.uniform(2,7), jc(c,5), rng.uniform(0.18,0.44), rng.choice(["dry","bristle"]), 0.45)
# right-side shadow mass and left-edge lit strip
shadow_poly = []
for y0,y1 in [(TOWER_TOP,TOWER_BASE-10)]:
    hw0,hw1 = halfwidth(y0),halfwidth(y1)
    shadow_poly = [(LX+0.20*hw0,y0),(LX+hw0,y0),(LX+hw1,y1),(LX+0.20*hw1,y1)]
poly("lighthouse_shadow", shadow_poly, (70,67,82), (104,76,84), size=17, spacing=6.5,
     opacity=0.42, preset="round_soft", hardness=0.35, edge=False, jitter=0.18,
     gradient_angle=270, seed=81)
for y0,y1 in [(TOWER_TOP,TOWER_BASE-12)]:
    p = [(LX-halfwidth(y0)+2,y0),(LX-halfwidth(y0)+13,y0),(LX-halfwidth(y1)+13,y1),(LX-halfwidth(y1)+2,y1)]
    poly("lighthouse_light", p, (255,229,187),(255,190,141),size=7,spacing=5.2,
         opacity=0.28,preset="round_soft",edge=False,jitter=0.15,gradient_angle=270,seed=82)
# vertical impasto ribs
for i in range(17):
    x0 = LX-halfwidth(520)+rng.uniform(6,2*halfwidth(520)-6)
    yy0 = TOWER_TOP+rng.uniform(10,42); yy1 = TOWER_BASE-rng.uniform(14,36)
    x1 = x0 + rng.uniform(-4,4)
    c = (255,232,192) if x0 < LX-8 else ((106,79,89) if x0 > LX+12 else (213,180,150))
    add("lighthouse", [(x0,yy0),(x1,yy1)], rng.uniform(1.5,4.2), c,
        rng.uniform(0.18,0.42), rng.choice(["bristle","dry"]), 0.5)
# chunky horizontal impasto on the painted bands
for bi,(y0,y1,c,c2,sh) in enumerate(bands):
    for k in range(54):
        py = rng.uniform(y0+3,y1-3)
        hw = halfwidth(py)
        cx = LX + rng.uniform(-hw*0.86,hw*0.86)
        lit_here = 0.62 if cx < LX-10 else 0.23
        base = np.array(rng.choice([c,c2,sh]),np.float32)
        col = base*(0.74+0.42*rng.random())*(0.60+0.48*lit_here)
        col = np.clip(col + np.array([10,3,-2],np.float32)*lit_here,0,255)
        ln = rng.uniform(7,33)
        add("lighthouse", [(cx-ln/2,py+rng.uniform(-1.2,1.2)),(cx+ln/2,py+rng.uniform(-1.2,1.2))],
            rng.uniform(4.5,11.0), jc(col,5), rng.uniform(0.24,0.62),
            rng.choice(["round_soft","dry","bristle"]), rng.uniform(0.45,0.85))
        if rng.random() < 0.34:
            add("lighthouse", [(cx-ln*0.34,py-1.8),(cx+ln*0.34,py-1.4)],
                rng.uniform(1.0,2.8), jc(np.clip(base*1.15+8,0,255),4),
                rng.uniform(0.14,0.34), "dry", 0.5)

# dark windows with warm sill light
for wy in [397,492,566,624]:
    hw = halfwidth(wy)
    ww = 7.5 if wy < 600 else 10
    poly("lighthouse", [(LX-ww,wy-18),(LX+ww,wy-18),(LX+ww,wy+18),(LX-ww,wy+18)],
         (18,24,38),(44,39,48),size=6,spacing=3.5,preset="round_hard",opacity=1.0,edge=True,jitter=0.12,seed=90+wy)
    add("lighthouse_light", [(LX-ww+1,wy-13),(LX-ww+1,wy+13)], 2.2, (255,183,110), 0.5, "liner", 0.6)
    add("lighthouse", [(LX-ww-3,wy+19),(LX+ww+3,wy+19)], 3.0, (119,79,62), 0.65, "round_soft", 0.5)
# door
poly("lighthouse", [(LX-13,TOWER_BASE-12),(LX+13,TOWER_BASE-12),(LX+11,TOWER_BASE-62),(LX-11,TOWER_BASE-62)],
     (18,23,35),(53,40,43),size=7,spacing=3.6,preset="round_hard",edge=True,jitter=0.1,seed=101)
add("lighthouse_light", [(LX+7,TOWER_BASE-58),(LX+7,TOWER_BASE-15)], 2.0, (255,177,99), 0.30, "liner", 0.6)

# gallery deck and railing
poly("lighthouse", [(LX-72,329),(LX+72,329),(LX+77,344),(LX-77,344)], (50,40,46),(92,61,57),
     size=9,spacing=4.3,preset="oil_bristle",edge=True,jitter=0.18,gradient_angle=315,seed=111)
add("lighthouse_light", [(LX-74,330),(LX+74,330)], 3.5, (255,189,123), 0.28, "round_soft", 0.4)
for i in range(12):
    px = LX-62 + i*11.3
    add("lighthouse", [(px,301),(px+rng.uniform(-1,1),332)], 3.1, (35,35,48), 0.9, "liner", 0.55)
add("lighthouse", [(LX-64,310),(LX+64,310)], 3.0, (47,43,58), 0.85, "liner", 0.5)
add("lighthouse", [(LX-62,322),(LX+62,322)], 2.2, (71,58,64), 0.7, "liner", 0.5)
for i in range(9):
    px = LX-56+i*14
    add("lighthouse_light", [(px-3,309),(px+2,309)], 1.4, (255,194,128), 0.45, "liner", 0.6)

# lantern glass room: dark amber glass, then separate warm panes
glass = [(LX-37,266),(LX+37,266),(LX+43,326),(LX-43,326)]
poly("lantern_glass", glass, (42,44,54),(121,78,48),size=14,spacing=4.2,opacity=1.0,
     preset="oil_bristle",edge=True,jitter=0.14,gradient_angle=270,seed=121)
# three luminous glazed panels (glow sits under the black cage)
for cx,cw in [(LX-24,9.5),(LX,11.0),(LX+24,9.5)]:
    pane = [(cx-cw,271),(cx+cw,271),(cx+cw+1.5,321),(cx-cw-1.5,321)]
    poly("lantern_glow", pane, (188,101,43),(255,224,148),size=10,spacing=4.0,opacity=0.64,
         preset="round_soft",edge=False,jitter=0.12,gradient_angle=270,seed=124+int(cx))
    for k in range(12):
        lx = cx + rng.uniform(-cw+1,cw-1)
        add("lantern_glow", [(lx,275),(lx+rng.uniform(-1.2,1.2),318)], rng.uniform(1.0,2.7),
            rng.choice([(255,236,174),(255,186,92)]), rng.uniform(0.12,0.34), "dry", 0.4)
# the lamp itself, small and hot, plus a restrained halo
for rad,col,op,sz in [(9,(255,251,221),0.98,7),(18,(255,218,145),0.55,16),
                      (33,(255,169,82),0.18,28),(58,(255,145,70),0.055,48)]:
    poly("lantern_glow", ellipse_pts(LIGHTX,LIGHTY+4,rad,rad*0.86,40), col, None,
         size=sz, spacing=max(3.5,sz*0.20), opacity=op, preset="airbrush",
         hardness=0.12, edge=False, jitter=0.18, seed=130+rad)
for i in range(16):
    ang = rng.uniform(-math.pi,math.pi)
    ln = rng.uniform(8,29)
    add("lantern_glow", [(LIGHTX,LIGHTY+4),(LIGHTX+math.cos(ang)*ln,LIGHTY+4+math.sin(ang)*ln)],
        rng.uniform(1.2,3.1), (255,246,204), rng.uniform(0.12,0.40), "round_soft", 0.25)

# crisp black metal cage on top of the glow
for px in [LX-38,LX,LX+38]:
    add("lamp_frame", [(px,263),(px+(2 if px>LX else -2),330)], 5.0, (27,31,43), 0.96, "liner", 0.65)
add("lamp_frame", [(LX-38,263),(LX-44,330)], 4.4, (27,31,43), 0.96, "liner", 0.65)
add("lamp_frame", [(LX+38,263),(LX+44,330)], 4.4, (27,31,43), 0.96, "liner", 0.65)
for py in [267,291,321,329]:
    hw = 37 + 6*(py-266)/60
    add("lamp_frame", [(LX-hw,py),(LX+hw,py)], 3.8, (27,31,43), 0.94, "liner", 0.65)
# metal glints on the lit side of the cage
for py in [270,285,300,315]:
    add("lamp_frame", [(LX-37,py),(LX-33,py+1)], 1.0, (224,165,100), 0.45, "liner", 0.55)
add("lamp_frame", [(LX-50,261),(LX+50,261)], 4.2, (38,37,45), 0.9, "liner", 0.55)

# roof dome and cap
roof = [(LX-50,263),(LX-45,251)]
for t in np.linspace(0.0,1.0,18):
    roof.append((LX-45+45*t, 253 - 53*math.sin(math.pi*t)*0.88))
roof += [(LX+45,251),(LX+50,263)]
poly("lighthouse", roof, (25,33,47),(64,52,58),size=13,spacing=3.9,opacity=1.0,
     preset="oil_bristle",edge=True,jitter=0.18,gradient_angle=300,seed=141)
# dome ribs and rim light
for i in range(-2,3):
    x0 = LX + i*17
    add("lighthouse", [(x0,255),(LX+i*3,207+abs(i)*3)], rng.uniform(2.0,4.2), (58,53,62), 0.6, "liner", 0.5)
add("lighthouse", [(LX-48,253),(LX-41,229),(LX-25,211)], 4.0, (106,80,72), 0.55, "round_soft", 0.5)
add("lighthouse_light", [(LX-42,246),(LX-33,231),(LX-20,221)], 2.2, (255,182,112), 0.28, "liner", 0.6)
# gallery roof lip
poly("lighthouse", [(LX-53,251),(LX+53,251),(LX+49,260),(LX-49,260)], (49,42,49),
     (105,73,62),size=6,spacing=3.5,preset="oil_bristle",edge=True,jitter=0.1,seed=143)
# vent and lightning rod
poly("lighthouse", ellipse_pts(LX,197,9,11,20), (49,49,55),(101,80,65),size=5,spacing=2.5,
     preset="round_hard",edge=True,jitter=0.1,seed=145)
add("lighthouse_light", [(LX-4,193),(LX-1,191)], 2.0, (212,160,104), 0.45, "round_soft", 0.4)
tapered("lighthouse", f"M {LX:.1f} 190 Q {LX-2:.1f} 169 {LX:.1f} 145", (29,31,39), 5.0, 1.0,
        steps=14, opacity=1.0, preset="liner", hardness=0.75)
tapered("lighthouse", f"M {LX:.1f} 150 Q {LX+1:.1f} 126 {LX:.1f} 105", (29,31,39), 2.2, 0.45,
        steps=12, opacity=1.0, preset="liner", hardness=0.75)

# ------------- beams and atmospheric light -------------
def beam_cone(x0, y0a, y0b, x1, y1a, y1b, color, opacity, size0, size1, n=96, seed=0):
    """Paint a cone from stacked, feathered cross-sections (no hard wires)."""
    gen = np.random.default_rng(seed)
    for i in range(n):
        t = (i + gen.uniform(0.1,0.9))/max(1,n-1)
        xx = x0 + (x1-x0)*t
        ya = y0a + (y1a-y0a)*t + gen.uniform(-2.5,2.5)
        yb = y0b + (y1b-y0b)*t + gen.uniform(-2.5,2.5)
        sz = size0 + (size1-size0)*t
        c = np.asarray(color,np.float32) + gen.uniform(-5,5,3)
        add("beam", [(xx,ya),(xx,yb)], sz, c, opacity*gen.uniform(0.78,1.18),
            "airbrush", 0.12)

# Main sweep angles up into the dark sky so its volume has contrast.
beam_cone(LX-37, 258, 302, -58, 92, 238, (255,186,110), 0.080, 42, 84, 108, 151)
beam_cone(LX-38, 268, 293, -54, 124, 211, (255,232,182), 0.088, 26, 52, 102, 153)
# A handful of feathered streaks add direction without reading as cables.
for k in range(18):
    frac = rng.uniform(-0.78,0.78)
    y0 = 280 + frac*15
    y1 = 168 + frac*86
    tapered("beam", f"M {LX-45:.1f} {y0:.1f} Q 610 {y0+frac*8-18:.1f} -45 {y1:.1f}",
            rng.choice([(255,239,195),(255,207,140),(255,250,220)]),
            rng.uniform(7,18), rng.uniform(0.8,3.0), steps=26,
            opacity=rng.uniform(0.020,0.060), preset="airbrush", hardness=0.15)
# bright feathered core, still soft-edged
beam_cone(LX-39, 271, 291, -58, 145, 196, (255,244,208), 0.070, 12, 30, 100, 156)
# three broad soft shafts hold the cone together without reading as wires
for frac in (-0.42, 0.0, 0.38):
    y0 = 280 + frac*12
    y1 = 172 + frac*58
    tapered("beam", f"M {LX-48:.1f} {y0:.1f} Q 520 {y0-32+frac*10:.1f} -52 {y1:.1f}",
            (255,238,198), 25.0, 7.0, steps=28, opacity=0.060,
            preset="airbrush", hardness=0.12, copies=2, spread=9.0)
# a warmer return sweep on the right, angled down toward the sea
beam_cone(LX+39, 272, 300, W+50, 330, 398, (255,164,92), 0.055, 32, 66, 74, 154)
beam_cone(LX+40, 279, 292, W+46, 344, 378, (255,218,165), 0.045, 20, 38, 64, 155)
# dust motes, clustered toward the source
for i in range(250):
    u = rng.random()**1.5
    px = LX - u*(LX+20)
    ctr = 279 + u*(rng.uniform(-35,38))
    py = ctr + rng.uniform(-22,22)
    add("beam", [(px,py),(px+rng.uniform(-4,4),py+rng.uniform(-2,2))],
        rng.uniform(0.7,2.2), (255,235,186), rng.uniform(0.045,0.18),
        "airbrush", 0.2)

# birds
for bx,by,sc in [(700,416,1.0),(728,402,0.78),(751,429,0.92),(785,385,0.58)]:
    add("detail", [(bx-10*sc,by),(bx-3*sc,by-4*sc),(bx,by)], 2.0, (39,37,53), 0.7, "liner", 0.55)
    add("detail", [(bx,by),(bx+5*sc,by-5*sc),(bx+12*sc,by-2*sc)], 2.0, (39,37,53), 0.7, "liner", 0.55)

# ------------- foam where the sea meets the headland -------------
for i in range(190):
    px = rng.uniform(700,900); py = rng.uniform(624,690)
    if py < 648 and px > 830: continue
    ln = rng.uniform(7,38)
    c = (222,214,184) if rng.random()<0.6 else (168,176,165)
    add("foam", [(px-ln/2,py),(px,py+rng.uniform(-2,2)),(px+ln/2,py+rng.uniform(-2,2))],
        rng.uniform(1.4,4.2), jc(c,7), rng.uniform(0.18,0.6), rng.choice(["bristle","dry","round_soft"]), 0.55)

# ------------- foreground shelf: plant the tower into the headland -------------
poly("ground_front", [(LX-94,646),(LX+78,647),(LX+111,669),(LX+78,688),(LX-55,686),(LX-109,664)],
     (43,39,42),(119,77,57),size=14,spacing=4.5,preset="oil_bristle",
     edge=True,jitter=0.22,gradient_angle=300,seed=181)
# cast shadow runs down-right from the tower foot
poly("ground_front", [(LX+18,652),(LX+155,684),(LX+99,704),(LX+8,672)],
     (18,24,37),(44,36,44),size=14,spacing=5.2,preset="round_soft",
     opacity=0.54,edge=False,jitter=0.2,gradient_angle=315,seed=182)
# a lip of broken stones along the front edge
for i in range(95):
    px = rng.uniform(LX-100,LX+96); py = rng.uniform(659,691)
    if abs(px-LX) < 52 and py < 674: continue
    ln = rng.uniform(7,30); ang = rng.uniform(-0.12,0.22)
    c = rng.choice([(51,43,43),(75,55,49),(103,67,52),(128,80,55),(38,35,42)])
    add("ground_front", curved(px,py,ln,ang), rng.uniform(4.5,13.0), jc(c,6),
        rng.uniform(0.38,0.78), rng.choice(["oil_bristle","round_soft","dry"]), rng.uniform(0.5,0.9))
# warm rim on the shelf's left and seaward edge
for i in range(70):
    px = rng.uniform(LX-105,LX-18); py = rng.uniform(648,676)
    add("ground_front", [(px,py),(px+rng.uniform(6,25),py+rng.uniform(-2,3))],
        rng.uniform(1.5,4.0), rng.choice([(190,123,78),(222,152,91),(114,79,59)]),
        rng.uniform(0.22,0.58), "dry", 0.55)
# grass framing the foundation, kept off the tower face
for i in range(24):
    bx = rng.choice([rng.uniform(LX-102,LX-57), rng.uniform(LX+47,LX+92)])
    by = rng.uniform(650,673)
    for k in range(int(rng.integers(3,6))):
        dx = rng.uniform(-8,8); hh = rng.uniform(7,28)
        d = f"M {bx+dx:.1f} {by:.1f} Q {bx+dx+rng.uniform(-5,5):.1f} {by-hh*.58:.1f} {bx+dx+rng.uniform(-9,9):.1f} {by-hh:.1f}"
        tapered("ground_front", d, rng.choice([(40,48,32),(57,58,34),(83,69,38),(111,84,44)]),
                rng.uniform(1.2,2.8), rng.uniform(0.15,0.5), steps=5,
                opacity=rng.uniform(0.62,0.95), preset="liner", hardness=0.55)

# ------------- final unifying atmosphere -------------
# a few broad scumbles of dusk haze, screen
for i in range(55):
    py = rng.uniform(470,620); px = rng.uniform(-60,W+60)
    add("atmosphere", [(px-170,py+rng.uniform(-8,8)),(px+130,py+rng.uniform(-4,4)),(px+360,py+rng.uniform(-1,1))],
        rng.uniform(6,17), (255,150,98), rng.uniform(0.018,0.055), "airbrush", 0.2)
# warm headland edge catches last light
for i in range(160):
    px = rng.uniform(720,1120); py = rng.uniform(645,760)
    if rng.random()<0.55:
        add("atmosphere", [(px,py),(px+rng.uniform(5,26),py+rng.uniform(-3,3))],
            rng.uniform(1.5,4.3), (255,162,100), rng.uniform(0.05,0.18), "dry", 0.4)
# vignette corners / depth, softlight
poly("atmosphere", [(0,0),(260,0),(70,330),(0,470)], (32,32,62),(92,57,72),size=55,
     spacing=22,opacity=0.08,preset="airbrush",edge=False,jitter=0.3,seed=170)
poly("atmosphere", [(W,0),(W,270),(940,96),(820,0)], (28,28,58),(78,53,79),size=55,
     spacing=22,opacity=0.07,preset="airbrush",edge=False,jitter=0.3,seed=171)
poly("atmosphere", [(0,H),(0,800),(260,884),(480,H)], (8,18,35),(41,33,52),size=60,
     spacing=24,opacity=0.09,preset="airbrush",edge=False,jitter=0.3,seed=172)

# ------------- save plan, render, thumbnail -------------
layer_blends = {
    "under":"normal", "sky":"normal", "sky_texture":"normal",
    "sun_core":"screen", "cloud":"normal", "cloud_rim":"normal",
    "cloud_wisp":"normal", "sun_glow":"screen", "beam":"normal",
    "sea":"normal", "sea_light":"screen", "sea_break":"normal", "sea_haze":"screen",
    "cliff":"normal", "rock_detail":"normal", "grass":"normal",
    "lighthouse":"normal", "lighthouse_shadow":"normal",
    "lighthouse_light":"screen", "lantern_glass":"normal",
    "lantern_glow":"screen", "lamp_frame":"normal",
    "ground_front":"normal", "foam":"screen", "detail":"normal",
    "atmosphere":"softlight",
}
layer_order = list(layer_blends.keys())
plan_path = OUT / "lighthouse_dusk.plan.json"
png_path = OUT / "lighthouse_dusk.png"
count = plan_io.dump_plan(str(plan_path), W, H, doc["strokes"], layer_blends=layer_blends, layer_order=layer_order)
t0 = time.perf_counter()
plan_io.render_plan(str(plan_path), str(png_path), threads=8)
render_s = time.perf_counter()-t0
from PIL import Image
im = Image.open(png_path).convert("RGB")
im.resize((W//4,H//4), Image.LANCZOS).save(OUT / "lighthouse_dusk_25.png")
print(json.dumps({"plan":str(plan_path),"png":str(png_path),"strokes":count,
   "layers":len(layer_order),"bytes":png_path.stat().st_size,"render_s":round(render_s,3)},ensure_ascii=False))
