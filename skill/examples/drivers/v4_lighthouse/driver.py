# -*- coding: utf-8 -*-
"""Deterministic plan driver for a dusk lighthouse, 1200x900, impasto.

Only writes inside this asset folder. All geometry is invented here; no
reference bitmap is read.
"""
from pathlib import Path
import sys, math, json, time
import numpy as np

SKILL = Path(r"D:\deepseek-harness-dsh-v0.2.2rc1\.dsh-home\skills\svg-rebuild-and-paint")
OUT = Path(r"D:\DSHwork\flower\promo\assets\cold-lighthouse-v4")
sys.path.insert(0, str(SKILL / "scripts"))
import plan_io
import shape_ops

W, H = 1200, 900

LAYERS = [
    ("bed", "normal"),
    ("sky", "normal"),
    ("clouds_shadow", "normal"),
    ("clouds_light", "normal"),
    ("beam", "screen"),
    ("sea", "normal"),
    ("sea_glints", "screen"),
    ("horizon_haze", "screen"),
    ("distant_land", "normal"),
    ("cliff", "normal"),
    ("cliff_light", "screen"),
    ("tower", "normal"),
    ("tower_detail", "normal"),
    ("tower_light", "screen"),
    ("lamp_glow", "screen"),
    ("contact_dark", "multiply"),
    ("contact_foam", "normal"),
    ("foreground", "normal"),
    ("vignette", "softlight"),
]
BLENDS = dict(LAYERS)

doc = {"canvas": [W, H], "layers": [{"name": n, "blend": b} for n, b in LAYERS], "strokes": []}
rng = np.random.default_rng(20260418)


def clamp(v, lo=0.0, hi=255.0):
    return max(lo, min(hi, float(v)))


def color_jit(c, amt, r):
    return [clamp(c[0] + r.uniform(-amt, amt)),
            clamp(c[1] + r.uniform(-amt, amt)),
            clamp(c[2] + r.uniform(-amt, amt))]


def mix(c1, c2, t):
    t = max(0.0, min(1.0, float(t)))
    return [c1[i] + (c2[i] - c1[i]) * t for i in range(3)]


def add(layer, pts, size, color, opacity=1.0, preset="round_soft",
        hardness=0.5, tag=None, pressure=1.0):
    clean = []
    for p in pts:
        if len(p) == 2:
            clean.append([float(p[0]), float(p[1]), float(pressure)])
        else:
            clean.append([float(p[0]), float(p[1]), float(p[2])])
    s = {
        "layer": layer,
        "preset": preset,
        "size": float(size),
        "opacity": float(opacity),
        "color": [float(c) for c in color],
        "hardness": float(hardness),
        "points": clean,
    }
    if tag:
        s["tag"] = tag
    doc["strokes"].append(s)


def dab(layer, x, y, size, color, opacity=1.0, preset="round_soft", hardness=0.5, tag=None):
    add(layer, [[x, y, 1.0]], size, color, opacity, preset, hardness, tag)


def stroke_xy(layer, x0, y0, x1, y1, size, color, opacity=1.0,
              preset="round_soft", hardness=0.5, curve=0.0, tag=None):
    mx = (x0 + x1) * 0.5
    my = (y0 + y1) * 0.5 + curve
    add(layer, [[x0, y0, 1.0], [mx, my, 1.0], [x1, y1, 1.0]],
        size, color, opacity, preset, hardness, tag)


def point_in_poly(x, y, poly):
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi):
            inside = not inside
        j = i
    return inside


def poly_bbox(poly):
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    return min(xs), min(ys), max(xs), max(ys)


def rand_in_poly(poly, r):
    x0, y0, x1, y1 = poly_bbox(poly)
    for _ in range(300):
        x = float(r.uniform(x0, x1)); y = float(r.uniform(y0, y1))
        if point_in_poly(x, y, poly):
            return x, y
    return (x0 + x1) * 0.5, (y0 + y1) * 0.5


def sky_color(y):
    stops = [
        (-30, (10, 16, 41)),
        (100, (18, 25, 57)),
        (230, (38, 39, 78)),
        (340, (75, 55, 91)),
        (440, (128, 78, 99)),
        (520, (190, 112, 109)),
        (575, (235, 150, 113)),
        (620, (247, 175, 130)),
    ]
    if y <= stops[0][0]:
        return list(stops[0][1])
    for i in range(len(stops) - 1):
        y0, c0 = stops[i]; y1, c1 = stops[i + 1]
        if y <= y1:
            t = (y - y0) / (y1 - y0)
            t = t * t * (3 - 2 * t)
            return mix(c0, c1, t)
    return list(stops[-1][1])


def sea_color(y):
    stops = [
        (580, (145, 101, 106)),
        (610, (92, 76, 95)),
        (670, (49, 63, 84)),
        (740, (26, 47, 68)),
        (820, (16, 35, 56)),
        (900, (12, 27, 46)),
    ]
    if y <= stops[0][0]:
        return list(stops[0][1])
    for i in range(len(stops) - 1):
        y0, c0 = stops[i]; y1, c1 = stops[i + 1]
        if y <= y1:
            t = (y - y0) / (y1 - y0)
            t = t * t * (3 - 2 * t)
            return mix(c0, c1, t)
    return list(stops[-1][1])


# ---------------------------------------------------------------- bed
# One enormous opaque understroke guarantees no transparent holes.
add("bed", [(-250, H * 0.5), (W + 250, H * 0.5)], 2400,
    (13, 16, 31), 1.0, "round_hard", 1.0)

# ---------------------------------------------------------------- sky
r = np.random.default_rng(101)
# Broad, slightly diagonal horizontal paint bands establish the dusk ramp.
for y in range(-18, 626, 5):
    c = sky_color(y)
    for k in range(3):
        yy = y + r.uniform(-2.0, 2.0)
        x0 = -220 + r.uniform(-80, 80)
        x1 = W + 220 + r.uniform(-80, 80)
        dy = r.uniform(-8, 8)
        stroke_xy("sky", x0, yy, x1, yy + dy,
                  r.uniform(10, 20), color_jit(c, 5, r),
                  r.uniform(0.55, 0.9), "round_soft" if k else "bristle",
                  r.uniform(0.18, 0.45), r.uniform(-7, 7))
# Softer glazes to stop the bands reading as stripes.
for i in range(280):
    y = r.uniform(-20, 620)
    c = color_jit(sky_color(y), 10, r)
    x0 = r.uniform(-300, 400); x1 = r.uniform(800, 1500)
    stroke_xy("sky", x0, y, x1, y + r.uniform(-35, 35),
              r.uniform(35, 95), c, r.uniform(0.08, 0.22),
              "round_soft", 0.18, r.uniform(-12, 12))
# Short bristle marks and dry scratches, more visible than in a flat fill.
for i in range(185):
    y = r.uniform(15, 600)
    c = color_jit(sky_color(y), 14, r)
    x = r.uniform(-60, W + 60)
    ln = r.uniform(85, 310)
    ang = math.radians(r.uniform(-18, 18))
    x2 = x + ln * math.cos(ang); y2 = y + ln * math.sin(ang)
    preset = "bristle" if i % 3 else "dry"
    stroke_xy("sky", x, y, x2, y2, r.uniform(8, 22), c,
              r.uniform(0.05, 0.14), preset, r.uniform(0.18, 0.5),
              r.uniform(-3, 3))
# Thin warm cloud-free horizon line.
for i in range(160):
    y = r.uniform(568, 592)
    x0 = r.uniform(-100, 100); x1 = r.uniform(1100, 1300)
    c = color_jit((224, 143, 112), 12, r)
    stroke_xy("sky", x0, y, x1, y + r.uniform(-2, 2),
              r.uniform(3, 9), c, r.uniform(0.06, 0.16),
              "round_soft", 0.25)

# ---------------------------------------------------------------- clouds
# A cloud is a union of flattened lobes. Each mark is short; no pill-shaped
# single stroke is allowed to stand in for the whole cloud.
CLOUDS = [
    {"lobes": [(225, 146, 250, 54, -0.05), (385, 164, 210, 46, 0.05),
               (92, 170, 140, 32, -0.10)], "tone": 0.10, "n": 310},
    {"lobes": [(790, 144, 285, 45, 0.02), (990, 165, 235, 38, -0.03),
               (600, 170, 160, 31, 0.02)], "tone": 0.22, "n": 300},
    {"lobes": [(305, 332, 300, 82, -0.04), (500, 355, 245, 56, 0.03),
               (138, 360, 195, 48, 0.02)], "tone": 0.40, "n": 470},
    {"lobes": [(958, 300, 285, 72, -0.03), (1130, 329, 180, 52, 0.02),
               (785, 328, 165, 40, 0.04)], "tone": 0.48, "n": 390},
    {"lobes": [(770, 514, 410, 39, 0.00), (1010, 529, 225, 29, 0.01),
               (520, 528, 190, 27, -0.01)], "tone": 0.64, "n": 260},
]
CLOUD_DARK = [(46, 37, 68), (63, 43, 69), (89, 55, 76), (116, 66, 79), (142, 83, 91)]
CLOUD_WARM = [(194, 112, 104), (224, 137, 111), (245, 164, 126), (252, 187, 143)]


def cloud_point(lobe, r, edge=False):
    cx, cy, rx, ry, rot = lobe
    if edge:
        rad = r.uniform(0.55, 1.04)
        # only the lower half of the lobe catches the sunset
        th = r.uniform(math.pi * 0.08, math.pi * 0.92)
        x = math.cos(th) * rx * rad
        y = math.sin(th) * ry * rad
    else:
        rad = math.sqrt(r.random()) * 0.9
        th = r.uniform(0, math.pi * 2)
        x = math.cos(th) * rx * rad
        y = math.sin(th) * ry * rad
    xr = x * math.cos(rot) - y * math.sin(rot)
    yr = x * math.sin(rot) + y * math.cos(rot)
    return cx + xr, cy + yr, rad, th


for ci, cloud in enumerate(CLOUDS):
    lobes = cloud["lobes"]
    tone = cloud["tone"]
    base_dark = mix(CLOUD_DARK[0], CLOUD_DARK[3], tone)
    # shadow body
    for i in range(cloud["n"]):
        lobe = lobes[int(r.integers(0, len(lobes)))]
        x, y, rad, th = cloud_point(lobe, r, edge=False)
        c = color_jit(mix(base_dark, CLOUD_DARK[4], r.uniform(0, 0.22)), 9, r)
        # lower part is cooler/darker; a little rose light leaks in
        if th > 0.2:
            c = mix(c, (147, 83, 88), 0.18)
        ln = r.uniform(14, 54)
        ang = math.radians(r.uniform(-10, 10))
        x2 = x + ln * math.cos(ang); y2 = y + ln * math.sin(ang)
        preset = "bristle" if (i + ci) % 4 == 0 else "round_soft"
        add("clouds_shadow", [[x, y, 1.0], [x2, y2, r.uniform(0.45, 1.0)]],
            r.uniform(14, 44), c, r.uniform(0.10, 0.25), preset,
            r.uniform(0.18, 0.48))
    # broad soft pillows bind the individual marks into a cloud mass
    for i in range(max(18, cloud["n"] // 9)):
        lobe = lobes[int(r.integers(0, len(lobes)))]
        x, y, rad, th = cloud_point(lobe, r, edge=False)
        c = color_jit(mix(base_dark, (119, 77, 88), 0.28), 7, r)
        dab("clouds_shadow", x, y, r.uniform(80, 210), c,
            r.uniform(0.025, 0.060), "round_soft", 0.08)
    # warm, broken under-rim
    for i in range(8):
        lobe = lobes[int(r.integers(0, len(lobes)))]
        x, y, rad, th = cloud_point(lobe, r, edge=True)
        dab("clouds_light", x, y, r.uniform(28, 84),
            color_jit((204, 116, 100), 8, r), r.uniform(0.012, 0.032),
            "round_soft", 0.12)
    for i in range(max(10, cloud["n"] // 18)):
        lobe = lobes[int(r.integers(0, len(lobes)))]
        x, y, rad, th = cloud_point(lobe, r, edge=True)
        c = color_jit(mix((192, 108, 98), CLOUD_WARM[3], r.uniform(0.20, 0.85)), 8, r)
        ln = r.uniform(10, 34)
        ang = math.radians(r.uniform(-16, 16))
        add("clouds_light", [[x - ln * 0.5, y, 0.9],
                             [x + ln * 0.5, y + r.uniform(-2, 2), 0.45]],
            r.uniform(7, 23), c, r.uniform(0.05, 0.16), "round_soft", 0.20)
    # sparse cool top edge to keep the cloud volume readable
    for i in range(max(16, cloud["n"] // 14)):
        lobe = lobes[int(r.integers(0, len(lobes)))]
        cx, cy, rx, ry, rot = lobe
        th = r.uniform(math.pi * 1.05, math.pi * 1.95)
        rad = r.uniform(0.7, 1.0)
        x = cx + math.cos(th) * rx * rad
        y = cy + math.sin(th) * ry * rad
        c = color_jit((131, 111, 137), 12, r)
        add("clouds_light", [[x, y, 0.8], [x + r.uniform(-14, 14), y + r.uniform(-3, 3), 0.35]],
            r.uniform(4, 12), c, r.uniform(0.05, 0.14), "dry", 0.35)

# ---------------------------------------------------------------- beam
# Main cone to the right; drawn behind the tower and the solid shore.
OX, OY = 790.0, 197.0
beam_poly = [[OX - 2, OY - 10], [1188, 34], [1180, 196], [OX + 5, OY + 12]]
shape_ops.fill_polygon(doc, "beam", beam_poly, color=(255, 210, 126),
                       color2=(226, 129, 86), size=32, opacity=0.065,
                       preset="round_soft", spacing=16, jitter=0.60,
                       angle=-8, gradient_angle=0, hardness=0.15,
                       blend="screen", seed=701, edge=False)
# A warm inner core makes the fan read as a beam, not a cloud.
beam_core = [[OX - 1, OY - 6], [1192, 88], [1192, 152], [OX + 4, OY + 8]]
shape_ops.fill_polygon(doc, "beam", beam_core, color=(255, 238, 174),
                       color2=(248, 167, 101), size=18, opacity=0.032,
                       preset="round_soft", spacing=13, jitter=0.45,
                       angle=-12, gradient_angle=0, hardness=0.12,
                       blend="screen", seed=703, edge=False)

# Broad atmospheric wash near the cone core.
for t, sz, op in [(0.16, 285, 0.042), (0.34, 235, 0.037), (0.56, 190, 0.030)]:
    tx = OX + (1120 - OX) * t
    ty = OY + (105 - OY) * t
    stroke_xy("beam", OX + 3, OY, tx, ty, sz, (255, 205, 125), op,
              "round_soft", 0.10)
# Strong readable edges of the cone.
for ey, eop in [(38, 0.046), (105, 0.062), (188, 0.040)]:
    stroke_xy("beam", OX + 3, OY, 1182, ey, 2.0 + eop * 26,
              (255, 200, 120), eop, "round_soft", 0.10)
# Filament-like light streaks inside the cone.
rr = np.random.default_rng(702)
for i in range(390):
    t = rr.beta(1.25, 2.2)
    dist = 18 + t * 450
    ang = math.radians(-13.0 + rr.normal(0, 5.2))
    x = OX + math.cos(ang) * dist + rr.normal(0, 5)
    y = OY + math.sin(ang) * dist + rr.normal(0, 10 + 30 * t)
    ln = rr.uniform(10, 95) * (0.45 + 0.75 * t)
    x0 = x - math.cos(ang) * ln * 0.5; y0 = y - math.sin(ang) * ln * 0.5
    x1 = x + math.cos(ang) * ln * 0.5; y1 = y + math.sin(ang) * ln * 0.5
    c = mix((255, 208, 126), (232, 137, 89), t * 0.9)
    c = color_jit(c, 7, rr)
    op = rr.uniform(0.012, 0.050) * (1.0 - 0.25 * t)
    stroke_xy("beam", x0, y0, x1, y1, rr.uniform(1.2, 5.2), c, op,
              "airbrush" if i % 4 else "round_soft", 0.08)
# Broad, slow wisps carry the volume; the hairline strokes only texture it.
for i in range(135):
    t = rr.beta(1.5, 2.0)
    dist = 24 + t * 430
    ang = math.radians(-13.0 + rr.normal(0, 2.7))
    x = OX + math.cos(ang) * dist + rr.normal(0, 7)
    y = OY + math.sin(ang) * dist + rr.normal(0, 12 + 24 * t)
    ln = rr.uniform(35, 150)
    x0 = x - math.cos(ang) * ln * 0.5; y0 = y - math.sin(ang) * ln * 0.5
    x1 = x + math.cos(ang) * ln * 0.5; y1 = y + math.sin(ang) * ln * 0.5
    stroke_xy("beam", x0, y0, x1, y1, rr.uniform(8, 34),
              color_jit((255, 195, 112), 7, rr), rr.uniform(0.016, 0.045),
              "round_soft", 0.08)

# small back-scatter on the left keeps the lamp from looking pasted on.
for i in range(140):
    t = rr.beta(1.8, 2.0)
    ang = math.radians(168 + rr.normal(0, 9))
    dist = 18 + t * 270
    x = OX + math.cos(ang) * dist; y = OY + math.sin(ang) * dist
    add("beam", [[x, y, 1.0]], rr.uniform(16, 80),
        color_jit((255, 207, 144), 8, rr), rr.uniform(0.012, 0.045),
        "round_soft", 0.08)

# ---------------------------------------------------------------- sea
r = np.random.default_rng(201)
# Opaque-ish depth ramp, with brush size growing toward the viewer.
for y in range(584, 910, 4):
    c = sea_color(y)
    depth = max(0.0, min(1.0, (y - 584) / 326.0))
    for k in range(4):
        yy = y + r.uniform(-2, 2)
        x0 = -200 + r.uniform(-60, 120)
        x1 = W + 200 + r.uniform(-120, 60)
        stroke_xy("sea", x0, yy, x1, yy + r.uniform(-3, 3),
                  r.uniform(8, 15) + depth * r.uniform(4, 22),
                  color_jit(c, 6, r), r.uniform(0.5, 0.9),
                  "flat" if k == 0 else "round_hard",
                  r.uniform(0.5, 0.85), r.uniform(-4, 4))
for i in range(300):
    y = r.uniform(590, 900)
    depth = (y - 590) / 310.0
    c = color_jit(sea_color(y), 9, r)
    x0 = r.uniform(-80, 350); x1 = r.uniform(850, 1280)
    stroke_xy("sea", x0, y, x1, y + r.uniform(-6, 6),
              r.uniform(25, 75) * (0.6 + depth),
              c, r.uniform(0.06, 0.18), "round_soft", 0.18,
              r.uniform(-8, 8))

# Wave fragments: short, slightly arced, perspective-scaled.
for i in range(1250):
    y = r.uniform(592, 898)
    depth = (y - 592) / 306.0
    x = r.uniform(-40, W + 40)
    ln = r.uniform(8, 25) + depth * r.uniform(18, 105)
    c0 = sea_color(y)
    light = r.random() < 0.55
    if light:
        c = mix(c0, (171, 167, 157), r.uniform(0.18, 0.58))
        op = r.uniform(0.12, 0.42)
        sz = r.uniform(1.2, 4.0) + depth * r.uniform(0, 4)
    else:
        c = mix(c0, (9, 20, 36), r.uniform(0.15, 0.5))
        op = r.uniform(0.12, 0.36)
        sz = r.uniform(1.5, 5.5) + depth * r.uniform(0, 5)
    c = color_jit(c, 8, r)
    pts = [[x - ln * 0.5, y + r.uniform(-2, 2), 0.8],
           [x, y + r.uniform(-5, 5), 1.0],
           [x + ln * 0.5, y + r.uniform(-2, 2), 0.6]]
    add("sea", pts, sz, c, op, "round_soft" if light else "dry",
        r.uniform(0.2, 0.7))
# Long swell lines, kept sparse and uneven near the bottom.
for i in range(250):
    y = r.uniform(690, 900)
    depth = (y - 690) / 210.0
    x0 = r.uniform(-100, 500); x1 = x0 + r.uniform(220, 760)
    c = mix(sea_color(y), (79, 105, 118), r.uniform(0.12, 0.38))
    stroke_xy("sea", x0, y, x1, y + r.uniform(-8, 8),
              r.uniform(2, 7) + depth * 6, c, r.uniform(0.1, 0.28),
              "bristle", r.uniform(0.25, 0.65), r.uniform(-5, 8))

# Sea glints: cool crests plus the broken warm column below the lighthouse.
for i in range(520):
    y = r.uniform(598, 892)
    depth = (y - 598) / 294.0
    x = r.uniform(-30, W + 30)
    c = mix((146, 165, 169), (235, 209, 173), r.uniform(0.0, 0.55))
    op = r.uniform(0.04, 0.16) * (1.2 - 0.45 * depth)
    ln = r.uniform(8, 34) + depth * r.uniform(10, 70)
    stroke_xy("sea_glints", x, y, x + ln, y + r.uniform(-2, 2),
              r.uniform(1.0, 3.2), color_jit(c, 8, r), op,
              "round_soft", 0.12)
rr = np.random.default_rng(202)
for i in range(420):
    t = rr.random() ** 1.6
    y = 600 + t * 286
    spread = 18 + t * 62
    x = 755 + rr.normal(0, spread) + t * 18
    ln = rr.uniform(5, 18) + t * rr.uniform(5, 38)
    c = color_jit(mix((255, 209, 142), (193, 107, 79), t), 8, rr)
    stroke_xy("sea_glints", x, y, x + ln, y + rr.uniform(-2, 2),
              rr.uniform(1.0, 3.8), c,
              rr.uniform(0.025, 0.13) * (1.0 - 0.45 * t),
              "round_soft" if i % 5 else "dry", 0.16)
# subtle horizon shimmer
for i in range(240):
    x = r.uniform(-30, W + 30); y = r.uniform(584, 600)
    c = color_jit((244, 188, 146), 12, r)
    stroke_xy("horizon_haze", x, y, x + r.uniform(15, 100), y + r.uniform(-1.5, 1.5),
              r.uniform(2, 7), c, r.uniform(0.025, 0.085), "round_soft", 0.18)

# ---------------------------------------------------------------- distant land
far_land = [(-20, 610), (45, 565), (138, 554), (212, 566), (282, 589),
            (305, 610), (-20, 630)]
shape_ops.fill_polygon(doc, "distant_land", far_land, color=(38, 48, 66),
                       color2=(22, 35, 52), size=8, opacity=0.85,
                       preset="bristle", spacing=6, jitter=0.55,
                       angle=-4, gradient_angle=0, hardness=0.55,
                       blend="normal", seed=301, edge=False)
for i in range(90):
    x = r.uniform(-10, 305); y = r.uniform(552, 602)
    if not point_in_poly(x, y, far_land):
        continue
    c = color_jit((47, 58, 74), 8, r)
    stroke_xy("distant_land", x, y, x + r.uniform(3, 20), y + r.uniform(-2, 3),
              r.uniform(2, 7), c, r.uniform(0.15, 0.4), "dry", 0.45)
for i in range(36):
    x = r.uniform(0, 290); y = 559 + r.uniform(-7, 8)
    stroke_xy("distant_land", x, y, x + r.uniform(4, 20), y + r.uniform(-2, 2),
              r.uniform(1.2, 3), color_jit((112, 100, 103), 8, r),
              r.uniform(0.08, 0.2), "round_soft", 0.25)

# ---------------------------------------------------------------- cliff / island
island = [(586, 783), (604, 741), (631, 706), (660, 680),
          (690, 661), (716, 643), (742, 655), (765, 646),
          (792, 652), (814, 641), (842, 660), (870, 679),
          (896, 705), (918, 740), (934, 776), (922, 805),
          (888, 826), (838, 840), (780, 846), (720, 840),
          (668, 825), (624, 805), (595, 795)]
shape_ops.fill_polygon(doc, "cliff", island, color=(121, 88, 86),
                       color2=(25, 40, 56), size=15, opacity=0.95,
                       preset="round_soft", spacing=7, jitter=0.50,
                       angle=18, gradient_angle=0, hardness=0.62,
                       blend="normal", seed=401, edge=True,
                       edge_inset=5)
# Chiseled facets and dark cracks.
for i in range(860):
    x, y = rand_in_poly(island, r)
    depth = max(0.0, min(1.0, (y - 665) / 165.0))
    c = mix((108, 83, 86), (24, 36, 51), r.uniform(0.0, 1.0))
    if r.random() < 0.34:
        c = mix(c, (158, 117, 105), 0.42)
    c = color_jit(c, 8, r)
    ang = r.uniform(-70, 70)
    ln = r.uniform(10, 43) + depth * r.uniform(0, 24)
    x2 = x + math.cos(math.radians(ang)) * ln
    y2 = y + math.sin(math.radians(ang)) * ln
    add("cliff", [[x, y, r.uniform(0.6, 1.0)], [x2, y2, r.uniform(0.4, 0.9)]],
        r.uniform(4, 18) + depth * 6, c, r.uniform(0.16, 0.48),
        "flat" if i % 3 else "bristle", r.uniform(0.45, 0.9))
# Two frontal buttresses give the island depth and a real shelf for the tower.
left_crag = [(604, 760), (630, 718), (664, 688), (696, 704),
             (704, 738), (686, 779), (654, 807), (618, 798)]
right_crag = [(836, 700), (870, 708), (904, 736), (930, 777),
              (914, 817), (875, 830), (846, 803), (828, 760)]
for cp, cs, c1, c2 in [
    (left_crag, 721, (91, 73, 81), (30, 43, 57)),
    (right_crag, 722, (78, 66, 79), (21, 34, 49)),
]:
    shape_ops.fill_polygon(doc, "cliff", cp, color=c1, color2=c2,
                           size=10, opacity=0.86, preset="round_soft",
                           spacing=6, jitter=0.5, angle=55,
                           gradient_angle=0, hardness=0.55,
                           blend="normal", seed=cs, edge=True,
                           edge_inset=3)
    for i in range(170):
        x, y = rand_in_poly(cp, r)
        c = color_jit(mix((111, 86, 88), (28, 39, 53), r.uniform(0, 1)), 9, r)
        ang = r.uniform(-65, 65)
        ln = r.uniform(8, 34)
        add("cliff", [[x, y, 0.8], [x + math.cos(math.radians(ang)) * ln,
                                   y + math.sin(math.radians(ang)) * ln, 0.4]],
            r.uniform(3, 13), c, r.uniform(0.12, 0.38), "flat", 0.65)

# Warm upper rim: sky and lamp grazing the wet stone.
for i in range(360):
    x = r.uniform(620, 930)
    y = 650 + 0.0025 * (x - 775) ** 2 + r.uniform(-5, 14)
    c = color_jit(mix((154, 111, 96), (223, 166, 120), r.uniform(0, 0.7)), 8, r)
    ln = r.uniform(6, 28)
    add("cliff_light", [[x, y, 0.85], [x + ln, y + r.uniform(-2, 3), 0.35]],
        r.uniform(1.6, 6.5), c, r.uniform(0.055, 0.22), "round_soft", 0.22)
# Wet/shadowed lower skirt.
for i in range(190):
    x, y = rand_in_poly(island, r)
    if y < 770:
        continue
    c = color_jit((18, 30, 45), 7, r)
    stroke_xy("cliff", x, y, x + r.uniform(8, 38), y + r.uniform(-2, 5),
              r.uniform(3, 11), c, r.uniform(0.12, 0.4), "dry", 0.45)
# Boulders: large readable masses first, then facets and lit tops.
boulders = [
    ([(608, 746), (630, 716), (661, 700), (690, 710), (699, 739),
      (681, 767), (650, 784), (620, 773)], 731, (104, 82, 84), (31, 45, 59)),
    ([(647, 770), (669, 741), (700, 733), (727, 749), (731, 781),
      (710, 806), (678, 807), (654, 790)], 732, (92, 75, 82), (29, 43, 57)),
    ([(807, 697), (836, 690), (869, 700), (894, 723), (905, 755),
      (887, 782), (859, 774), (837, 747), (820, 722)], 733, (92, 76, 82), (27, 41, 55)),
    ([(834, 757), (862, 738), (895, 744), (917, 769), (922, 798),
      (901, 820), (871, 812), (849, 790)], 734, (83, 70, 80), (24, 37, 52)),
    ([(708, 774), (740, 749), (780, 746), (818, 766), (828, 793),
      (807, 816), (767, 821), (731, 802)], 735, (85, 72, 80), (26, 40, 55)),
]
for bp, bseed, bc1, bc2 in boulders:
    shape_ops.fill_polygon(doc, "cliff", bp, color=bc1, color2=bc2,
                           size=8.5, opacity=0.91, preset="round_soft",
                           spacing=5.2, jitter=0.42, angle=32,
                           gradient_angle=0, hardness=0.58,
                           blend="normal", seed=bseed, edge=True,
                           edge_inset=3)
    bx0, by0, bx1, by1 = poly_bbox(bp)
    for i in range(95):
        x, y = rand_in_poly(bp, r)
        c = color_jit(mix((124, 95, 91), (33, 47, 61), r.uniform(0, 1)), 8, r)
        ang = r.uniform(-60, 60)
        ln = r.uniform(6, 28)
        add("cliff", [[x, y, 0.8],
                      [x + math.cos(math.radians(ang)) * ln,
                       y + math.sin(math.radians(ang)) * ln, 0.4]],
            r.uniform(2.5, 10), c, r.uniform(0.10, 0.34),
            "flat", r.uniform(0.45, 0.8))
    # A warm wet rim on the upper third of each boulder.
    for i in range(115):
        x, y = rand_in_poly(bp, r)
        if y > by0 + (by1 - by0) * 0.46:
            continue
        c = color_jit(mix((151, 108, 96), (226, 170, 122), r.uniform(0, 0.7)), 8, r)
        add("cliff_light", [[x, y, 0.8], [x + r.uniform(4, 19),
                                          y + r.uniform(-2, 2), 0.35]],
            r.uniform(1.6, 6.2), c, r.uniform(0.06, 0.20),
            "round_soft", 0.2)
    # Contour light along the exposed upper edges.
    for k in range(4):
        ax, ay = bp[k]; bx, by = bp[k + 1]
        for j in range(11):
            t = (j + r.uniform(0.1, 0.9)) / 11.0
            x = ax + (bx - ax) * t + r.uniform(-1.5, 1.5)
            y = ay + (by - ay) * t + r.uniform(-1.5, 1.5)
            add("cliff_light", [[x, y, 0.8],
                                [x + r.uniform(2, 10), y + r.uniform(-1, 2), 0.3]],
                r.uniform(1.2, 4.2), color_jit((218, 157, 114), 7, r),
                r.uniform(0.06, 0.18), "round_soft", 0.22)
# A horizontal shelf just under the lighthouse base.
shelf = [[671, 688], [838, 688], [864, 728], [651, 728]]
shape_ops.fill_polygon(doc, "cliff", shelf, color=(90, 74, 76),
                       color2=(44, 49, 59), size=8, opacity=0.92,
                       preset="round_soft", spacing=5, jitter=0.4,
                       angle=4, gradient_angle=0, hardness=0.62,
                       blend="normal", seed=402, edge=True, edge_inset=2)

# ---------------------------------------------------------------- lighthouse
body = [[713, 246], [803, 246], [833, 701], [682, 701]]
tower_left = lambda y: 713 + (682 - 713) * (y - 246) / (701 - 246)
tower_right = lambda y: 803 + (833 - 803) * (y - 246) / (701 - 246)

# plinth first, then body
plinth = [[693, 687], [821, 687], [842, 730], [674, 730]]
shape_ops.fill_polygon(doc, "tower", plinth, color=(105, 86, 82),
                       color2=(45, 48, 58), size=11, opacity=0.96,
                       preset="round_soft", spacing=5, jitter=0.35,
                       angle=4, gradient_angle=0, hardness=0.68,
                       blend="normal", seed=501, edge=True, edge_inset=2)
# Opaque underpaint gives the tower a solid body before texture.
shape_ops.fill_polygon(doc, "tower", body, color=(150, 111, 91),
                       color2=(118, 89, 87), size=14, opacity=1.0,
                       preset="round_hard", spacing=5.5, jitter=0.10,
                       angle=88, gradient_angle=0, hardness=0.82,
                       blend="normal", seed=500, edge=False)
shape_ops.fill_polygon(doc, "tower", body, color=(191, 142, 101),
                       color2=(124, 94, 90), size=6, opacity=0.95,
                       preset="round_soft", spacing=5.2, jitter=0.14,
                       angle=88, gradient_angle=0, hardness=0.62,
                       blend="normal", seed=502, edge=True, edge_inset=6)
# left lit face and right shadow face
lit_face = [[713, 246], [750, 246], [734, 701], [682, 701]]
right_face = [[786, 246], [803, 246], [833, 701], [790, 701]]
shape_ops.fill_polygon(doc, "tower", lit_face, color=(222, 172, 119),
                       color2=(194, 143, 105), size=3.6, opacity=0.58,
                       preset="round_soft", spacing=3.4, jitter=0.18,
                       angle=88, gradient_angle=90, hardness=0.58,
                       blend="normal", seed=503, edge=False)
shape_ops.fill_polygon(doc, "tower", right_face, color=(74, 60, 72),
                       color2=(53, 51, 66), size=3.6, opacity=0.56,
                       preset="round_soft", spacing=3.4, jitter=0.22,
                       angle=88, gradient_angle=90, hardness=0.60,
                       blend="normal", seed=504, edge=False)
# Vertical knife strokes: calm, broad, and mostly parallel. This is the
# tower's impasto rather than a random cross-hatch.
for i in range(380):
    y = r.uniform(252, 690)
    x = r.uniform(tower_left(y) + 5, tower_right(y) - 5)
    t = (x - tower_left(y)) / max(1.0, (tower_right(y) - tower_left(y)))
    c = mix((224, 175, 121), (83, 66, 75), t * 0.95)
    c = color_jit(c, 7, r)
    ln = r.uniform(16, 64)
    add("tower", [[x, y, r.uniform(0.65, 1.0)],
                  [x + r.uniform(-2.0, 2.0), y + ln, r.uniform(0.30, 0.75)]],
        r.uniform(4, 13), c, r.uniform(0.10, 0.30),
        "flat" if i % 3 else "round_soft", r.uniform(0.42, 0.78))
# A few dry chips and horizontal plaster scars, not an all-over texture.
for i in range(95):
    y = r.uniform(260, 690)
    x = r.uniform(tower_left(y) + 10, tower_right(y) - 30)
    c = color_jit((113, 86, 86), 7, r)
    stroke_xy("tower", x, y, x + r.uniform(8, 24), y + r.uniform(-1, 2),
              r.uniform(1.2, 3.2), c, r.uniform(0.05, 0.16), "dry", 0.42)
# subtle central sunlit plane
center_face = [[750, 250], [786, 250], [790, 700], [734, 700]]
shape_ops.fill_polygon(doc, "tower", center_face, color=(203, 154, 111),
                       color2=(147, 108, 95), size=10, opacity=0.18,
                       preset="round_soft", spacing=10, jitter=0.18,
                       angle=88, gradient_angle=90, hardness=0.45,
                       blend="normal", seed=505, edge=False)

# A few genuinely broad knife-strokes to read as impasto at full size.
for i in range(130):
    y = r.uniform(255, 688)
    x = r.uniform(tower_left(y) + 8, tower_right(y) - 12)
    t = (x - tower_left(y)) / max(1.0, (tower_right(y) - tower_left(y)))
    c = mix((238, 192, 138), (91, 72, 80), t * 0.92)
    c = color_jit(c, 7, r)
    ln = r.uniform(38, 112)
    add("tower", [[x, y, r.uniform(0.5, 0.9)],
                  [x + r.uniform(-3.5, 3.5), y + ln, r.uniform(0.25, 0.6)]],
        r.uniform(14, 27), c, r.uniform(0.035, 0.12),
        "flat" if i % 2 else "round_soft", r.uniform(0.32, 0.62))

# Edge accents read as a clean silhouette at 25% size.
for i in range(92):
    y = r.uniform(252, 700)
    xr = tower_right(y) - r.uniform(1.0, 3.2)
    add("tower_detail", [[xr, y, 0.75], [xr + r.uniform(-0.5, 0.5), y + r.uniform(3, 9), 0.45]],
        r.uniform(1.4, 3.2), (67, 56, 67), r.uniform(0.10, 0.26), "liner", 0.45)
for i in range(68):
    y = r.uniform(254, 698)
    xl = tower_left(y) + r.uniform(1.0, 3.0)
    add("tower_light", [[xl, y, 0.7], [xl + r.uniform(-0.5, 0.5), y + r.uniform(3, 8), 0.3]],
        r.uniform(1.1, 2.6), (204, 151, 108), r.uniform(0.04, 0.13), "liner", 0.35)

# red bands, clipped by the tower's taper
def band_poly(y0, y1):
    return [[tower_left(y0), y0], [tower_right(y0), y0],
            [tower_right(y1), y1], [tower_left(y1), y1]]

for y0, y1, cc1, cc2 in [(344, 381, (149, 64, 65), (104, 49, 61)),
                         (527, 565, (139, 60, 66), (95, 49, 62))]:
    shape_ops.fill_polygon(doc, "tower_detail", band_poly(y0, y1),
                           color=cc1, color2=cc2, size=7.5, opacity=0.96,
                           preset="round_soft", spacing=4.4, jitter=0.28,
                           angle=88, gradient_angle=90, hardness=0.62,
                           blend="normal", seed=520 + y0, edge=False)
    for i in range(55):
        yy = r.uniform(y0 + 2, y1 - 2)
        x = r.uniform(tower_left(yy) + 3, tower_left(yy) + 42)
        c = color_jit((196, 102, 82), 8, r)
        stroke_xy("tower_detail", x, yy, x + r.uniform(8, 26), yy + r.uniform(-1, 2),
                  r.uniform(1.4, 4.2), c, r.uniform(0.12, 0.34), "dry", 0.4)
    # dark lip of each band
    for i in range(26):
        yy = y1 - r.uniform(0, 4)
        x = r.uniform(tower_left(yy) + 6, tower_right(yy) - 8)
        dab("tower_detail", x, yy, r.uniform(2, 5), (63, 42, 51), r.uniform(0.2, 0.5), "dry", 0.4)

# door and windows
door = [[741, 701], [741, 663], [744, 652], [750, 645], [757, 642],
        [764, 645], [770, 652], [773, 663], [773, 701]]
shape_ops.fill_polygon(doc, "tower_detail", door, color=(46, 37, 49),
                       color2=(28, 29, 41), size=3.4, opacity=0.92,
                       preset="flat", spacing=2.5, jitter=0.4,
                       angle=88, gradient_angle=0, hardness=0.62,
                       blend="normal", seed=541, edge=True, edge_inset=1)
for i in range(40):
    yy = r.uniform(650, 699)
    x = 742 + r.uniform(0, 29)
    stroke_xy("tower_detail", x, yy, x + r.uniform(-1, 2), yy + r.uniform(3, 12),
              r.uniform(1, 2.5), color_jit((100, 68, 63), 7, r),
              r.uniform(0.1, 0.25), "dry", 0.25)
# door frame highlight
stroke_xy("tower_light", 741, 701, 741, 657, 1.8, (211, 154, 109), 0.25, "liner", 0.4)
stroke_xy("tower_light", 741, 657, 749, 646, 1.5, (211, 154, 109), 0.22, "liner", 0.4)

def window(cx, y0, y1, w):
    poly = [[cx - w/2, y1], [cx - w/2, y0 + 5], [cx - w/2 + 3, y0],
            [cx + w/2 - 3, y0], [cx + w/2, y0 + 5], [cx + w/2, y1]]
    shape_ops.fill_polygon(doc, "tower_detail", poly, color=(36, 47, 56),
                           color2=(24, 32, 44), size=3, opacity=0.9,
                           preset="flat", spacing=2.2, jitter=0.35,
                           angle=90, gradient_angle=0, hardness=0.6,
                           blend="normal", seed=560 + cx, edge=True, edge_inset=1)
    for i in range(18):
        yy = r.uniform(y0 + 4, y1 - 2)
        xx = cx - w/2 + r.uniform(0, 2.5)
        stroke_xy("tower_light", xx, yy, xx + r.uniform(-0.5, 0.5), yy + r.uniform(2, 8),
                  1.2, (189, 139, 103), r.uniform(0.06, 0.16), "liner", 0.35)
    # warm sill
    for i in range(12):
        xx = cx - w/2 - 2 + r.uniform(0, w + 4)
        stroke_xy("tower_light", xx, y1, xx + r.uniform(2, 8), y1,
                  1.3, (209, 157, 113), r.uniform(0.08, 0.2), "flat", 0.4)

window(758, 408, 460, 17)
window(760, 588, 624, 14)

# A broad two-step stone pedestal makes the tower base explicit.
pedestal_cap = [[670, 701], [844, 701], [859, 722], [655, 722]]
shape_ops.fill_polygon(doc, "tower_detail", pedestal_cap,
                       color=(141, 113, 94), color2=(74, 68, 72),
                       size=7, opacity=0.97, preset="round_soft",
                       spacing=4.6, jitter=0.28, angle=2,
                       gradient_angle=0, hardness=0.62,
                       blend="normal", seed=571, edge=True, edge_inset=2)
pedestal_face = [[655, 722], [859, 722], [846, 746], [669, 746]]
shape_ops.fill_polygon(doc, "tower_detail", pedestal_face,
                       color=(96, 82, 78), color2=(57, 56, 64),
                       size=7, opacity=0.97, preset="round_soft",
                       spacing=4.6, jitter=0.25, angle=2,
                       gradient_angle=90, hardness=0.68,
                       blend="normal", seed=572, edge=True, edge_inset=2)
# warm capstone rim and left-side edge light
for i in range(40):
    x = r.uniform(672, 842)
    y = 703 + r.uniform(-1.5, 1.5)
    stroke_xy("tower_light", x, y, x + r.uniform(7, 26), y + r.uniform(-1, 1),
              r.uniform(1.4, 3.3), (226, 174, 124), r.uniform(0.09, 0.24),
              "round_soft", 0.28)
for i in range(24):
    y = r.uniform(724, 744)
    x = 658 + (y - 722) * 0.45 + r.uniform(-1, 2)
    stroke_xy("tower_light", x, y, x + r.uniform(1, 4), y + r.uniform(1, 5),
              r.uniform(1, 2.4), (184, 135, 99), r.uniform(0.04, 0.12),
              "flat", 0.35)
# Warm stone planes on the pedestal face.
for i in range(60):
    y = r.uniform(724, 744)
    x = r.uniform(670, 842)
    c = color_jit(mix((116, 95, 90), (63, 59, 67), r.uniform(0, 1)), 7, r)
    stroke_xy("tower_detail", x, y, x + r.uniform(10, 32), y + r.uniform(-1, 1),
              r.uniform(1.5, 4.0), c, r.uniform(0.05, 0.18),
              "round_soft", 0.4)
# Stone block seams on the front face.
for i in range(22):
    y = r.uniform(726, 743); x = r.uniform(675, 835)
    stroke_xy("tower_detail", x, y, x + r.uniform(8, 24), y,
              r.uniform(0.8, 1.8), (45, 47, 56), r.uniform(0.08, 0.22),
              "liner", 0.4)

# gallery deck + supports
gallery = [[693, 240], [825, 240], [831, 251], [687, 251]]
shape_ops.fill_polygon(doc, "tower_detail", gallery, color=(54, 56, 65),
                       color2=(27, 31, 41), size=7, opacity=0.96,
                       preset="flat", spacing=3.6, jitter=0.35,
                       angle=0, gradient_angle=0, hardness=0.75,
                       blend="normal", seed=580, edge=True, edge_inset=1)
for bx in (706, 812):
    bracket = [[bx - 8, 250], [bx + 8, 250], [bx + 2, 274]]
    shape_ops.fill_polygon(doc, "tower_detail", bracket, color=(41, 43, 52),
                           color2=(24, 29, 40), size=4, opacity=0.9,
                           preset="flat", spacing=2.8, jitter=0.3,
                           angle=90, gradient_angle=0, hardness=0.65,
                           blend="normal", seed=581, edge=False)
# railing
for yy in (213, 225, 239):
    stroke_xy("tower_detail", 697, yy, 821, yy, 1.8 + (yy == 239) * 2.2,
              (27, 31, 41), 0.92, "liner", 0.55)
for x in range(700, 822, 9):
    stroke_xy("tower_detail", x, 213, x, 239, 1.4, (31, 34, 43), 0.8, "liner", 0.5)
for i in range(70):
    x = r.uniform(696, 822)
    stroke_xy("tower_light", x, 211 + r.uniform(-1, 1), x + r.uniform(4, 14),
              211 + r.uniform(-1, 1), r.uniform(1, 2.2), (205, 153, 111),
              r.uniform(0.04, 0.14), "liner", 0.3)

# lantern room back / glass panes
lantern = [[724, 169], [792, 169], [797, 235], [719, 235]]
# Solid dark interior behind the glass; the panes are painted on top.
shape_ops.fill_polygon(doc, "tower_detail", lantern, color=(47, 41, 44),
                       color2=(27, 31, 39), size=11, opacity=1.0,
                       preset="flat", spacing=4.2, jitter=0.10,
                       angle=90, gradient_angle=0, hardness=0.9,
                       blend="normal", seed=599, edge=True, edge_inset=1)
shape_ops.fill_polygon(doc, "tower_detail", lantern, color=(69, 54, 51),
                       color2=(40, 41, 50), size=4, opacity=0.92,
                       preset="bristle", spacing=3.8, jitter=0.4,
                       angle=90, gradient_angle=0, hardness=0.5,
                       blend="normal", seed=600, edge=True, edge_inset=1)
for x0, x1 in [(726, 746), (752, 768), (774, 793)]:
    pane = [[x0, 173], [x1, 173], [x1 + 1, 230], [x0 - 1, 230]]
    shape_ops.fill_polygon(doc, "tower_detail", pane, color=(94, 65, 54),
                           color2=(54, 50, 53), size=3, opacity=0.55,
                           preset="round_soft", spacing=3.2, jitter=0.35,
                           angle=90, gradient_angle=90, hardness=0.35,
                           blend="normal", seed=610 + x0, edge=False)
# broad warm interior so the whole lantern reads as the source
for i in range(34):
    x = r.normal(760, 17); y = r.normal(200, 22)
    dab("lamp_glow", x, y, r.uniform(14, 58), (255, 196, 116),
        r.uniform(0.025, 0.065), "round_soft", 0.12)
# panes with light, painted on a screen layer
for x0, x1 in [(728, 746), (753, 767), (775, 792)]:
    pane = [[x0, 174], [x1, 174], [x1 + 1, 229], [x0 - 1, 229]]
    shape_ops.fill_polygon(doc, "lamp_glow", pane, color=(255, 210, 122),
                           color2=(244, 148, 94), size=4, opacity=0.22,
                           preset="round_soft", spacing=4, jitter=0.5,
                           angle=90, gradient_angle=90, hardness=0.3,
                           blend="screen", seed=620 + x0, edge=False)
# mullions and horizontal rails
for x in (721, 748, 771, 795):
    stroke_xy("tower_detail", x, 169, x, 236, 3.3, (27, 31, 39), 0.95, "liner", 0.6)
for yy in (170, 185, 216, 234):
    stroke_xy("tower_detail", 719, yy, 797, yy, 2.2, (30, 33, 41), 0.92, "liner", 0.55)
# glass highlights
for i in range(110):
    x = r.uniform(727, 791); y = r.uniform(176, 226)
    c = color_jit((255, 232, 166), 10, r)
    stroke_xy("lamp_glow", x, y, x + r.uniform(-1, 1), y + r.uniform(2, 9),
              r.uniform(1.0, 3.2), c, r.uniform(0.06, 0.2), "round_soft", 0.15)

# roof
roof = [[718, 169], [798, 169], [789, 149], [758, 111], [727, 145]]
# Opague iron roof underlayer so no sky shows through the brush gaps.
shape_ops.fill_polygon(doc, "tower_detail", roof, color=(23, 35, 46),
                       color2=(16, 27, 39), size=11, opacity=1.0,
                       preset="flat", spacing=4.1, jitter=0.10,
                       angle=18, gradient_angle=90, hardness=0.9,
                       blend="normal", seed=639, edge=True, edge_inset=1)
shape_ops.fill_polygon(doc, "tower_detail", roof, color=(34, 49, 61),
                       color2=(18, 30, 43), size=5, opacity=0.96,
                       preset="bristle", spacing=3.6, jitter=0.4,
                       angle=18, gradient_angle=90, hardness=0.7,
                       blend="normal", seed=640, edge=True, edge_inset=1)
for i in range(80):
    y = r.uniform(118, 168)
    # left rim and some lit roof tiles
    x = 723 + (y - 145) * 0.35 + r.uniform(-2, 12)
    stroke_xy("tower_light", x, y, x + r.uniform(1, 12), y + r.uniform(-2, 2),
              r.uniform(1.2, 3.4), (209, 151, 101), r.uniform(0.05, 0.17),
              "bristle", 0.35)
# finial
stroke_xy("tower_detail", 758, 111, 758, 100, 2.8, (42, 46, 53), 0.9, "liner", 0.6)
dab("tower_detail", 758, 99, 7.5, (48, 53, 59), 0.9, "round_hard", 0.6)
dab("tower_light", 757, 97, 3.5, (210, 160, 112), 0.22, "round_soft", 0.15)
# dark tower edge accents (subtle, not a comic outline)
for i in range(55):
    y = r.uniform(252, 695)
    xx = tower_right(y) - r.uniform(0, 3)
    dab("tower_detail", xx, y, r.uniform(1.2, 3.2), (54, 48, 60),
        r.uniform(0.08, 0.24), "dry", 0.3)

# ---------------------------------------------------------------- lamp
# The single highest-intensity focus: a tiny near-white core plus controlled
# soft bloom. Everything else stays below this.
for rad, op, col in [(250, 0.016, (255, 190, 110)), (175, 0.025, (255, 207, 134)),
                     (115, 0.04, (255, 225, 166)), (72, 0.07, (255, 236, 191)),
                     (38, 0.11, (255, 245, 210))]:
    dab("lamp_glow", 760, 199, rad, col, op, "round_soft", 0.08)
for i in range(90):
    ang = r.uniform(0, math.pi * 2)
    rad = abs(r.normal(0, 29))
    x = 760 + math.cos(ang) * rad
    y = 199 + math.sin(ang) * rad * 0.8
    dab("lamp_glow", x, y, r.uniform(4, 24), color_jit((255, 220, 151), 9, r),
        r.uniform(0.035, 0.1), "round_soft", 0.1)
# core / filament
dab("lamp_glow", 760, 199, 19, (255, 250, 220), 0.78, "round_soft", 0.08)
dab("lamp_glow", 760, 199, 9, (255, 255, 245), 0.95, "round_hard", 0.25)
dab("lamp_glow", 760, 199, 4.5, (255, 255, 255), 1.0, "round_hard", 0.4)

# ---------------------------------------------------------------- contact
# Tower-on-plinth and plinth-on-rock shadows.
for i in range(95):
    x = r.uniform(690, 835)
    y = r.uniform(700, 710)
    stroke_xy("contact_dark", x, y, x + r.uniform(8, 52), y + r.uniform(-1, 4),
              r.uniform(4, 14), (23, 26, 37), r.uniform(0.04, 0.16),
              "round_soft", 0.25)
for i in range(72):
    x = r.uniform(680, 850)
    y = r.uniform(744, 758)
    stroke_xy("contact_dark", x, y, x + r.uniform(12, 50), y + r.uniform(-1, 3),
              r.uniform(5, 15), (17, 24, 36), r.uniform(0.035, 0.14),
              "round_soft", 0.3)
# cast shadow from the pedestal across the right-hand rock shelf
for i in range(80):
    y = r.uniform(724, 772)
    x = r.uniform(846, 945)
    stroke_xy("contact_dark", x, y, x + r.uniform(18, 62), y + r.uniform(1, 6),
              r.uniform(6, 22), (18, 25, 38), r.uniform(0.035, 0.13),
              "round_soft", 0.28)
# waterline shadow around the island
for i in range(140):
    x = r.uniform(608, 950)
    y = 816 - 0.00145 * (x - 780) ** 2 + r.uniform(-12, 10)
    stroke_xy("contact_dark", x, y, x + r.uniform(8, 38), y + r.uniform(-2, 3),
              r.uniform(3, 10), (14, 21, 34), r.uniform(0.03, 0.13),
              "round_soft", 0.3)

# Foam collar: contact between rock, water and the tower reflection.
rr = np.random.default_rng(801)
for i in range(265):
    x = rr.uniform(606, 950)
    y = 816 - 0.00145 * (x - 780) ** 2 + rr.normal(0, 7)
    c = mix((240, 233, 208), (146, 184, 190), rr.uniform(0.0, 0.55))
    c = color_jit(c, 9, rr)
    ln = rr.uniform(3, 18)
    stroke_xy("contact_foam", x, y, x + ln, y + rr.uniform(-2, 2),
              rr.uniform(0.9, 3.2), c, rr.uniform(0.06, 0.33),
              "sponge" if i % 3 else "round_soft", rr.uniform(0.1, 0.5))
# a few crest arcs that read as water wrapping the rock
for i in range(24):
    x = rr.uniform(625, 920)
    y = 816 - 0.00145 * (x - 780) ** 2 + rr.uniform(-5, 16)
    ln = rr.uniform(28, 100)
    stroke_xy("contact_foam", x - ln * 0.5, y, x + ln * 0.5, y + rr.uniform(-3, 3),
              rr.uniform(1.2, 3.5), (228, 234, 218), rr.uniform(0.12, 0.34),
              "round_soft", 0.2, rr.uniform(-5, 5))
# warm reflection spots in the foam (from the tower/lamp), kept weak
for i in range(80):
    x = rr.uniform(700, 890)
    y = rr.uniform(835, 890)
    c = color_jit((226, 154, 103), 10, rr)
    stroke_xy("sea_glints", x, y, x + rr.uniform(5, 23), y + rr.uniform(-1, 2),
              rr.uniform(1.0, 3.2), c, rr.uniform(0.02, 0.08), "round_soft", 0.15)

# ---------------------------------------------------------------- foreground
# Near breakers frame the bottom, leaving the island's foam visible.
left_wave = [(-20, 790), (130, 784), (300, 812), (430, 870), (465, 930), (-20, 930)]
right_wave = [(1000, 810), (1130, 792), (1230, 805), (1230, 930), (910, 930)]
for poly, seed in [(left_wave, 901), (right_wave, 902)]:
    shape_ops.fill_polygon(doc, "foreground", poly, color=(20, 35, 54),
                           color2=(10, 22, 38), size=12, opacity=0.9,
                           preset="flat", spacing=8, jitter=0.55,
                           angle=-3, gradient_angle=90, hardness=0.7,
                           blend="normal", seed=seed, edge=False)
    for i in range(120):
        x, y = rand_in_poly(poly, r)
        c = color_jit((54, 76, 90), 10, r)
        stroke_xy("foreground", x, y, x + r.uniform(12, 60), y + r.uniform(-3, 4),
                  r.uniform(2, 9), c, r.uniform(0.08, 0.28), "bristle", 0.45)
for i in range(280):
    y = r.uniform(820, 905)
    x = r.uniform(-50, W + 50)
    # keep the middle water open enough to see the island contact
    if 470 < x < 930 and y < 860:
        continue
    c = mix(sea_color(y), (137, 159, 163), r.uniform(0.2, 0.65))
    ln = r.uniform(15, 70)
    stroke_xy("foreground", x, y, x + ln, y + r.uniform(-5, 5),
              r.uniform(1.5, 5.5), color_jit(c, 8, r), r.uniform(0.06, 0.24),
              "round_soft", 0.25, r.uniform(-7, 7))
# open-water streaks at bottom centre
for i in range(230):
    y = r.uniform(835, 900); x = r.uniform(430, 960)
    c = color_jit((37, 66, 83), 10, r)
    stroke_xy("foreground", x, y, x + r.uniform(18, 90), y + r.uniform(-4, 4),
              r.uniform(1.5, 5), c, r.uniform(0.05, 0.18), "dry", 0.5)

# ---------------------------------------------------------------- vignette
for cx, cy, n in [(60, 70, 28), (1140, 70, 28), (80, 830, 24), (1120, 830, 24),
                  (600, -40, 20)]:
    for i in range(n):
        x = cx + r.normal(0, 120); y = cy + r.normal(0, 90)
        dab("vignette", x, y, r.uniform(120, 320), (18, 18, 31),
            r.uniform(0.012, 0.035), "round_soft", 0.08)
# cool glaze across the shadow side, very light
for i in range(24):
    x = r.uniform(650, 1200); y = r.uniform(0, 150)
    dab("vignette", x, y, r.uniform(160, 300), (40, 50, 73),
        r.uniform(0.008, 0.02), "round_soft", 0.1)

# ---------------------------------------------------------------- dump
OUT.mkdir(parents=True, exist_ok=True)
plan_path = OUT / "lighthouse.plan.json"
blends = dict(LAYERS)
layer_order = [n for n, _ in LAYERS]
n = plan_io.dump_plan(str(plan_path), W, H, doc["strokes"],
                      layer_blends=blends, layer_order=layer_order)
plan_bytes = plan_path.stat().st_size
meta = {
    "plan": str(plan_path),
    "canvas": [W, H],
    "strokes": int(n),
    "layers": len(layer_order),
    "bytes": int(plan_bytes),
    "presets": sorted({s["preset"] for s in doc["strokes"]}),
}
print(json.dumps(meta, ensure_ascii=False))
