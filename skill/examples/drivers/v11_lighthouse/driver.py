# -*- coding: utf-8 -*-
"""cold-lighthouse-v11 — original procedural impasto plan.

Dusk, cold-shadow lighthouse on a warm-lit headland. 1200x900.
Generated from analytic fields + explicit geometry, then rendered by
svg-rebuild-and-paint's deterministic thick-paint engine.
"""
from pathlib import Path
import math, sys, json, time

ROOT = Path(__file__).resolve().parent
SKILL = Path(r"D:\deepseek-harness-dsh-v0.2.2rc1\.dsh-home\skills\svg-rebuild-and-paint")
sys.path.insert(0, str(SKILL / "scripts"))
import cv2
import numpy as np
import plan_io
import shape_ops
import render_util as RU

W, H = 1200, 900
HOR = 552
SUNX, SUNY, SUNR = 348, 500, 38
LXC, LTOP, LBASE = 940, 222, 654

rng = np.random.default_rng(20260519)
doc = {"canvas": [W, H], "layers": [], "strokes": []}


def mix(a, b, t):
    a = np.asarray(a, np.float32)
    b = np.asarray(b, np.float32)
    return a + (b - a) * float(np.clip(t, 0, 1))


def smoothstep(a, b, x):
    t = np.clip((x - a) / (b - a + 1e-9), 0, 1)
    return t * t * (3.0 - 2.0 * t)


def add(layer, pts, size, color, opacity=1.0, preset="bristle", hardness=0.55, tag=None):
    pp = []
    for p in pts:
        pp.append([float(p[0]), float(p[1]), float(p[2]) if len(p) > 2 else 1.0])
    c = [float(np.clip(v, 0, 255)) for v in color]
    s = {"layer": layer, "preset": preset, "size": float(max(0.6, size)),
         "opacity": float(np.clip(opacity, 0, 1)), "color": c,
         "hardness": float(np.clip(hardness, 0, 1)), "points": pp}
    if tag:
        s["tag"] = tag
    doc["strokes"].append(s)


def add_line(layer, pts, size, color, opacity=0.8, preset="bristle", hardness=0.55, tag=None):
    add(layer, pts, size, color, opacity, preset, hardness, tag)


def cjit(c, amp=6, rng=rng):
    c = np.asarray(c, np.float32)
    return np.clip(c + rng.normal(0, amp, 3), 0, 255)


def fbm_field(seed, gx=10, gy=6, octaves=5, gain=0.55):
    r = np.random.default_rng(seed)
    out = np.zeros((H, W), np.float32)
    amp, tot = 1.0, 0.0
    for o in range(octaves):
        gw = max(2, int(gx * (2 ** o)))
        gh = max(2, int(gy * (2 ** o)))
        low = r.random((gh, gw)).astype(np.float32)
        up = cv2.resize(low, (W, H), interpolation=cv2.INTER_CUBIC)
        out += up * amp
        tot += amp
        amp *= gain
    out /= max(tot, 1e-6)
    out -= out.min()
    out /= max(float(out.max()), 1e-6)
    return out


def sample(arr, x, y):
    xi = int(np.clip(round(x), 0, W - 1))
    yi = int(np.clip(round(y), 0, H - 1))
    return arr[yi, xi]


# ----------------------------------------------------------------------------
# 1. analytic sky field (reference for colour sampling, not a bitmap layer)
# ----------------------------------------------------------------------------
yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
sv = yy / float(HOR)
u = xx / float(W - 1)

stops = np.array([0.00, 0.16, 0.32, 0.48, 0.64, 0.78, 0.89, 1.00], np.float32)
sky_cols = np.array([
    [11, 15, 36], [22, 28, 60], [43, 42, 84], [75, 54, 99],
    [120, 68, 108], [179, 94, 112], [231, 137, 113], [255, 190, 136],
], np.float32)
sky = np.empty((H, W, 3), np.float32)
for k in range(3):
    sky[..., k] = np.interp(sv, stops, sky_cols[:, k])

n_hi = fbm_field(101, 14, 5, 5, 0.54)
n_mid = fbm_field(102, 10, 4, 5, 0.56)
n_fine = fbm_field(103, 22, 8, 6, 0.52)

# cloud bands: ragged, wind-stretched, with coral undersides
def cloud_band(c0, c1, width, phase, noise, cut, xguard=0.0):
    center = c0 + (c1 - c0) * u + 0.020 * np.sin(2 * math.pi * (u * 1.35 + phase)) + 0.025 * (noise - 0.5)
    cover = np.exp(-((sv - center) / width) ** 2 * 1.55)
    rag = smoothstep(cut, cut + 0.24, 0.70 * noise + 0.30 * n_hi + 0.24 * (1 - np.minimum(1, np.abs(sv - center) / max(width, 1e-3))))
    guard = smoothstep(-0.05, 0.35, u) if xguard > 0 else 1.0
    return cover * rag * guard, center, width

m1, c1, w1 = cloud_band(0.175, 0.105, 0.090, 0.12, n_hi, 0.30)
m2, c2, w2 = cloud_band(0.455, 0.315, 0.073, 0.47, n_mid, 0.39, 0.10)
m3, c3, w3 = cloud_band(0.365, 0.475, 0.105, 0.74, n_fine, 0.36, 0.36)
m4, c4, w4 = cloud_band(0.615, 0.555, 0.055, 0.21, n_mid, 0.46, 0.18)
cloud_mask = np.clip(0.95 * m1 + 0.78 * m2 + 0.82 * m3 + 0.45 * m4, 0, 1.0)
m_sum = 0.95 * m1 + 0.78 * m2 + 0.82 * m3 + 0.45 * m4 + 1e-5
under_num = (0.95 * m1 * np.clip((sv - c1) / w1, 0, 1) +
             0.78 * m2 * np.clip((sv - c2) / w2, 0, 1) +
             0.82 * m3 * np.clip((sv - c3) / w3, 0, 1) +
             0.45 * m4 * np.clip((sv - c4) / w4, 0, 1))
cloud_under = np.clip(under_num / m_sum, 0, 1)
sun_near = np.exp(-(((xx - SUNX) / 360.0) ** 2 + ((yy - SUNY) / 250.0) ** 2))
cloud_lit = np.clip(0.10 + 0.67 * cloud_under + 0.42 * sun_near + 0.12 * (n_fine - 0.5), 0, 1)
# vectorised 4-stop cloud ramp
cshadow = np.array([30, 29, 57], np.float32)
cmid = np.array([91, 56, 89], np.float32)
clitc = np.array([220, 119, 108], np.float32)
crim = np.array([255, 190, 143], np.float32)
a = smoothstep(0.00, 0.48, cloud_lit)[..., None]
b = smoothstep(0.44, 0.80, cloud_lit)[..., None]
c = smoothstep(0.78, 1.00, cloud_lit)[..., None]
cloud_col = cshadow + (cmid - cshadow) * a
cloud_col = cloud_col + (clitc - cloud_col) * b
cloud_col = cloud_col + (crim - cloud_col) * c
cloud_alpha = np.clip(cloud_mask * 0.94, 0, 0.94)[..., None]
sky = sky * (1 - cloud_alpha) + cloud_col * cloud_alpha

# thin luminous horizon haze
haze = np.exp(-((sv - 0.955) / 0.060) ** 2)
sky = sky + haze[..., None] * np.array([45, 20, 4], np.float32) * (0.55 + 0.45 * n_mid[..., None])

# sun bloom + disc
d = np.sqrt((xx - SUNX) ** 2 + ((yy - SUNY) * 1.22) ** 2)
bloom = np.exp(-(d / 238.0) ** 2) * 0.55 + np.exp(-(d / 96.0) ** 2) * 0.70
sky = np.clip(sky + bloom[..., None] * np.array([60, 42, 16], np.float32), 0, 255)
disc = smoothstep(42.0, 34.0, d)
rim = smoothstep(58.0, 38.0, d) * (1 - disc)
sky = sky * (1 - rim[..., None] * 0.45) + np.array([255, 203, 132], np.float32) * rim[..., None] * 0.45
sky = sky * (1 - disc[..., None]) + np.array([255, 242, 194], np.float32) * disc[..., None]
core = smoothstep(25.0, 11.0, d)
sky = np.clip(sky + core[..., None] * np.array([16, 11, 5], np.float32), 0, 255)
sky = cv2.GaussianBlur(sky, (0, 0), 0.75)

# wind direction field for the sky
sky_ang = -0.13 + 0.20 * np.sin(xx / 210.0 + yy / 150.0) + 0.08 * np.sin(xx / 77.0 + 1.7)
sky_angle = np.arctan2(np.sin(sky_ang), np.cos(sky_ang))

# ----------------------------------------------------------------------------
# 2. sea field
# ----------------------------------------------------------------------------
def sea_target():
    t = np.clip((yy - HOR) / float(H - HOR), 0, 1)
    sc = np.array([[83, 106, 103], [53, 83, 84], [31, 58, 68], [18, 42, 52], [10, 27, 36]], np.float32)
    st = np.array([0.0, 0.10, 0.34, 0.64, 1.0], np.float32)
    water = np.empty((H, W, 3), np.float32)
    for k in range(3):
        water[..., k] = np.interp(t, st, sc[:, k])
    nw = fbm_field(301, 8, 7, 5, 0.55)
    phase = t * 9.0 + 0.55 * np.sin(xx / 230.0 + 0.7) + 0.85 * nw
    ripple = np.sin(2 * math.pi * phase * 2.4)
    amp = 7.0 * (0.25 + 0.75 * t)
    water += ripple[..., None] * np.array([5.5, 8.0, 9.0], np.float32) * amp[..., None] / 7.0
    # broken gold reflection under the sun
    width = 11.0 + 165.0 * (t ** 1.35)
    path = np.exp(-((xx - SUNX) / (width + 1.0)) ** 2)
    breakp = 0.42 + 0.58 * (0.5 + 0.5 * np.sin(2 * math.pi * (t * 19.0 + nw * 1.7 + xx / 97.0)))
    refl = path * breakp * (0.30 + 0.70 * np.exp(-((t - 0.42) / 0.38) ** 2))
    water += refl[..., None] * np.array([164, 100, 46], np.float32)
    # sparse cool crests
    crest = np.clip(ripple - 0.55, 0, 1) * (0.20 + 0.55 * t)
    water += crest[..., None] * np.array([19, 35, 31], np.float32)
    water = cv2.GaussianBlur(water, (0, 0), 0.7)
    return water

sea_rgb = sea_target()
sea_under = cv2.GaussianBlur(sea_rgb, (0, 0), 9.0)

# ----------------------------------------------------------------------------
# 3. opaque priming bed
# ----------------------------------------------------------------------------
# full-canvas opaque prime: many overlapping broad strokes, no uncovered paper
for i, y in enumerate(np.arange(-100, H + 100, 32)):
    for x in np.arange(-140, W + 140, 100):
        add("base", [(x - 60, y + 15 * math.sin(i + x * 0.01)), (x + 160, y - 8 * math.cos(i * 0.7))],
            180, cjit([31, 33, 50], 3), 1.0, "round_hard", 0.95)

# ----------------------------------------------------------------------------
# 4. sky painting
# ----------------------------------------------------------------------------
# smooth vertical sky bed with a true per-stroke gradient: no giant constant-colour bands
shape_ops.fill_polygon(doc, "sky", [(0, -40), (W, -40), (W, HOR + 2), (0, HOR + 2)],
                       color=(14, 18, 40), color2=(248, 164, 112),
                       size=118, spacing=38, jitter=0.26, preset="round_soft",
                       hardness=0.30, edge=False, opacity=1.0,
                       gradient_angle=90, angle=7, seed=6000)

# many low-opacity target samples: the colours mix instead of tiling into rectangles
for y in np.arange(-8, HOR + 8, 8):
    for x in np.arange(-12, W + 12, 11):
        px = x + rng.uniform(-5, 5)
        py = y + rng.uniform(-3, 3)
        if rng.random() < 0.08:
            continue
        col = sample(sky, px, py)
        cm = float(np.clip(sample(cloud_mask, px, py), 0, 1))
        ang = sample(sky_angle, px, py) + rng.normal(0, 0.026)
        ln = rng.uniform(12, 36) * (1.0 + 0.30 * cm)
        sz = rng.uniform(6.5, 14.0) * (1.0 + 0.15 * cm)
        add("sky", [(px - math.cos(ang) * ln / 2, py - math.sin(ang) * ln / 2),
                    (px + math.cos(ang) * ln / 2, py + math.sin(ang) * ln / 2)],
            sz, cjit(col, rng.uniform(1.2, 3.0)), rng.uniform(0.26, 0.46),
            "round_soft" if rng.random() < 0.94 else "round_hard", rng.uniform(0.20, 0.40))

# extra body within cloud masks, still translucent enough to blend
for _ in range(360):
    px = rng.uniform(0, W)
    py = rng.uniform(30, HOR - 12)
    cm = float(sample(cloud_mask, px, py))
    if cm < 0.20:
        continue
    c = mix(sample(sky, px, py), [255, 205, 162], 0.05 * float(sample(cloud_lit, px, py)))
    ang = sample(sky_angle, px, py) + rng.normal(0, 0.030)
    ln = rng.uniform(44, 125)
    add("cloud_edge", [(px - math.cos(ang) * ln / 2, py - math.sin(ang) * ln / 2),
                       (px + math.cos(ang) * ln / 2, py + math.sin(ang) * ln / 2)],
        rng.uniform(10, 22), cjit(c, 3), rng.uniform(0.13, 0.26), "round_soft", 0.24)

# denser small contour pass, lower contrast than before
for _ in range(500):
    px = rng.uniform(0, W)
    py = rng.uniform(20, HOR - 8)
    cm = float(sample(cloud_mask, px, py))
    if cm < 0.18:
        continue
    litv = float(sample(cloud_lit, px, py))
    c = sample(sky, px, py)
    if litv > 0.66:
        c = mix(c, [255, 206, 164], 0.09)
    ang = sample(sky_angle, px, py) + rng.normal(0, 0.038)
    ln = rng.uniform(24, 78) * (0.85 + 0.30 * cm)
    add("cloud_edge", [(px - math.cos(ang) * ln / 2, py - math.sin(ang) * ln / 2),
                       (px + math.cos(ang) * ln / 2, py + math.sin(ang) * ln / 2)],
        rng.uniform(6.0, 15.0), cjit(c, 3), rng.uniform(0.16, 0.32), "round_soft", rng.uniform(0.18, 0.34))

# a few broad, randomly placed warm/cool accents (no stitching rows)
def band_at(xx0, c0, c1, width, phase):
    uu0 = xx0 / float(W - 1)
    cc = c0 + (c1 - c0) * uu0 + 0.020 * math.sin(2 * math.pi * (uu0 * 1.35 + phase))
    return cc, width * (1.0 + 0.10 * math.sin(2 * math.pi * (uu0 * 2.1 + phase)))

bspecs = [(0.175, 0.105, 0.090, 0.12), (0.455, 0.315, 0.073, 0.47),
          (0.365, 0.475, 0.105, 0.74), (0.615, 0.555, 0.055, 0.21)]
for _ in range(90):
    c0, c1, wd, ph = bspecs[int(rng.integers(0, len(bspecs)))]
    px = rng.uniform(0, W)
    cc, ww = band_at(px, c0, c1, wd, ph)
    if rng.random() < 0.55:
        py = (cc + ww * rng.uniform(0.46, 0.70)) * HOR + rng.uniform(-6, 6)
        if py > HOR - 6 or float(sample(cloud_mask, px, py)) < 0.08:
            continue
        c = mix(sample(sky, px, py), [255, 205, 160], rng.uniform(0.22, 0.52))
    else:
        py = (cc - ww * rng.uniform(0.22, 0.48)) * HOR + rng.uniform(-6, 6)
        if py < 16 or float(sample(cloud_mask, px, py)) < 0.10:
            continue
        c = mix(sample(sky, px, py), [24, 25, 44], rng.uniform(0.18, 0.32))
    ang = sample(sky_angle, px, py) + rng.normal(0, 0.045)
    ln = rng.uniform(38, 105)
    add("cloud_edge", [(px - math.cos(ang) * ln / 2, py - math.sin(ang) * ln / 2),
                       (px + math.cos(ang) * ln / 2, py + math.sin(ang) * ln / 2)],
        rng.uniform(8, 19), cjit(c, 3), rng.uniform(0.10, 0.23), "round_soft", 0.22)

# a few tactile highlights, not a field of dashes
for _ in range(140):
    px = rng.uniform(50, 1090)
    py = rng.uniform(120, 505)
    if float(sample(cloud_mask, px, py)) < 0.23 or float(sample(cloud_lit, px, py)) < 0.58:
        continue
    c = mix(sample(sky, px, py), [255, 214, 174], rng.uniform(0.18, 0.42))
    ang = sample(sky_angle, px, py) + rng.normal(0, 0.030)
    ln = rng.uniform(12, 38)
    add("sky_glow", [(px - math.cos(ang) * ln / 2, py - math.sin(ang) * ln / 2),
                     (px + math.cos(ang) * ln / 2, py + math.sin(ang) * ln / 2)],
        rng.uniform(3.5, 8.5), cjit(c, 3), rng.uniform(0.12, 0.28), "round_soft", 0.24)

# sun body: many soft area-uniform dabs, not radial spokes
for _ in range(220):
    rr = SUNR * math.sqrt(rng.random())
    aa = rng.uniform(0, 2 * math.pi)
    px = SUNX + math.cos(aa) * rr
    py = SUNY + math.sin(aa) * rr * 0.94
    c = mix([255, 226, 160], [255, 248, 211], rng.uniform(0.25, 1.0))
    add("sky_glow", [(px, py)], rng.uniform(7, 24), c, rng.uniform(0.05, 0.18), "round_soft", 0.12)
for _ in range(75):
    rr = SUNR * 0.72 * math.sqrt(rng.random())
    aa = rng.uniform(0, 2 * math.pi)
    px = SUNX + math.cos(aa) * rr
    py = SUNY + math.sin(aa) * rr
    add("sky_glow", [(px, py), (px + rng.uniform(-5, 7), py + rng.uniform(-2, 2))],
        rng.uniform(2.5, 8), cjit([255, 247, 202], 4), rng.uniform(0.15, 0.34), "round_soft", 0.18)

# final sun body: read as a painted disc, not a blown-out cloud
for _ in range(115):
    rr = SUNR * 0.92 * math.sqrt(rng.random())
    aa = rng.uniform(0, 2 * math.pi)
    px = SUNX + math.cos(aa) * rr
    py = SUNY + math.sin(aa) * rr * 0.94
    cc = mix([255, 239, 185], [255, 252, 224], rng.uniform(0.1, 0.95))
    add("sky_glow", [(px, py)], rng.uniform(5, 15), cc, rng.uniform(0.10, 0.22), "round_soft", 0.10)
for _ in range(38):
    rr = SUNR * 0.72 * math.sqrt(rng.random())
    aa = rng.uniform(0, 2 * math.pi)
    px = SUNX + math.cos(aa) * rr
    py = SUNY + math.sin(aa) * rr
    add("sky_glow", [(px, py), (px + rng.uniform(-3, 4), py + rng.uniform(-1, 2))],
        rng.uniform(1.8, 4.2), [255, 252, 222], rng.uniform(0.20, 0.38), "round_hard", 0.28)
for _ in range(54):
    aa = rng.uniform(0, 2 * math.pi)
    rr = rng.uniform(SUNR * 0.95, SUNR * 1.38)
    px = SUNX + math.cos(aa) * rr
    py = SUNY + math.sin(aa) * rr * 0.94
    add("sky_glow", [(px, py)], rng.uniform(8, 22), [255, 190, 120], rng.uniform(0.04, 0.10), "round_soft", 0.12)

# horizon haze and distant shore
for _ in range(120):
    py = HOR - rng.uniform(10, 34)
    px = rng.uniform(-30, W + 30)
    ln = rng.uniform(80, 240)
    c = mix([255, 194, 143], [191, 106, 111], rng.uniform(0, 0.6))
    add("horizon_glow", [(px - ln / 2, py + rng.uniform(-1, 1)),
                         (px + ln / 2, py + rng.uniform(-1, 1))],
        rng.uniform(4, 9), c, rng.uniform(0.035, 0.085), "round_soft", 0.20)
# tiny far islands, deliberately hazy
for i, (x0, x1, y) in enumerate([(34, 176, HOR - 4), (220, 286, HOR - 2)]):
    for k in range(28):
        px = rng.uniform(x0, x1)
        py = y - 9 * math.sin(math.pi * (px - x0) / (x1 - x0)) + rng.uniform(-1, 1)
        add("far_shore", [(px, py), (px + rng.uniform(5, 18), py + rng.uniform(-0.5, 0.5))],
            rng.uniform(2, 5), cjit([57, 54, 78], 3), rng.uniform(0.35, 0.60), "round_hard", 0.5)
# a thin dark seam gives the sea a clean edge without a scalloped line
for x in range(-20, W + 20, 34):
    py = HOR + rng.uniform(-0.5, 0.8)
    add("horizon_glow", [(x, py), (x + rng.uniform(18, 42), py)],
        rng.uniform(0.8, 1.6), [39, 45, 60], rng.uniform(0.25, 0.50), "round_hard", 0.65)

# ----------------------------------------------------------------------------
# 5. sea painting
# ----------------------------------------------------------------------------
# dense smooth undercoat at the horizon: kills any show-through from the sky/haze layer
for y in np.arange(HOR + 8, HOR + 84, 12):
    t0 = float(np.clip((y - HOR) / (H - HOR), 0, 1))
    for x in np.arange(-260, W + 260, 300):
        px = x + rng.uniform(-70, 70)
        py = y + rng.uniform(-2, 2)
        col = sample(sea_under, px, py)
        ln = rng.uniform(360, 520)
        add("sea", [(px - ln / 2, py), (px + ln / 2, py)],
            rng.uniform(15.0, 18.0 + 15.0 * t0), cjit(col, 1.5), 1.0,
            "round_hard", 0.72)

# broad horizontal under-sweep: strokes grow toward the foreground, none cross the horizon
for y in np.arange(HOR + 2, H + 20, 18):
    t = float(np.clip((y - HOR) / (H - HOR), 0, 1))
    for x in np.arange(-80, W + 80, 82):
        px = x + rng.uniform(-18, 18)
        py = y + rng.uniform(-3, 3)
        col = sample(sea_rgb, px, py)
        ln = 60 + 320 * math.sqrt(t)
        sz = 6 + 78 * t
        add("sea", [(px - ln / 2, py + rng.uniform(-1.5, 1.5)),
                    (px + ln / 2, py + rng.uniform(-1.5, 1.5))],
            rng.uniform(sz * 0.82, sz * 1.16), cjit(col, 3), rng.uniform(0.90, 1.0),
            "round_soft", rng.uniform(0.28, 0.54))

# mid-scale wave marks, again scaled by distance
for y in np.arange(HOR + 3, H + 8, 12):
    t = float(np.clip((y - HOR) / (H - HOR), 0, 1))
    for x in np.arange(-16, W + 18, 23):
        px = x + rng.uniform(-8, 8)
        py = y + rng.uniform(-2.5, 2.5)
        col = sample(sea_rgb, px, py)
        slope = math.sin(px / 180.0 + py / 42.0) * 0.030 + rng.normal(0, 0.008)
        ln = (14 + 96 * math.sqrt(t)) * rng.uniform(0.80, 1.25)
        sz = 1.8 + 11.5 * t
        add("sea", [(px - math.cos(slope) * ln / 2, py - math.sin(slope) * ln / 2),
                    (px + math.cos(slope) * ln / 2, py + math.sin(slope) * ln / 2)],
            rng.uniform(sz * 0.75, sz * 1.20), cjit(col, rng.uniform(3, 7)),
            rng.uniform(0.34, 0.64),
            "round_soft" if rng.random() < 0.82 else "round_hard", rng.uniform(0.30, 0.54))

# a few real wave ribbons, broken into segments so they do not read as stripes
for i, t in enumerate([0.08, 0.14, 0.21, 0.29, 0.38, 0.49, 0.62, 0.77, 0.91]):
    ybase = HOR + (H - HOR) * t
    nseg = 2 if t < 0.30 else 3
    for seg in range(nseg):
        xa = -30 + (W + 60) * (seg / nseg) + rng.uniform(-80, 30)
        xb = -30 + (W + 60) * ((seg + 1) / nseg) + rng.uniform(-20, 80)
        xs = np.linspace(xa, xb, rng.integers(5, 10))
        ph = rng.uniform(0, 2 * math.pi)
        amp = (2.0 + 10.0 * t) * rng.uniform(0.55, 1.1)
        ys = ybase + amp * np.sin(xs / (62 + 50 * t) + ph) + rng.normal(0, 0.7, len(xs))
        pts = [(float(a), float(b)) for a, b in zip(xs, ys)]
        dark = mix([16, 33, 42], [7, 20, 28], t)
        add("sea_detail", pts, 3.0 + 9.0 * t, cjit(dark, 3), 0.14 + 0.10 * t,
            "round_soft", 0.30)
        # crest dabs on the upper side of the ribbon
        for j in range(len(xs) - 1):
            if rng.random() > 0.70:
                continue
            uu = rng.uniform(0, 1)
            px = xs[j] * (1 - uu) + xs[j + 1] * uu
            py = ys[j] * (1 - uu) + ys[j + 1] * uu - (2 + 5 * t)
            warm = math.exp(-((px - SUNX) / (70 + 240 * t)) ** 2)
            cc = mix([75, 107, 105], [241, 163, 107], 0.55 * warm + 0.12)
            add("sea_detail", [(px - rng.uniform(4, 16), py), (px + rng.uniform(4, 22), py + rng.uniform(-1, 1))],
                rng.uniform(2.0, 6.5) * (1 + t), cjit(cc, 6), rng.uniform(0.18, 0.46),
                "bristle" if rng.random() < 0.6 else "chalk", rng.uniform(0.35, 0.62))

# soft reflection body
for _ in range(30):
    t = rng.uniform(0.02, 0.98)
    py = HOR + (H - HOR) * t
    width = 14 + 170 * (t ** 1.3)
    px = SUNX + rng.normal(0, width * 0.30)
    ln = rng.uniform(40, 180) * (0.5 + 0.8 * t)
    add("sea_glints", [(px - ln / 2, py + rng.uniform(-2, 2)), (px + ln / 2, py + rng.uniform(-2, 2))],
        rng.uniform(8, 26), cjit([246, 170, 108], 8), rng.uniform(0.03, 0.075), "round_soft", 0.18)

# broken gold glints
for _ in range(440):
    t = rng.uniform(0.0, 1.0)
    py = HOR + (H - HOR) * t
    width = 10 + 155 * (t ** 1.3)
    px = SUNX + rng.normal(0, width * 0.38)
    if abs(px - SUNX) > width * 1.15:
        continue
    ln = rng.uniform(4, 45) * (0.45 + 0.9 * t)
    cc = mix([255, 219, 155], [235, 131, 90], rng.uniform(0, 0.65))
    add("sea_glints", [(px - ln / 2, py), (px + ln / 2, py + rng.uniform(-0.8, 0.8))],
        rng.uniform(1.1, 5.0), cjit(cc, 5), rng.uniform(0.10, 0.52),
        "round_hard" if rng.random() < 0.55 else "bristle", rng.uniform(0.35, 0.70))

# clean, quiet horizon seam drawn after the sea so no rectangular under-sweep can break it
for x in range(-20, W + 30, 46):
    add("sea_detail", [(x, HOR + rng.uniform(-0.4, 0.4)),
                       (x + rng.uniform(32, 50), HOR + rng.uniform(-0.4, 0.4))],
        rng.uniform(0.9, 1.5), cjit([39, 45, 60], 2), rng.uniform(0.22, 0.38), "round_hard", 0.62)

# ----------------------------------------------------------------------------
# 6. cliff / headland
# ----------------------------------------------------------------------------
cliff_poly = [
    (690, 900), (702, 852), (714, 810), (728, 772), (744, 737), (762, 706),
    (782, 684), (806, 670), (830, 662), (852, 658), (870, 661), (890, 654),
    (912, 659), (934, 652), (956, 657), (978, 649), (1000, 655), (1022, 647),
    (1046, 653), (1072, 641), (1100, 647), (1128, 635), (1160, 641),
    (1200, 630), (1200, 900),
]
cliff_mask = np.zeros((H, W), np.uint8)
cv2.fillPoly(cliff_mask, [np.array(cliff_poly, np.int32)], 255)

# opaque rough base mass: two full passes so no dark holes survive between facets
shape_ops.fill_polygon(doc, "cliff", cliff_poly, color=(28, 31, 45), color2=(48, 44, 57),
                       size=58, spacing=21, jitter=0.48, preset="round_hard",
                       hardness=0.62, edge=True, opacity=1.0, angle=74, seed=71)
shape_ops.fill_polygon(doc, "cliff", cliff_poly, color=(65, 57, 64), color2=(36, 39, 53),
                       size=44, spacing=17, jitter=0.55, preset="bristle",
                       hardness=0.52, edge=False, opacity=0.88, angle=28, seed=72)

# fracture the headland into unequal facets, then paint each facet as a rock plane
height = fbm_field(777, 9, 7, 4, 0.58)
poly_np = np.array(cliff_poly, np.int32)
subdiv = cv2.Subdiv2D((-12, -12, W + 24, H + 24))
boundary = cliff_poly[3:-1]
for p in boundary:
    subdiv.insert((float(p[0]), float(p[1])))
for _ in range(92):
    p = (float(rng.uniform(700, 1200)), float(rng.uniform(645, 900)))
    if cv2.pointPolygonTest(poly_np, p, False) > 20:
        subdiv.insert(p)
tris = subdiv.getTriangleList()
light = np.array([-0.46, -0.58, 0.67], np.float32)
light /= np.linalg.norm(light)
facets = []
for tri in tris:
    p = [(float(tri[0]), float(tri[1])), (float(tri[2]), float(tri[3])), (float(tri[4]), float(tri[5]))]
    cen = (sum(q[0] for q in p) / 3.0, sum(q[1] for q in p) / 3.0)
    if cv2.pointPolygonTest(poly_np, cen, False) <= 0:
        continue
    area = abs((p[1][0] - p[0][0]) * (p[2][1] - p[0][1]) -
               (p[2][0] - p[0][0]) * (p[1][1] - p[0][1])) * 0.5
    if area < 260:
        continue
    zz = []
    for q in p:
        zz.append(44.0 * float(sample(height, q[0], q[1])))
    q0, q1, q2 = [np.array([p[0][0], p[0][1], zz[0]], np.float32),
                  np.array([p[1][0], p[1][1], zz[1]], np.float32),
                  np.array([p[2][0], p[2][1], zz[2]], np.float32)]
    nrm = np.cross(q1 - q0, q2 - q0)
    nrm /= np.linalg.norm(nrm) + 1e-6
    if nrm[2] < 0:
        nrm = -nrm
    lam = max(0.0, float(np.dot(nrm, light)))
    shade = 0.18 + 0.78 * lam
    # warm faces near the top-left, cool deep faces lower-right
    warm = smoothstep(0.0, 0.8, 0.70 * (1 - (cen[0] - 720) / 470) + 0.42 * (1 - (cen[1] - 640) / 250))
    shadow = np.array([19, 23, 39], np.float32)
    midc = np.array([57, 57, 72], np.float32)
    litc = np.array([142, 110, 88], np.float32)
    rimc = np.array([200, 143, 95], np.float32)
    if shade < 0.48:
        c = mix(shadow, midc, shade / 0.48)
    else:
        c = mix(midc, litc, (shade - 0.48) / 0.52)
    c = mix(c, rimc, 0.18 * warm * shade)
    c = cjit(c, 7)
    facets.append((p, area, cen, c, shade, nrm))
    size = float(np.clip(math.sqrt(area) * 0.52, 18, 36))
    shape_ops.fill_polygon(doc, "cliff_facet", p, color=c.tolist(), size=size,
                           spacing=max(7.5, size * 0.38), jitter=0.50, preset="bristle",
                           hardness=0.52, edge=False, opacity=0.95, angle=rng.uniform(-42, 42),
                           seed=int(1000 + len(facets)))

# painterly rock grain: short marks inside the silhouette, no long wire-like edges
for _ in range(760):
    px = rng.uniform(690, 1200)
    py = rng.uniform(640, 900)
    if cv2.pointPolygonTest(poly_np, (px, py), False) < 0:
        continue
    topness = float(np.clip((752 - py) / 120.0, 0, 1))
    side = float(np.clip((px - 700) / 500.0, 0, 1))
    c = mix([24, 28, 43], [96, 78, 70], 0.33 + 0.35 * topness + 0.18 * (1 - side))
    if topness > 0.5 and rng.random() < 0.09:
        c = mix(c, [126, 91, 68], rng.uniform(0.18, 0.42))
    c = cjit(c, 5)
    ang = -0.55 + rng.normal(0, 0.42)
    ln = rng.uniform(6, 28) * (0.8 + 0.6 * (py - 640) / 260)
    add("cliff_detail", [(px - math.cos(ang) * ln / 2, py - math.sin(ang) * ln / 2),
                         (px + math.cos(ang) * ln / 2, py + math.sin(ang) * ln / 2)],
        rng.uniform(2.0, 7.5), c, rng.uniform(0.10, 0.32),
        "round_soft" if rng.random() < 0.72 else "round_hard", rng.uniform(0.34, 0.62))

# short cracks only: never connect two distant facet vertices
for _ in range(58):
    px = rng.uniform(710, 1190)
    py = rng.uniform(665, 895)
    if cv2.pointPolygonTest(poly_np, (px, py), False) < 0:
        continue
    ang = rng.uniform(-1.15, 0.55)
    ln = rng.uniform(9, 38)
    add("cliff_detail", [(px - math.cos(ang) * ln / 2, py - math.sin(ang) * ln / 2),
                         (px + math.cos(ang) * ln / 2, py + math.sin(ang) * ln / 2)],
        rng.uniform(0.8, 2.1), cjit([15, 19, 31], 3), rng.uniform(0.12, 0.30),
        "round_soft", 0.32)

# broad warm/cool planes give the headland volume beneath the chip texture
for _ in range(62):
    px = rng.uniform(700, 1160)
    py = rng.uniform(665, 885)
    if cv2.pointPolygonTest(poly_np, (px, py), False) < 0:
        continue
    topness = float(np.clip((780 - py) / 150.0, 0, 1))
    leftness = float(np.clip((980 - px) / 300.0, 0, 1))
    if rng.random() < 0.70:
        c = mix([49, 50, 64], [139, 103, 79], 0.22 + 0.44 * topness * leftness)
    else:
        c = mix([20, 24, 39], [55, 64, 82], rng.uniform(0.20, 0.62))
    ang = -0.38 + rng.normal(0, 0.28)
    ln = rng.uniform(22, 72)
    add("cliff_detail", [(px - math.cos(ang) * ln / 2, py - math.sin(ang) * ln / 2),
                         (px + math.cos(ang) * ln / 2, py + math.sin(ang) * ln / 2)],
        rng.uniform(6, 19), cjit(c, 4), rng.uniform(0.07, 0.17), "round_soft", 0.30)

# angular boulders and ledges: a broken, asymmetric rock coast instead of potatoes
boulders = [(824, 752, 30, 24), (860, 710, 25, 19), (1008, 720, 30, 24),
            (1064, 780, 37, 32), (1126, 848, 50, 42)]
for i, (bx, by, rx, ry) in enumerate(boulders):
    n = 9
    aa = np.linspace(0, 2 * math.pi, n, endpoint=False) + rng.uniform(-0.08, 0.08)
    rr = rng.uniform(0.72, 1.14, n)
    pts = [(bx + math.cos(a) * rx * w, by + math.sin(a) * ry * w) for a, w in zip(aa, rr)]
    wfac = 0.28 + 0.30 * (1.0 - min(1.0, (bx - 700) / 500.0))
    base = mix([43, 44, 59], [131, 100, 79], wfac)
    shape_ops.fill_polygon(doc, "cliff_facet", pts, color=base, color2=base * 0.50,
                           size=13, spacing=5.4, jitter=0.38, preset="round_hard",
                           hardness=0.60, edge=False, opacity=0.96, angle=-22 + i * 8, seed=450 + i)
    # broad dark right-hand plane
    for k in range(6):
        py = by + rng.uniform(-ry * 0.35, ry * 0.55)
        px = bx + rng.uniform(0.05, 0.72) * rx
        add("cliff_detail", [(px - rng.uniform(4, 12), py), (px + rng.uniform(6, 19), py + rng.uniform(-3, 3))],
            rng.uniform(3.5, 8.0), cjit(base * 0.44, 4), rng.uniform(0.22, 0.42), "round_soft", 0.46)
    # warm top-left facet
    for k in range(5):
        px = bx - rng.uniform(0.05, 0.62) * rx
        py = by - rng.uniform(0.05, 0.68) * ry
        add("cliff_detail", [(px - rng.uniform(4, 10), py), (px + rng.uniform(4, 12), py + rng.uniform(-2, 2))],
            rng.uniform(2.8, 6.5), cjit(mix(base, [184, 137, 95], 0.48), 5),
            rng.uniform(0.20, 0.42), "round_hard", 0.54)
    # warm rim in separate short segments along the upper arc
    for k in range(3):
        aa0 = pts[5 + k % 4]; bb0 = pts[6 + k % 4]
        add("cliff_detail", [aa0, bb0], rng.uniform(1.1, 2.6),
            cjit([171, 116, 79], 5), rng.uniform(0.12, 0.26), "round_soft", 0.42)
    # dark base contact
    add("cliff_detail", [(bx - rx * 0.55, by + ry * 0.72), (bx + rx * 0.55, by + ry * 0.78)],
        rng.uniform(2.4, 5.0), [22, 24, 36], rng.uniform(0.14, 0.28), "round_soft", 0.28)

# three stepped ledges build a more vertical, wave-cut left coast
ledges = [
    ([(690, 900), (700, 862), (727, 845), (752, 866), (740, 900)], [39, 40, 54]),
    ([(704, 842), (720, 802), (751, 785), (772, 812), (751, 850), (721, 859)], [45, 45, 59]),
    ([(733, 790), (754, 746), (787, 729), (802, 761), (779, 803), (746, 808)], [51, 50, 63]),
]
for li, (pts, base) in enumerate(ledges):
    base = np.array(base, np.float32)
    shape_ops.fill_polygon(doc, "cliff_facet", pts, color=base.tolist(), color2=[25, 28, 43],
                           size=16, spacing=6.0, jitter=0.40, preset="round_hard",
                           hardness=0.58, edge=False, opacity=0.96, angle=68, seed=640 + li)
    # lit top-left edge segments
    for k in range(len(pts) - 2):
        if rng.random() < 0.30:
            continue
        a = pts[k + 1]; b = pts[k + 2]
        add("cliff_detail", [a, b], rng.uniform(1.0, 2.8),
            cjit(mix([144, 98, 74], [189, 128, 84], rng.random()), 5),
            rng.uniform(0.12, 0.30), "round_soft", 0.42)
    # a few broad internal planes
    for k in range(8):
        cx0 = sum(q[0] for q in pts) / len(pts) + rng.uniform(-30, 30)
        cy0 = sum(q[1] for q in pts) / len(pts) + rng.uniform(-24, 24)
        add("cliff_detail", [(cx0 - rng.uniform(6, 18), cy0), (cx0 + rng.uniform(6, 20), cy0 + rng.uniform(-4, 4))],
            rng.uniform(3, 8), cjit(base * rng.uniform(0.55, 1.25), 5), rng.uniform(0.14, 0.30), "round_soft", 0.42)

# main ridge: repeated broken warm strokes, no continuous cartoon outline
ridge = [(760, 706), (782, 684), (806, 670), (830, 662), (852, 658), (870, 661),
         (890, 654), (912, 659), (934, 652), (956, 657), (978, 649), (1000, 655),
         (1022, 647), (1046, 653), (1072, 641), (1100, 647), (1128, 635),
         (1160, 641), (1200, 630)]
for i in range(len(ridge) - 1):
    a = ridge[i]; b = ridge[i + 1]
    for k in range(3):
        uu = rng.uniform(0.04, 0.96)
        px = a[0] * (1 - uu) + b[0] * uu
        py = a[1] * (1 - uu) + b[1] * uu + rng.uniform(-1.5, 1.0)
        ln = rng.uniform(4, 13)
        add("cliff_detail", [(px - ln / 2, py + rng.uniform(-1, 1)),
                             (px + ln / 2, py + rng.uniform(-1, 1))],
            rng.uniform(0.9, 2.6), cjit(mix([184, 123, 79], [101, 80, 67], rng.uniform(0, 0.65)), 5),
            rng.uniform(0.10, 0.32), "round_soft", 0.44)
# a second, brighter crust on the ridge where the last sun catches the rock
for i in range(len(ridge) - 1):
    if rng.random() < 0.30:
        continue
    a, b = ridge[i], ridge[i + 1]
    for k in range(2):
        uu = rng.uniform(0.05, 0.95)
        px = a[0] * (1 - uu) + b[0] * uu
        py = a[1] * (1 - uu) + b[1] * uu + rng.uniform(-1.2, 0.8)
        ln = rng.uniform(7, 21)
        add("cliff_detail", [(px - ln / 2, py + rng.uniform(-1, 1)),
                             (px + ln / 2, py + rng.uniform(-1, 1))],
            rng.uniform(1.2, 3.2), cjit(mix([213, 145, 92], [157, 105, 73], rng.uniform(0, 0.65)), 5),
            rng.uniform(0.18, 0.42), "round_soft", 0.42)
# warm edge where the left cliff face meets the sea light
for i in range(1, 10):
    a, b = cliff_poly[i], cliff_poly[i + 1]
    for k in range(3):
        uu = rng.uniform(0.02, 0.98)
        px = a[0] * (1 - uu) + b[0] * uu
        py = a[1] * (1 - uu) + b[1] * uu
        ln = rng.uniform(5, 16)
        add("cliff_detail", [(px, py + rng.uniform(-2, 2)), (px + ln, py + rng.uniform(-2, 2))],
            rng.uniform(0.8, 2.4), cjit(mix([124, 88, 70], [181, 122, 82], rng.random()), 4),
            rng.uniform(0.10, 0.30), "round_soft", 0.38)

# dark shadow under the ridge lip
for i in range(0, len(ridge) - 1, 2):
    a, b = ridge[i], ridge[i + 1]
    add("cliff_detail", [(a[0], a[1] + 7), (b[0], b[1] + 9)],
        rng.uniform(1.0, 2.4), [24, 26, 39], rng.uniform(0.10, 0.24), "round_soft", 0.35)

# sea foam where the headland enters the water
for arc_i, (cx, cy, rx, ry, n) in enumerate([(736, 785, 58, 102, 80), (772, 750, 44, 66, 62)]):
    for i in range(n):
        tt = rng.uniform(0.04, 0.96)
        ang = math.pi * 0.55 + math.pi * 0.85 * tt
        px = cx + math.cos(ang) * rx + rng.normal(0, 4)
        py = cy + math.sin(ang) * ry + rng.normal(0, 4)
        ln = rng.uniform(2, 13)
        cc = mix([143, 176, 169], [241, 225, 198], rng.uniform(0.1, 0.85))
        add("foam", [(px - ln / 2, py), (px + ln / 2, py + rng.uniform(-1, 1))],
            rng.uniform(1.0, 3.4), cjit(cc, 5), rng.uniform(0.16, 0.55), "chalk", 0.6)
# wet dark contact shadow
for i in range(28):
    tt = rng.uniform(0, 1)
    px = 725 + tt * 100
    py = 885 - tt * 160 + rng.uniform(-5, 5)
    add("foam", [(px, py), (px + rng.uniform(8, 28), py + rng.uniform(-2, 2))],
        rng.uniform(2, 7), [26, 37, 47], rng.uniform(0.15, 0.30), "round_soft", 0.4)

# a few dark wind-bent grass tufts, kept subordinate to the rock
for gx0, gy0, n in [(820, 665, 5), (1010, 650, 6), (1114, 639, 5)]:
    for i in range(n):
        dx = rng.uniform(-7, 7)
        ln = rng.uniform(3, 9)
        add("cliff_detail", [(gx0 + dx, gy0), (gx0 + dx + rng.uniform(-3, 4), gy0 - ln)],
            rng.uniform(0.6, 1.5), cjit(mix([54, 57, 43], [98, 87, 55], rng.random()), 4),
            rng.uniform(0.16, 0.40), "liner", 0.55)

# ----------------------------------------------------------------------------
# 7. lighthouse: plinth, tapered body, bands, ironwork, lantern
# ----------------------------------------------------------------------------
def body_hw(y):
    t = np.clip((y - LTOP) / (LBASE - LTOP), 0, 1)
    return 35.0 + (64.0 - 35.0) * t

# stone pedestal: a visible two-tier footing, not a hidden dark band
plinth_top = LBASE - 8
plinth_bot = LBASE + 48
shape_ops.fill_polygon(doc, "plinth",
                       [(LXC - 78, plinth_top + 11), (LXC + 78, plinth_top + 11),
                        (LXC + 70, plinth_bot), (LXC - 70, plinth_bot)],
                       color=(122, 108, 101), color2=(48, 51, 69), size=14, spacing=6.0,
                       jitter=0.36, preset="round_hard", hardness=0.64, edge=False,
                       opacity=0.98, gradient_angle=0, angle=4, seed=811)
# overhanging cap slab
shape_ops.fill_polygon(doc, "plinth",
                       [(LXC - 86, plinth_top), (LXC + 86, plinth_top),
                        (LXC + 80, plinth_top + 14), (LXC - 80, plinth_top + 14)],
                       color=(169, 141, 116), color2=(67, 68, 83), size=12, spacing=5.0,
                       jitter=0.30, preset="round_hard", hardness=0.66, edge=False,
                       opacity=0.99, gradient_angle=0, angle=0, seed=812)
# cap lip, lower seam and vertical joints
add("plinth", [(LXC - 84, plinth_top + 1), (LXC + 84, plinth_top + 1)],
    2.6, [205, 168, 128], 0.58, "round_hard", 0.65)
add("plinth", [(LXC - 80, plinth_top + 14), (LXC + 80, plinth_top + 14)],
    2.0, [37, 39, 55], 0.55, "round_hard", 0.60)
for xx0 in [LXC - 58, LXC - 30, LXC, LXC + 31, LXC + 59]:
    add("plinth", [(xx0, plinth_top + 15), (xx0 - rng.uniform(1, 4), plinth_bot - 2)],
        rng.uniform(0.8, 1.7), [50, 51, 65], rng.uniform(0.16, 0.32), "round_soft", 0.4)
# stone mottling, lit strongly on the left
for _ in range(110):
    px = rng.uniform(LXC - 76, LXC + 76)
    py = rng.uniform(plinth_top + 14, plinth_bot - 2)
    lit = max(0.0, 1.0 - (px - (LXC - 78)) / 156.0)
    c = mix([52, 54, 69], [173, 143, 115], lit * rng.uniform(0.35, 0.95))
    add("plinth", [(px, py), (px + rng.uniform(3, 15), py + rng.uniform(-3, 3))],
        rng.uniform(1.5, 4.8), cjit(c, 6), rng.uniform(0.16, 0.40),
        "round_soft" if rng.random() < 0.72 else "round_hard", 0.48)
# cast shadow and rubble at the foot
for _ in range(28):
    px = rng.uniform(LXC - 96, LXC + 104)
    py = rng.uniform(plinth_bot - 6, plinth_bot + 18)
    add("cliff_detail", [(px - rng.uniform(16, 40), py), (px + rng.uniform(16, 40), py + rng.uniform(-2, 2))],
        rng.uniform(6, 18), [20, 22, 35], rng.uniform(0.12, 0.28), "round_soft", 0.24)
for _ in range(20):
    px = rng.uniform(LXC - 92, LXC + 92)
    py = rng.uniform(plinth_bot - 2, plinth_bot + 12)
    add("cliff_detail", [(px, py), (px + rng.uniform(-5, 8), py + rng.uniform(-2, 2))],
        rng.uniform(2, 6), cjit([76, 66, 61], 6), rng.uniform(0.20, 0.42), "round_hard", 0.55)

# foreground rocks overlap the plinth base so the pedestal is embedded in the headland
for i, (bx, by, rx, ry) in enumerate([(870, 694, 43, 18), (920, 701, 56, 22),
                                      (985, 694, 50, 18), (1030, 700, 33, 14)]):
    n = 9
    aa = np.linspace(0, 2 * math.pi, n, endpoint=False) + rng.uniform(-0.12, 0.12)
    rr = rng.uniform(0.64, 1.15, n)
    pts = [(bx + math.cos(a) * rx * w, by + math.sin(a) * ry * w) for a, w in zip(aa, rr)]
    base = mix([38, 39, 53], [110, 85, 70], 0.32 + 0.06 * i)
    shape_ops.fill_polygon(doc, "front_rocks", pts, color=base, color2=base * 0.46,
                           size=10, spacing=4.2, jitter=0.32, preset="round_hard",
                           hardness=0.62, edge=False, opacity=0.98, angle=-12 + i * 11, seed=880 + i)
    # warm lip and shadowed underside
    for k in range(len(pts) - 1):
        if rng.random() < 0.45:
            continue
        add("front_rocks", [pts[k], pts[k + 1]], rng.uniform(0.9, 2.4),
            cjit([158, 110, 78], 5), rng.uniform(0.14, 0.34), "round_soft", 0.45)
    add("front_rocks", [(bx - rx * 0.5, by + ry * 0.55), (bx + rx * 0.5, by + ry * 0.65)],
        rng.uniform(2.2, 4.4), [21, 23, 35], rng.uniform(0.15, 0.28), "round_soft", 0.25)

# tapered body: one continuous cylinder, shaded from warm left edge to cold right edge
def body_pt(y, f, inset=4.0):
    hw = max(4.0, body_hw(y) - inset)
    return (LXC - hw + 2.0 * hw * f, y)

body_poly = [body_pt(LTOP + 3, 0.02), body_pt(LTOP + 3, 0.98),
             body_pt(LBASE - 2, 0.98), body_pt(LBASE - 2, 0.02)]
shape_ops.fill_polygon(doc, "tower", body_poly, color=(250, 228, 189), color2=(70, 76, 103),
                       size=15.0, spacing=6.0, jitter=0.28, preset="round_hard",
                       hardness=0.62, edge=False, opacity=0.99,
                       gradient_angle=0, angle=90, seed=2000)
shape_ops.fill_polygon(doc, "tower", body_poly, color=(242, 220, 187), color2=(89, 93, 116),
                       size=22.0, spacing=11.0, jitter=0.42, preset="round_soft",
                       hardness=0.36, edge=False, opacity=0.28,
                       gradient_angle=0, angle=90, seed=2001)
# broad warm key-light and cool sea-reflection swaths
for _ in range(22):
    py = rng.uniform(LTOP + 30, LBASE - 12)
    hw = body_hw(py)
    px = rng.uniform(LXC - hw * 0.86, LXC - hw * 0.30)
    ln = rng.uniform(40, 150)
    add("tower_detail", [(px, py - ln / 2), (px + rng.uniform(-2, 2), py + ln / 2)],
        rng.uniform(7, 17), cjit([255, 238, 203], 4), rng.uniform(0.035, 0.095), "round_soft", 0.22)
for _ in range(14):
    py = rng.uniform(LTOP + 160, LBASE - 10)
    hw = body_hw(py)
    px = rng.uniform(LXC + hw * 0.34, LXC + hw * 0.88)
    ln = rng.uniform(35, 125)
    add("tower_detail", [(px, py - ln / 2), (px + rng.uniform(-2, 2), py + ln / 2)],
        rng.uniform(7, 16), cjit([108, 132, 140], 5), rng.uniform(0.025, 0.070), "round_soft", 0.20)
# deep shadow where the tower meets the pedestal
for _ in range(14):
    px = rng.uniform(LXC - 58, LXC + 58)
    py = LBASE - rng.uniform(1, 10)
    add("tower_detail", [(px - rng.uniform(8, 22), py), (px + rng.uniform(8, 22), py + rng.uniform(-1, 1))],
        rng.uniform(4, 9), [54, 53, 67], rng.uniform(0.12, 0.28), "round_soft", 0.28)

# quiet vertical paint ribs on the tower wall (no fibrous bristle)
for _ in range(76):
    py = rng.uniform(LTOP + 10, LBASE - 5)
    hw = body_hw(py)
    fc = rng.beta(1.7, 2.0)
    px = LXC - hw + 2 * hw * fc
    shade = float(np.clip(0.88 - 1.20 * fc + 0.46 * math.exp(-((fc - 0.15) / 0.26) ** 2), 0.04, 1.0))
    c = mix(np.array([96, 95, 116]), np.array([250, 228, 190]), shade)
    c = cjit(c, 4)
    ln = rng.uniform(9, 28)
    add("tower_detail", [(px + rng.uniform(-0.8, 0.8), py - ln / 2),
                         (px + rng.uniform(-1.4, 0.4), py + ln / 2)],
        rng.uniform(1.1, 3.0), c, rng.uniform(0.06, 0.20), "round_soft", 0.38)

# a sparser set of opaque impasto ribs, visible at 100% but not fibrous
for _ in range(54):
    py = rng.uniform(LTOP + 14, LBASE - 8)
    hw = body_hw(py)
    fc = rng.beta(1.8, 1.9)
    px = LXC - hw + 2 * hw * fc
    shade = float(np.clip(0.88 - 1.20 * fc + 0.46 * math.exp(-((fc - 0.15) / 0.26) ** 2), 0.04, 1.0))
    base_c = mix(np.array([96, 95, 116]), np.array([250, 228, 190]), shade)
    if rng.random() < 0.5:
        c = np.clip(base_c * rng.uniform(0.78, 0.92), 0, 255)
    else:
        c = np.clip(base_c + (255 - base_c) * rng.uniform(0.10, 0.22), 0, 255)
    ln = rng.uniform(9, 26)
    add("tower_detail", [(px + rng.uniform(-0.8, 0.8), py - ln / 2),
                         (px + rng.uniform(-1.6, 0.5), py + ln / 2)],
        rng.uniform(1.8, 4.2), c, rng.uniform(0.10, 0.26), "round_hard", 0.46)

# shadow-side hatching to make the cylinder turn
for _ in range(36):
    py = rng.uniform(LTOP + 22, LBASE - 8)
    hw = body_hw(py)
    px = rng.uniform(LXC + 0.18 * hw, LXC + 0.92 * hw)
    ln = rng.uniform(5, 16)
    add("tower_detail", [(px - ln / 2, py + rng.uniform(-2, 2)),
                         (px + ln / 2, py + rng.uniform(-2, 2))],
        rng.uniform(0.7, 2.0), [78, 80, 102], rng.uniform(0.05, 0.14), "round_soft", 0.4)

# two coral bands, wrapped around the taper
bands = [(0.345, 0.410), (0.655, 0.715)]
for bi, (fa, fb) in enumerate(bands):
    y0 = LTOP + (LBASE - LTOP) * fa
    y1 = LTOP + (LBASE - LTOP) * fb
    bp = [body_pt(y0, 0.01, 2.0), body_pt(y0, 0.99, 2.0),
          body_pt(y1, 0.99, 2.0), body_pt(y1, 0.01, 2.0)]
    shape_ops.fill_polygon(doc, "tower_band", bp, color=(220, 105, 78), color2=(71, 38, 51),
                           size=12.0, spacing=5.2, jitter=0.28, preset="round_hard",
                           hardness=0.58, edge=False, opacity=0.99,
                           gradient_angle=0, angle=90, seed=2600 + bi)
    # band lips
    for yy0 in [y0 + 1, y1 - 1]:
        hw = body_hw(yy0)
        add("tower_detail", [(LXC - hw + 2, yy0), (LXC + hw - 2, yy0)],
            rng.uniform(1.0, 2.2), mix([235, 136, 101], [91, 44, 56], rng.random()),
            rng.uniform(0.14, 0.36), "bristle", 0.5)
    # broken vertical paint ridges in the band
    for _ in range(28):
        py = rng.uniform(y0 + 2, y1 - 2)
        hw = body_hw(py)
        fc = rng.beta(1.6, 2.0)
        px = LXC - hw + 2 * hw * fc
        cc = mix([110, 48, 56], [244, 126, 92], 1 - fc * 1.2)
        add("tower_detail", [(px, py - rng.uniform(2, 5)), (px + rng.uniform(-2, 1), py + rng.uniform(2, 5))],
            rng.uniform(1.2, 3.2), cjit(cc, 6), rng.uniform(0.08, 0.28), "round_soft", 0.40)

# windows: two narrow arched openings
def arch_poly(cx, ytop, wid, hgt, n=8):
    pts = [(cx - wid / 2, ytop + hgt), (cx - wid / 2, ytop + hgt * 0.24)]
    for i in range(1, n):
        a = math.pi - math.pi * i / n
        pts.append((cx + math.cos(a) * wid / 2, ytop + hgt * 0.24 - math.sin(a) * hgt * 0.24))
    pts += [(cx + wid / 2, ytop + hgt), (cx, ytop + hgt - 2)]
    return pts

for wi, (wy, ww, wh) in enumerate([(303, 17, 39), (434, 19, 43)]):
    pts = arch_poly(LXC, wy, ww, wh)
    shape_ops.fill_polygon(doc, "tower_detail", pts, color=(28, 32, 46), size=5.5,
                           spacing=2.4, jitter=0.18, preset="round_hard", hardness=0.78,
                           edge=True, opacity=1.0, angle=90, seed=3100 + wi)
    # warm glint on one side of the recess
    add("tower_detail", [(LXC - ww / 2 + 1, wy + 5), (LXC - ww / 2 + 1, wy + wh - 2)],
        1.4, [211, 158, 105], 0.40, "round_soft", 0.4)
    # thin stone surround
    add("tower_detail", arch_poly(LXC, wy - 1, ww + 4, wh + 3), 1.1,
        [187, 147, 103], 0.45, "chalk", 0.55)

# door at the base
door_pts = arch_poly(LXC, 591, 35, 60, 10)
shape_ops.fill_polygon(doc, "tower_detail", door_pts, color=(45, 36, 39), size=8,
                       spacing=3.6, jitter=0.22, preset="round_hard", hardness=0.70,
                       edge=True, opacity=1.0, angle=90, seed=3300)
for i in range(5):
    px = LXC - 13 + i * 7
    add("tower_detail", [(px, 596), (px + rng.uniform(-1, 1), 650)],
        1.2, [95, 69, 58], 0.30, "chalk", 0.5)
add("tower_detail", arch_poly(LXC, 589, 42, 64, 10), 2.0, [191, 143, 91],
    0.45, "bristle", 0.55)
# handle and lintel warmth
add("tower_detail", [(LXC + 10, 630), (LXC + 10, 635)], 2.8, [192, 139, 80], 0.7, "round_hard", 0.6)

# clean tapered silhouette edges: a warm left rim and a cool right shadow
for side, colr, siz, opac in [(-1, [251, 224, 179], 2.8, 0.55), (1, [65, 68, 90], 3.0, 0.68)]:
    add("tower_band" if False else "tower_detail",
        [(LXC + side * (body_hw(LTOP + 3) - 0.8), LTOP + 3),
         (LXC + side * (body_hw(LBASE - 3) - 0.8), LBASE - 3)],
        siz, cjit(colr, 3), opac, "round_hard", 0.55)

# gallery deck and railing
shape_ops.fill_polygon(doc, "gallery",
                       [(LXC - 57, 207), (LXC + 57, 207), (LXC + 51, 226), (LXC - 51, 226)],
                       color=(47, 46, 54), color2=(25, 27, 39), size=10, spacing=5,
                       jitter=0.35, preset="flat", hardness=0.65, edge=False,
                       opacity=0.98, angle=0, seed=4100)
# deck lit edge and black underside
add("gallery", [(LXC - 55, 208), (LXC + 55, 208)], 2.3, [153, 120, 92], 0.58, "round_hard", 0.7)
add("gallery", [(LXC - 50, 224), (LXC + 50, 224)], 3.2, [15, 17, 27], 0.75, "round_hard", 0.7)
for i in range(9):
    px = LXC - 46 + i * 11.5
    add("gallery", [(px, 181), (px, 207)], 2.0, [35, 35, 43], 0.82, "round_hard", 0.75)
    if i in (0, 1):
        add("gallery", [(px + 0.5, 182), (px + 0.5, 206)], 0.7, [170, 131, 92], 0.45, "chalk", 0.5)
# rails
add("gallery", [(LXC - 48, 179), (LXC + 48, 179)], 2.4, [39, 39, 47], 0.90, "round_hard", 0.75)
add("gallery", [(LXC - 48, 192), (LXC + 48, 192)], 1.5, [44, 42, 48], 0.72, "round_hard", 0.7)
add("gallery", [(LXC - 48, 179), (LXC + 48, 179)], 0.9, [174, 132, 92], 0.30, "chalk", 0.55)

# lantern room: dark frame, warm glass, mullions
shape_ops.fill_polygon(doc, "lantern",
                       [(LXC - 30, 207), (LXC + 30, 207), (LXC + 29, 149), (LXC - 29, 149)],
                       color=(34, 34, 41), size=9, spacing=4.2, jitter=0.25,
                       preset="flat", hardness=0.72, edge=False, opacity=0.98,
                       angle=90, seed=4300)
# six glass panes
for row in range(2):
    for col in range(3):
        x0 = LXC - 25 + col * 17.0
        y0 = 154 + row * 25.0
        w0 = 13.0; h0 = 20.0
        cc = mix([255, 230, 163], [232, 151, 79], row * 0.35 + col * 0.08 + rng.uniform(-0.05, 0.08))
        shape_ops.fill_polygon(doc, "lantern", [(x0, y0), (x0 + w0, y0), (x0 + w0, y0 + h0), (x0, y0 + h0)],
                               color=cc.tolist(), size=5.5, spacing=3.4, jitter=0.30,
                               preset="round_soft", hardness=0.28, edge=False,
                               opacity=0.88, angle=90, seed=4400 + row * 3 + col)
        if row == 0:
            add("lantern", [(x0 + 2, y0 + 4), (x0 + w0 - 2, y0 + 4)],
                1.3, [255, 246, 199], 0.42, "round_soft", 0.2)
# mullions and frame
for px in [LXC - 27, LXC - 9, LXC + 9, LXC + 27]:
    add("lantern", [(px, 150), (px, 206)], 2.4, [26, 27, 33], 0.95, "round_hard", 0.8)
for py in [150, 176, 206]:
    add("lantern", [(LXC - 28, py), (LXC + 28, py)], 2.2, [26, 27, 33], 0.95, "round_hard", 0.8)
# warm frame edge on the light side
add("lantern", [(LXC - 28, 151), (LXC - 28, 205)], 1.1, [168, 125, 75], 0.42, "chalk", 0.5)

# bell roof: deep rust red, lit on the left
roof = [(LXC - 45, 149), (LXC + 45, 149), (LXC + 38, 135), (LXC + 19, 116),
        (LXC, 100), (LXC - 19, 116), (LXC - 38, 135)]
shape_ops.fill_polygon(doc, "lantern", roof, color=(99, 43, 48), color2=(57, 37, 50),
                       size=12, spacing=6, jitter=0.42, preset="bristle",
                       hardness=0.56, edge=False, opacity=0.97, angle=-58, seed=4600)
# roof lit flank
for _ in range(80):
    tt = rng.uniform(0, 1)
    px = LXC - 42 + tt * 44 + rng.uniform(-3, 3)
    py = 149 - tt * 36 + rng.uniform(-2, 2)
    cc = mix([177, 77, 57], [91, 38, 46], tt)
    add("lantern", [(px - rng.uniform(2, 7), py + rng.uniform(-1, 1)),
                    (px + rng.uniform(2, 8), py + rng.uniform(-1, 1))],
        rng.uniform(1.4, 4.8), cjit(cc, 6), rng.uniform(0.18, 0.55), "bristle", 0.55)
# eaves, ridge, finial
add("lantern", [(LXC - 47, 150), (LXC + 47, 150)], 3.6, [37, 29, 38], 0.95, "round_hard", 0.75)
add("lantern", [(LXC - 37, 139), (LXC - 11, 111), (LXC, 101)], 1.4, [212, 122, 80], 0.52, "chalk", 0.45)
add("lantern", [(LXC, 101), (LXC, 86)], 2.1, [44, 38, 43], 0.95, "round_hard", 0.8)
add("lantern", [(LXC, 88)], 5.4, [191, 136, 76], 0.85, "round_hard", 0.6)
add("lantern", [(LXC - 4, 89), (LXC + 4, 89)], 1.0, [247, 206, 139], 0.55, "round_soft", 0.25)

# lantern bloom: many soft dabs, area-uniform
for _ in range(260):
    rr = 120 * math.sqrt(rng.random())
    aa = rng.uniform(0, 2 * math.pi)
    px = LXC + math.cos(aa) * rr
    py = 177 + math.sin(aa) * rr * 1.05
    cc = mix([255, 211, 132], [255, 171, 94], rng.random())
    add("lantern_glow", [(px, py)], rng.uniform(7, 46), cc, rng.uniform(0.012, 0.050), "round_soft", 0.10)
for _ in range(70):
    rr = 25 * math.sqrt(rng.random())
    aa = rng.uniform(0, 2 * math.pi)
    px = LXC + math.cos(aa) * rr
    py = 177 + math.sin(aa) * rr * 0.8
    add("lantern_glow", [(px, py), (px + rng.uniform(-4, 4), py + rng.uniform(-2, 2))],
        rng.uniform(2, 8), [255, 248, 208], rng.uniform(0.10, 0.28), "round_soft", 0.15)

# ----------------------------------------------------------------------------
# 8. volumetric beam (behind rock and tower; above the sea)
# ----------------------------------------------------------------------------
# soft body of the beam, built from two nested wedges so the edges dissolve
beam_outer = [(LXC + 2, 136), (LXC - 3, 223), (-90, 532), (-90, 250)]
shape_ops.fill_polygon(doc, "beam", beam_outer, color=(255, 216, 154),
                       size=150, spacing=55, jitter=0.20, preset="round_soft",
                       hardness=0.06, edge=False, opacity=0.036,
                       angle=-13, seed=7001)
beam_inner = [(LXC + 2, 156), (LXC - 2, 208), (-90, 452), (-90, 312)]
shape_ops.fill_polygon(doc, "beam", beam_inner, color=(255, 230, 178),
                       size=105, spacing=43, jitter=0.20, preset="round_soft",
                       hardness=0.06, edge=False, opacity=0.050,
                       angle=-10, seed=7002)

# scattered long soft marks give the cone air and brush direction
for _ in range(170):
    tt = rng.uniform(0.0, 1.0)
    px = LXC - tt * (LXC + 110)
    cy = 176 + tt * 236
    spread = 12 + tt * 118
    py = cy + rng.normal(0, spread * 0.32)
    if py > 518:
        continue
    ln = rng.uniform(70, 215) * (0.55 + 0.65 * tt)
    sz = rng.uniform(10, 42) * (0.55 + 0.60 * (1 - tt))
    cc = mix([255, 229, 168], [255, 179, 108], rng.uniform(0, 0.65))
    add("beam", [(px - ln / 2, py + rng.uniform(-3, 3)),
                 (px + ln / 2, py + rng.uniform(-3, 3))],
        sz, cjit(cc, 4), rng.uniform(0.026, 0.086), "round_soft", 0.08)

# optical axis: a small number of broad, soft strokes; no rain of thin lines
for _ in range(8):
    tt = rng.uniform(0.05, 0.94)
    px = LXC - tt * (LXC + 100)
    py = 184 + tt * 188 + rng.normal(0, 10 + 18 * tt)
    ln = rng.uniform(120, 260)
    add("beam_core", [(px - ln / 2, py - ln * 0.065), (px + ln / 2, py + ln * 0.065)],
        rng.uniform(4.0, 9.0), cjit([255, 237, 188], 3), rng.uniform(0.030, 0.078),
        "round_soft", 0.08)
# a gentle warm wash on the cloud bank the beam crosses
for _ in range(18):
    px = rng.uniform(0, LXC)
    py = 250 + 125 * (1 - px / LXC) + rng.normal(0, 24)
    add("beam_core", [(px - rng.uniform(30, 100), py), (px + rng.uniform(30, 100), py + rng.uniform(-2, 2))],
        rng.uniform(14, 34), [255, 206, 141], rng.uniform(0.010, 0.026), "round_soft", 0.12)

# ----------------------------------------------------------------------------
# 9. birds, punctuation, vignette
# ----------------------------------------------------------------------------
for i, (bx, by, sc) in enumerate([(260, 286, 1.0), (300, 267, 0.75), (338, 300, 0.55),
                                  (610, 252, 0.70), (650, 275, 0.9), (692, 243, 0.55)]):
    w = 9 * sc
    add("birds", [(bx - w, by), (bx, by - 2.2 * sc), (bx + w, by)], 1.15 * sc,
        [33, 30, 43], 0.62, "round_soft", 0.5)
    add("birds", [(bx - w * 0.8, by + 0.7), (bx, by - 2.0 * sc)], 0.8 * sc,
        [102, 78, 86], 0.28, "chalk", 0.4)

# soft corner vignette, deliberately broken and painted
corners = [(-90, -70, 220, 180), (W + 90, -70, 220, 180), (-100, H + 80, 260, 190), (W + 100, H + 80, 260, 190)]
for cx0, cy0, rw, rh in corners:
    for _ in range(36):
        px = cx0 + rng.normal(0, rw * 0.38)
        py = cy0 + rng.normal(0, rh * 0.38)
        if not (-30 < px < W + 30 and -30 < py < H + 30):
            continue
        add("vignette", [(px - rng.uniform(35, 100), py + rng.uniform(-20, 20)),
                         (px + rng.uniform(35, 100), py + rng.uniform(-20, 20))],
            rng.uniform(60, 150), cjit([27, 25, 47], 4), rng.uniform(0.018, 0.055),
            "round_soft", 0.16)

# a restrained final sparkle on the lantern edge and the lit sea path
for _ in range(28):
    px = LXC + rng.uniform(-30, 30)
    py = 166 + rng.uniform(-15, 18)
    add("lantern_glow", [(px, py)], rng.uniform(0.8, 2.5), [255, 249, 214],
        rng.uniform(0.12, 0.30), "round_hard", 0.3)
for _ in range(34):
    tt = rng.uniform(0.18, 0.92)
    py = HOR + (H - HOR) * tt
    px = SUNX + rng.normal(0, 24 + 95 * tt)
    add("sea_glints", [(px - rng.uniform(3, 15), py), (px + rng.uniform(3, 16), py)],
        rng.uniform(1.0, 2.8), [255, 218, 157], rng.uniform(0.12, 0.35), "round_hard", 0.45)

layer_order = [
    "base", "sky", "cloud_edge", "sky_glow", "horizon_glow", "far_shore",
    "beam", "beam_core", "sea", "sea_detail", "sea_glints",
    "cliff", "cliff_facet", "cliff_detail", "foam",
    "plinth", "front_rocks", "tower", "tower_band", "tower_detail", "gallery", "lantern", "lantern_glow",
    "birds", "vignette",
]
blends = {
    "base": "normal", "sky": "normal", "cloud_edge": "normal", "sky_glow": "screen",
    "horizon_glow": "normal", "far_shore": "normal", "beam": "screen", "beam_core": "screen",
    "sea": "normal", "sea_detail": "normal", "sea_glints": "screen",
    "cliff": "normal", "cliff_facet": "normal", "cliff_detail": "normal", "foam": "screen",
    "plinth": "normal", "front_rocks": "normal", "tower": "normal", "tower_band": "normal", "tower_detail": "normal",
    "gallery": "normal", "lantern": "normal", "lantern_glow": "screen",
    "birds": "normal", "vignette": "softlight",
}

plan_path = ROOT / "lighthouse.plan.json"
png_path = ROOT / "lighthouse.png"
thumb_path = ROOT / "lighthouse_25.png"
t0 = time.perf_counter()
count = plan_io.dump_plan(str(plan_path), W, H, doc["strokes"], layer_blends=blends, layer_order=layer_order)
build_s = time.perf_counter() - t0
t1 = time.perf_counter()
arr = RU.render_arr(plan_io.load_plan(str(plan_path))[0], gpu=False, nthreads=8)
render_s = time.perf_counter() - t1
a = arr[..., 3:4].astype(np.float32) / 255.0
rgb = np.clip(arr[..., :3].astype(np.float32) * a + np.array([250, 247, 239], np.float32) * (1 - a), 0, 255).astype(np.uint8)
cv2.imwrite(str(png_path), np.ascontiguousarray(rgb[..., ::-1]))
im = cv2.imread(str(png_path), cv2.IMREAD_COLOR)
cv2.imwrite(str(thumb_path), cv2.resize(im, (W // 4, H // 4), interpolation=cv2.INTER_AREA))
stats = {
    "canvas": [W, H], "strokes": count, "layers": len(layer_order),
    "presets": sorted({s["preset"] for s in doc["strokes"]}),
    "plan_bytes": plan_path.stat().st_size, "png_bytes": png_path.stat().st_size,
    "build_s": round(build_s, 3), "render_s": round(render_s, 3),
    "alpha_min": int(arr[..., 3].min()), "alpha_zero": int((arr[..., 3] == 0).sum()),
}
print(json.dumps(stats, ensure_ascii=False))
