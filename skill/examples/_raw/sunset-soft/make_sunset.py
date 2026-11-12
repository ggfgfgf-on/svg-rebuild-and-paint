from __future__ import annotations
import sys, math, time, json, random
from pathlib import Path
import numpy as np
from PIL import Image

SKILL = Path(r"D:\deepseek-harness-dsh-v0.2.2rc1\.dsh-home\skills\svg-rebuild-and-paint")
OUT = Path(r"D:\DSHwork\flower\_sub_b")
sys.path.insert(0, str(SKILL / "scripts"))
import plan_io
import render_util as RU

W, H = 1280, 800
HORIZON = 440.0
SUNX, SUNY = 820.0, 402.0
SUN_RX, SUN_RY = 39.0, 33.0
SEED = 20260714
rng = np.random.default_rng(SEED)
prng = random.Random(SEED)
strokes = []

# ---------- colour helpers ----------
def clamp(v, a=0.0, b=1.0):
    return a if v < a else b if v > b else v

def mix(a, b, t):
    t = clamp(t)
    return [a[i] + (b[i] - a[i]) * t for i in range(3)]

def jitter(c, amt=2.0):
    return [float(np.clip(c[i] + rng.normal(0.0, amt), 0, 255)) for i in range(3)]

def color_at(stops, t):
    t = clamp(t)
    for i in range(len(stops) - 1):
        t0, c0 = stops[i]
        t1, c1 = stops[i + 1]
        if t <= t1:
            u = 0.0 if t1 <= t0 else (t - t0) / (t1 - t0)
            return mix(c0, c1, u)
    return list(stops[-1][1])

def add(layer, pts, size, color, opacity, preset="round_soft", hardness=0.3, pressure=1.0):
    p = []
    for pt in pts:
        if len(pt) == 3:
            p.append([float(pt[0]), float(pt[1]), float(pt[2])])
        else:
            p.append([float(pt[0]), float(pt[1]), float(pressure)])
    strokes.append({
        "layer": layer,
        "preset": preset,
        "size": float(size),
        "opacity": float(clamp(opacity)),
        "color": [float(x) for x in color],
        "hardness": float(clamp(hardness)),
        "region_id": 0,
        "points": p,
    })

def curve_pts(x, y, length, slope, curve, n=3):
    pts = []
    for i in range(n):
        t = i / (n - 1)
        xx = x + (t - 0.5) * length
        yy = y + slope * (t - 0.5) * length + curve * (t - 0.5) ** 2
        pts.append((xx, yy))
    return pts

# ---------- sky palette ----------
SKY_STOPS = [
    (0.00, [20, 29, 57]),
    (0.14, [31, 40, 74]),
    (0.30, [62, 57, 92]),
    (0.46, [104, 72, 100]),
    (0.60, [151, 86, 101]),
    (0.72, [192, 104, 92]),
    (0.84, [226, 130, 90]),
    (0.93, [247, 164, 101]),
    (1.00, [255, 206, 146]),
]

def sky_color(y, x):
    t = clamp(y / HORIZON)
    c = color_at(SKY_STOPS, t)
    # broad warm atmospheric pool around the sun
    warm = math.exp(-((x - SUNX) / 520.0) ** 2 - ((y - HORIZON) / 330.0) ** 2)
    c = mix(c, [255, 183, 121], 0.26 * warm)
    # a cooler, more violet upper-left corner
    cool = math.exp(-((x - 80) / 500.0) ** 2 - ((y - 20) / 260.0) ** 2)
    c = mix(c, [37, 48, 91], 0.16 * cool)
    return c

# ---------- base fill (guarantees opaque canvas) ----------
for y in np.linspace(-70, H + 70, 12):
    c = [27, 30, 44] if y < HORIZON else [17, 26, 39]
    add("base", [(-120, y), (W * 0.5, y + 3), (W + 120, y - 2)], 285, c, 1.0,
        "round_hard", 0.92)

# sky foundation, opaque and softly graded
for y in np.linspace(-80, HORIZON + 90, 9):
    for xc in (-140, W * 0.5, W + 140):
        c = sky_color(min(y, HORIZON), xc)
        add("base_sky", [(-150, y + 2), (W * 0.52, y - 2), (W + 150, y + 1)],
            225, jitter(c, 1.4), 1.0, "round_hard", 0.82)

# sea foundation, opaque. Keep first top edge at the horizon.
for j, y in enumerate(np.linspace(HORIZON + 44, H + 40, 8)):
    t = clamp((y - HORIZON) / (H - HORIZON))
    for xc in (-140, W * 0.5, W + 140):
        c = mix([126, 100, 100], [14, 29, 43], t ** 0.85)
        add("base_sea", [(-160, y + 2), (W * 0.5, y - 3), (W + 160, y + 2)],
            165 + 80 * t, jitter(c, 1.6), 1.0, "round_hard", 0.84)

# ---------- sky colour-blending passes ----------
for i in range(820):
    y = float(rng.uniform(-35, HORIZON + 12))
    x = float(rng.uniform(-140, W + 140))
    ln = float(rng.uniform(180, 520))
    sz = float(rng.uniform(70, 235))
    c = sky_color(y, x)
    c = jitter(c, 2.2)
    slope = float(rng.normal(0.0, 0.012))
    curv = float(rng.normal(0.0, 7.0))
    add("sky", curve_pts(x, y, ln, slope, curv), sz, c,
        rng.uniform(0.028, 0.075), "round_soft", rng.uniform(0.08, 0.30))

# large soft atmospheric veils to suppress any banding
for i in range(120):
    y = float(rng.uniform(20, HORIZON - 10))
    x = float(rng.uniform(0, W))
    c = jitter(sky_color(y, x), 1.6)
    add("sky", [(x - 340, y), (x + 340, y + rng.normal(0, 3))],
        rng.uniform(180, 300), c, rng.uniform(0.012, 0.028), "round_soft", 0.05)

# ---------- solar halo ----------
for i in range(440):
    rr = 25.0 + 430.0 * (rng.random() ** 0.72)
    th = rng.uniform(0, math.tau)
    x = SUNX + math.cos(th) * rr * 1.25
    y = SUNY + math.sin(th) * rr * 0.70
    if y > HORIZON + 26:
        y = HORIZON + 26 - abs(rng.normal(0, 9))
    t = clamp(rr / 455.0)
    c = mix([255, 242, 190], [255, 126, 74], t ** 0.75)
    add("sun_glow", [(x, y)], 70 + 225 * (0.3 + 0.7 * (1 - t)),
        c, rng.uniform(0.006, 0.019) * (1.0 - 0.35 * t), "round_soft", 0.02)

# a wide warm horizon belt around the sun
for i in range(54):
    x = float(rng.uniform(-100, W + 100))
    y = float(HORIZON + rng.normal(-3, 12))
    q = math.exp(-((x - SUNX) / 420.0) ** 2)
    c = mix([255, 153, 91], [255, 205, 139], 0.35 * q)
    add("sun_glow", [(x - rng.uniform(130, 300), y), (x + rng.uniform(130, 300), y + rng.normal(0, 3))],
        rng.uniform(30, 90), c, rng.uniform(0.005, 0.016), "round_soft", 0.05)

# ---------- sun disc ----------
# dense area-uniform soft stamps: no radial spokes, no hollow ring
for i in range(1800):
    r = math.sqrt(rng.random())
    th = rng.uniform(0, math.tau)
    x = SUNX + math.cos(th) * r * SUN_RX
    y = SUNY + math.sin(th) * r * SUN_RY
    edge = clamp(r ** 0.85)
    c = mix([255, 252, 226], [255, 184, 93], edge)
    # limb is slightly warmer; core is hotter but still yellow-white
    if r < 0.36:
        c = mix(c, [255, 255, 241], 0.34 * (1 - r / 0.36))
    add("sun_disc", [(x, y)], rng.uniform(5.0, 12.5) * (1.0 - 0.18 * r),
        jitter(c, 2.0), rng.uniform(0.085, 0.165), "round_soft", 0.32)

# thin warm limb, intentionally soft and discontinuous rather than a ring
for i in range(240):
    th = rng.uniform(0, math.tau)
    r = rng.uniform(0.82, 1.0)
    x = SUNX + math.cos(th) * r * SUN_RX
    y = SUNY + math.sin(th) * r * SUN_RY
    add("sun_rim", [(x, y)], rng.uniform(3.0, 8.0),
        [255, 173 + rng.uniform(-10, 15), 79 + rng.uniform(-10, 15)],
        rng.uniform(0.045, 0.13), "round_soft", 0.25)

# ---------- procedural cloud fields ----------
def value_noise(shape, seed, octaves=5, base_w=4, base_h=3):
    h, w = shape
    nrng = np.random.default_rng(seed)
    acc = np.zeros((h, w), np.float32)
    norm = 0.0
    amp = 1.0
    for o in range(octaves):
        gw = base_w * (2 ** o)
        gh = base_h * (2 ** o)
        small = nrng.random((gh, gw)).astype(np.float32)
        im = Image.fromarray(small, mode="F").resize((w, h), Image.Resampling.BICUBIC)
        acc += amp * np.asarray(im, dtype=np.float32)
        norm += amp
        amp *= 0.52
    acc /= max(norm, 1e-6)
    acc -= float(acc.mean())
    acc /= float(acc.std() + 1e-6)
    return np.clip(acc * 0.22 + 0.5, 0, 1)

Yg, Xg = np.mgrid[0:H, 0:W].astype(np.float32)
noise_a = value_noise((H, W), SEED + 11, 5, 3, 2)
noise_b = value_noise((H, W), SEED + 29, 5, 5, 3)
noise_c = value_noise((H, W), SEED + 47, 4, 7, 2)

def cloud_env(blobs):
    env = np.zeros((H, W), np.float32)
    for cx, cy, rx, ry, amp, rot in blobs:
        th = math.radians(rot)
        dx = Xg - cx
        dy = Yg - cy
        xr = dx * math.cos(th) + dy * math.sin(th)
        yr = -dx * math.sin(th) + dy * math.cos(th)
        g = amp * np.exp(-((xr / rx) ** 2 + (yr / ry) ** 2))
        env = np.maximum(env, g.astype(np.float32))
    return env

CLOUD_GROUPS = [
    {
        "name": "high_wisps",
        "layer": "clouds",
        "blobs": [
            (90, 105, 245, 18, 0.78, -4), (315, 142, 285, 20, 0.82, 3),
            (610, 94, 320, 14, 0.62, -6), (860, 145, 235, 17, 0.72, -2),
            (1120, 108, 285, 23, 0.84, 4), (720, 185, 190, 9, 0.36, 2),
        ],
        "noise": noise_a, "gain": 1.00, "bias": 0.36, "threshold": 0.32,
        "step": 4, "max": 2600, "shadow": [41, 45, 70], "mid": [87, 72, 99],
        "lit": [236, 136, 103], "size0": 2.6, "size1": 7.6,
        "len0": 7, "len1": 34, "op0": 0.025, "op1": 0.075,
        "hardness": 0.34, "preset": "round_soft",
    },
    {
        "name": "mid_banks",
        "layer": "clouds",
        "blobs": [
            (70, 305, 112, 42, 0.95, -5), (165, 282, 92, 46, 0.98, 5),
            (265, 315, 108, 48, 0.95, -2), (350, 292, 80, 38, 0.90, -8),
            (440, 326, 102, 38, 0.82, 6), (525, 300, 72, 28, 0.72, 2),
            (610, 345, 86, 21, 0.55, -3),
            (990, 290, 90, 32, 0.86, -5), (1080, 312, 105, 45, 0.95, 4),
            (1170, 285, 85, 38, 0.88, -3), (1248, 320, 70, 32, 0.80, 4),
            (925, 338, 65, 19, 0.55, 0),
        ],
        "noise": noise_b, "gain": 0.80, "bias": 0.44, "threshold": 0.39,
        "step": 4, "max": 6200, "shadow": [40, 39, 62], "mid": [101, 66, 82],
        "lit": [255, 157, 100], "size0": 2.8, "size1": 11.0,
        "len0": 6, "len1": 42, "op0": 0.028, "op1": 0.095,
        "hardness": 0.72, "preset": "round_hard",
    },
    {
        "name": "horizon_bank",
        "layer": "clouds",
        "blobs": [
            (-15, 421, 135, 5.5, 0.54, 0), (115, 409, 78, 7.0, 0.68, 2),
            (430, 417, 138, 5.8, 0.62, -1), (553, 405, 64, 4.8, 0.44, 3),
            (1148, 416, 96, 5.0, 0.50, -2), (1242, 404, 68, 6.0, 0.56, 1),
        ],
        "noise": noise_c, "gain": 0.72, "bias": 0.56, "threshold": 0.39,
        "step": 4, "max": 1300, "shadow": [44, 43, 61], "mid": [90, 65, 76],
        "lit": [255, 174, 111], "size0": 1.8, "size1": 6.2,
        "len0": 6, "len1": 36, "op0": 0.022, "op1": 0.080,
        "hardness": 0.68, "preset": "round_hard",
    },
]

rim_candidates = []
for grp in CLOUD_GROUPS:
    env = cloud_env(grp["blobs"])
    dens = env * (grp["bias"] + grp["gain"] * grp["noise"])
    thr = grp["threshold"]
    step = grp["step"]
    ys = np.arange(2, int(HORIZON + 16), step)
    xs = np.arange(2, W, step)
    yy, xx = np.meshgrid(ys, xs, indexing="ij")
    vals = dens[yy, xx]
    sel = vals > thr
    yy = yy[sel]; xx = xx[sel]; vals = vals[sel]
    if len(vals) > grp["max"]:
        pick = rng.choice(len(vals), grp["max"], replace=False)
        yy = yy[pick]; xx = xx[pick]; vals = vals[pick]
    # randomize paint order within this cloud group
    order = rng.permutation(len(vals))
    yy = yy[order]; xx = xx[order]; vals = vals[order]
    for y0, x0, v in zip(yy.tolist(), xx.tolist(), vals.tolist()):
        x = float(x0 + rng.uniform(-1.8, 1.8))
        y = float(y0 + rng.uniform(-1.8, 1.8))
        dn = clamp((v - thr) / 0.55)
        # density-edge vector: positive when this point lies on the lower/right rim
        iy = int(np.clip(y0, 4, H - 5)); ix = int(np.clip(x0, 4, W - 5))
        dd = float(dens[iy - 4, ix] - dens[iy + 4, ix])
        dx = float(dens[iy, ix - 4] - dens[iy, ix + 4])
        rim = clamp((0.76 * dd + 0.24 * dx) * 3.2)
        sun_ex = math.exp(-((x - SUNX) / 470.0) ** 2 - ((y - (SUNY - 60)) / 330.0) ** 2)
        warm = clamp(0.05 + 0.20 * sun_ex + 0.74 * rim + rng.normal(0, 0.035))
        c = mix(grp["shadow"], grp["mid"], 0.22 + 0.78 * dn)
        c = mix(c, grp["lit"], warm * (0.55 + 0.45 * dn))
        c = jitter(c, 3.5)
        ln = grp["len0"] + (grp["len1"] - grp["len0"]) * (0.25 + 0.75 * dn) * rng.uniform(0.55, 1.15)
        sz = (grp["size0"] + (grp["size1"] - grp["size0"]) * (0.3 + 0.7 * dn)) * rng.uniform(0.75, 1.30)
        slope = rng.normal(0, 0.012)
        curv = rng.normal(0, 2.0)
        op = (grp["op0"] + (grp["op1"] - grp["op0"]) * dn) * rng.uniform(0.85, 1.45)
        # a broad underpainting pass gives the cloud real body instead of lace
        if dn > 0.22:
            mass_c = mix(grp["shadow"], grp["mid"], 0.22 + 0.50 * dn)
            add(grp["layer"], curve_pts(x, y + 1.0, ln * rng.uniform(1.15, 1.55), slope, curv),
                sz * rng.uniform(1.35, 1.85), jitter(mass_c, 2.0),
                rng.uniform(0.012, 0.032) * (0.7 + 0.6 * dn), "round_soft", 0.08)
        add(grp["layer"], curve_pts(x, y, ln, slope, curv), sz, c, op,
            grp["preset"], grp["hardness"])
        # visible directional drag-marks inside the mass give it a painted, not airbrushed, surface
        if dn > 0.10 and rng.random() < 0.46:
            detail_c = mix(grp["mid"], grp["lit"], clamp(0.20 + 0.55 * warm + rng.normal(0, 0.10)))
            add(grp["layer"], curve_pts(x, y - rng.uniform(0, 3), ln * rng.uniform(0.8, 1.5),
                slope, curv + rng.normal(0, 1.0)), sz * rng.uniform(0.9, 1.5), jitter(detail_c, 2.5),
                op * rng.uniform(0.85, 1.45), "bristle" if grp["hardness"] > 0.60 else "round_soft",
                rng.uniform(0.38, 0.56))
        # soft blocking shadow through the belly of the puffs, not at the sunlit rim
        if dn > 0.22 and rim < 0.34 and rng.random() < 0.10:
            sh = mix([28, 29, 48], [48, 38, 51], rng.uniform(0.2, 0.9))
            add("cloud_shadow", curve_pts(x + rng.normal(0, 2), y + rng.uniform(1, 4),
                ln * rng.uniform(0.55, 1.0), slope, curv),
                sz * rng.uniform(0.8, 1.30), jitter(sh, 2.0),
                rng.uniform(0.010, 0.030) * (0.50 + 0.50 * dn), "round_soft", 0.22)
        # a cool dark base directly above the warm under-rim gives the puff a readable volume
        if dd > 0.018 and dn > 0.18 and rng.random() < 0.34:
            add("clouds", curve_pts(x, y + rng.uniform(1.2, 3.0), ln * rng.uniform(0.8, 1.4), slope, curv),
                sz * rng.uniform(0.45, 0.85), jitter([31, 30, 47], 2.0),
                rng.uniform(0.025, 0.085) * (0.5 + 0.6 * dn), "round_soft", 0.38)
        if dd > 0.012 and rim > 0.20 and sun_ex > 0.07 and rng.random() < 0.44 + 0.30 * rim:
            rim_candidates.append((x, y, rim, sun_ex, dn, grp))

# ragged edge accent: a broken dark contour just inside the cloud silhouette
for grp in CLOUD_GROUPS:
    env = cloud_env(grp["blobs"])
    dens = env * (grp["bias"] + grp["gain"] * grp["noise"])
    thr = grp["threshold"]
    ys = np.arange(3, int(HORIZON + 12), 3)
    xs = np.arange(3, W - 3, 3)
    yy, xx = np.meshgrid(ys, xs, indexing="ij")
    vals = dens[yy, xx]
    grad = (np.abs(dens[yy - 3, xx] - dens[yy + 3, xx])
            + np.abs(dens[yy, xx - 3] - dens[yy, xx + 3]))
    sel = (vals > thr * 0.72) & (vals < thr * 1.55) & (grad > 0.006)
    yy = yy[sel]; xx = xx[sel]; vals = vals[sel]
    if len(vals) > 380:
        pick = rng.choice(len(vals), 380, replace=False)
        yy = yy[pick]; xx = xx[pick]; vals = vals[pick]
    for y0, x0 in zip(yy.tolist(), xx.tolist()):
        x = x0 + rng.normal(0, 1.0)
        y = y0 + rng.normal(0, 1.0)
        edge_c = mix(grp["shadow"], [25, 25, 40], rng.uniform(0.25, 0.72))
        add("clouds", [(x - rng.uniform(4, 14), y + rng.normal(0, 0.5)),
                       (x + rng.uniform(4, 14), y + rng.normal(0, 0.5))],
            rng.uniform(2.0, 7.5), jitter(edge_c, 2.0),
            rng.uniform(0.020, 0.070), "round_hard", rng.uniform(0.48, 0.66))

# luminous cloud rims, screen layer
select_rim = rng.permutation(len(rim_candidates))[:1550].tolist()
for ridx in select_rim:
    x, y, rim, sun_ex, dn, grp = rim_candidates[ridx]
    ln = rng.uniform(5, 25) * (0.6 + 0.8 * dn)
    sz = rng.uniform(2.7, 7.4) * (0.6 + 0.7 * dn)
    c = mix(grp["lit"], [255, 145, 74], 0.48)
    c = mix(c, [255, 193, 127], 0.25 * sun_ex)
    c = jitter(c, 2.5)
    add("cloud_rim", curve_pts(x, y + rng.uniform(0.3, 1.8), ln, rng.normal(0, 0.015), rng.normal(0, 1.5)),
        sz, c, rng.uniform(0.040, 0.135) * (0.45 + 0.8 * rim), "round_soft", 0.24)

# thin, broken filament drifting across the lower face of the sun
for i in range(26):
    x = rng.uniform(735, 1005)
    y = 413.5 + 4.0 * math.sin((x - 735) / 62.0) + rng.normal(0, 1.1)
    ln = rng.uniform(16, 78)
    add("clouds", [(x - ln / 2, y + rng.normal(0, 0.8)), (x, y + rng.normal(0, 1.0)),
                  (x + ln / 2, y + rng.normal(0, 0.8))],
        rng.uniform(1.2, 4.6), jitter([46, 41, 52], 2.0), rng.uniform(0.025, 0.095),
        "round_soft", 0.30)
for i in range(18):
    x = rng.uniform(765, 990)
    y = 412.5 + 4.0 * math.sin((x - 735) / 62.0) + rng.normal(0, 0.7)
    ln = rng.uniform(9, 46)
    add("cloud_rim", [(x - ln / 2, y), (x + ln / 2, y + rng.normal(0, 0.5))],
        rng.uniform(1.0, 2.8), jitter([255, 155, 88], 2.5), rng.uniform(0.035, 0.11),
        "round_soft", 0.28)

# ---------- horizon atmosphere ----------
for i in range(52):
    x = float(rng.uniform(-100, W + 100))
    y = HORIZON + rng.normal(0, 7)
    q = math.exp(-((x - SUNX) / 360.0) ** 2)
    c = mix([255, 216, 162], [255, 154, 91], 0.52 * q)
    add("horizon_haze", [(x - rng.uniform(100, 300), y),
                         (x + rng.uniform(100, 300), y + rng.normal(0, 1.5))],
        rng.uniform(8, 27), c, rng.uniform(0.006, 0.024), "round_soft", 0.05)

# thin horizon definition, broken and atmospheric
for i in range(26):
    x = rng.uniform(-100, W + 100)
    ln = rng.uniform(120, 500)
    c = mix([46, 51, 68], [111, 89, 84], rng.uniform(0.1, 0.5))
    add("sea_base", [(x - ln / 2, HORIZON + rng.normal(0, 0.8)),
                     (x + ln / 2, HORIZON + rng.normal(0, 0.8))],
        rng.uniform(1.0, 3.0), c, rng.uniform(0.055, 0.17), "round_soft", 0.35)

print("after sky/sun/clouds", len(strokes))

# ---------- sea base texture ----------
SEA_STOPS = [
    (0.00, [140, 110, 105]),
    (0.05, [108, 92, 97]),
    (0.14, [76, 78, 89]),
    (0.28, [50, 66, 79]),
    (0.46, [38, 57, 72]),
    (0.68, [29, 48, 63]),
    (0.85, [21, 39, 54]),
    (1.00, [14, 30, 44]),
]

def sea_base_color(t, x):
    c = color_at(SEA_STOPS, t)
    # warm patch below the sun, cools rapidly away from it
    width = 120.0 + 330.0 * t
    warm = math.exp(-((x - SUNX) / width) ** 2)
    c = mix(c, [134, 80, 62], 0.14 * warm * (1.0 - 0.30 * t))
    # subtle atmospheric desaturation of the far water
    c = mix(c, [126, 116, 118], 0.10 * math.exp(-t * 12))
    return c

# broad translucent water bands over the hard foundation
for i in range(330):
    t = float(rng.random() ** 0.80)
    y = HORIZON + 4 + (H - HORIZON - 2) * t
    x = float(rng.uniform(-140, W + 140))
    ln = float(rng.uniform(200, 650))
    sz = 18 + 190 * t + rng.uniform(-8, 18)
    c = jitter(sea_base_color(t, x), 2.4)
    add("sea_base", curve_pts(x, y, ln, rng.normal(0, 0.012), rng.normal(0, 2.0)),
        sz, c, rng.uniform(0.065, 0.19), "round_soft", 0.16)

# the horizon needs a little more density on the far water
for i in range(160):
    t = rng.uniform(0.0, 0.10)
    y = HORIZON + 2 + (H - HORIZON) * t
    x = rng.uniform(-80, W + 80)
    c = jitter(sea_base_color(t, x), 2.0)
    add("sea_base", [(x - rng.uniform(70, 220), y), (x + rng.uniform(70, 220), y + rng.normal(0, 0.8))],
        rng.uniform(8, 32), c, rng.uniform(0.06, 0.18), "round_soft", 0.18)

# broad sky-colour sheen on the far water, stronger toward the sun
for i in range(260):
    t = float(rng.random() ** 1.55) * 0.42
    y = HORIZON + 4 + (H - HORIZON) * t
    x = float(rng.uniform(-120, W + 120))
    w = math.exp(-((x - SUNX) / (420.0 + 160.0 * t)) ** 2)
    c = mix([122, 132, 146], [255, 177, 119], 0.24 + 0.58 * w)
    c = jitter(c, 3.0)
    add("sea_sheen", [(x - rng.uniform(80, 320), y + rng.normal(0, 1.5)),
                      (x + rng.uniform(80, 320), y + rng.normal(0, 1.5))],
        rng.uniform(18, 90) * (0.7 + 0.5 * t), c,
        rng.uniform(0.003, 0.012), "round_soft", 0.03)

# ---------- wave texture: broken facets riding perspective wave bands ----------
# Wave crests are coherent in y, but painted as interrupted facets, not as
# continuous contour lines.  That avoids both a flat sheet and a wire mesh.
def wave_curve(x, y0, wl, amp, phase):
    return (y0 + amp * (0.84 * math.sin(math.tau * x / wl + phase)
                        + 0.16 * math.sin(math.tau * x / (wl * 0.51) + phase * 1.57)))

def wave_slope(x, wl, amp, phase):
    return amp * (0.84 * math.tau / wl * math.cos(math.tau * x / wl + phase)
                  + 0.16 * math.tau / (wl * 0.51) * math.cos(math.tau * x / (wl * 0.51) + phase * 1.57))

def facet_path(xc, ln, y0, wl, amp, phase, y_off, npts=3):
    pts = []
    for q in range(npts):
        x = xc + (q / (npts - 1) - 0.5) * ln
        yy = wave_curve(x, y0, wl, amp, phase) + y_off + rng.normal(0, 0.18)
        pts.append((x, yy))
    return pts

WAVE_BANDS = 104
for j in range(WAVE_BANDS):
    u = (j + 0.5) / WAVE_BANDS
    t = u ** 1.42
    y0 = HORIZON + 5 + (H - HORIZON - 10) * t
    wl = 42.0 + 620.0 * t
    amp = 0.35 + 8.8 * t
    phase = rng.uniform(0, math.tau)
    nfac = int(round(44 - 24 * t))
    for k in range(nfac):
        x = float(rng.uniform(-40, W + 40))
        base = sea_base_color(t, x)
        warm_path = math.exp(-((x - SUNX) / (75 + 260 * t)) ** 2)
        q = rng.random()
        if q < 0.43:
            # shadowed face of a wavelet
            c = mix(base, [2, 8, 16], rng.uniform(0.18, 0.52))
            y_off = amp * rng.uniform(0.12, 0.42)
            op = rng.uniform(0.055, 0.20) * (0.65 + 0.55 * t)
            sz = (1.3 + 8.0 * t) * rng.uniform(0.55, 1.20)
            preset = "bristle" if t > 0.40 else "round_soft"
            hard = rng.uniform(0.32, 0.50)
        elif q < 0.86:
            # pale slope facing the sky
            c = mix(base, [145, 145, 137], rng.uniform(0.16, 0.52))
            c = mix(c, [255, 177, 113], 0.12 + 0.42 * warm_path)
            y_off = -amp * rng.uniform(0.04, 0.27)
            op = rng.uniform(0.045, 0.19) * (0.65 + 0.55 * t)
            sz = (0.8 + 4.8 * t) * rng.uniform(0.55, 1.18)
            preset = "round_soft"
            hard = rng.uniform(0.22, 0.42)
        else:
            # a few pale foam facets, especially in the sun path
            c = mix([168, 164, 151], [255, 213, 166], 0.35 + 0.55 * warm_path)
            c = mix(base, c, rng.uniform(0.45, 0.85))
            y_off = -amp * rng.uniform(0.10, 0.36)
            op = rng.uniform(0.045, 0.17)
            sz = (0.65 + 2.8 * t) * rng.uniform(0.55, 1.05)
            preset = "round_soft"
            hard = 0.26
            if rng.random() < 0.56:
                add("wave_crest", facet_path(x, (8 + 62 * t) * rng.uniform(0.4, 1.1),
                    y0, wl, amp, phase, y_off, 3), sz * 0.85, c,
                    rng.uniform(0.030, 0.12), "round_soft", 0.24)
        ln = (7 + 100 * t) * rng.uniform(0.35, 1.25)
        add("sea_waves", facet_path(x, ln, y0, wl, amp, phase, y_off, 3),
            sz, jitter(c, 2.4), op, preset, hard)

# near-field swell groups: a broad dark face plus interrupted glints on top
for i in range(130):
    t = 0.48 + 0.51 * (rng.random() ** 0.60)
    y0 = HORIZON + 5 + (H - HORIZON - 8) * t
    wl = 320 + 600 * t
    amp = 4.0 + 11.0 * t
    phase = rng.uniform(0, math.tau)
    xc = rng.uniform(-120, W + 120)
    base = sea_base_color(t, xc)
    warm_path = math.exp(-((xc - SUNX) / (75 + 230 * t)) ** 2)
    # broad, soft trough/face
    add("sea_waves", facet_path(xc, rng.uniform(220, 560), y0, wl, amp, phase,
        amp * rng.uniform(0.65, 1.10), 6), rng.uniform(9, 28),
        jitter(mix(base, [2, 8, 15], rng.uniform(0.28, 0.55)), 2.2),
        rng.uniform(0.045, 0.14), "round_soft", 0.18)
    # several broken pale facets along the same crest, not one continuous line
    for q in range(3):
        xx = xc + rng.uniform(-0.45, 0.45) * wl
        add("sea_waves", facet_path(xx, rng.uniform(25, 95), y0, wl, amp, phase,
            -amp * rng.uniform(0.10, 0.48), 3), rng.uniform(1.0, 3.5),
            jitter(mix(base, mix([145, 147, 140], [255, 181, 116], 0.25 + 0.55 * warm_path),
            rng.uniform(0.28, 0.62)), 2.0), rng.uniform(0.075, 0.23), "round_soft", 0.30)

# small wind ripples fill the gaps without forming a net
for i in range(680):
    t = float(rng.random() ** 1.10)
    y = HORIZON + 4 + (H - HORIZON - 6) * t + rng.normal(0, 2.0 + 8 * t)
    x = rng.uniform(-30, W + 30)
    ln = (4 + 42 * t) * rng.uniform(0.4, 1.2)
    sz = (0.5 + 2.2 * t) * rng.uniform(0.55, 1.05)
    base = sea_base_color(t, x)
    if rng.random() < 0.55:
        c = mix(base, [2, 9, 17], rng.uniform(0.16, 0.45))
    else:
        c = mix(base, [138, 140, 134], rng.uniform(0.14, 0.43))
        c = mix(c, [255, 174, 112], 0.36 * math.exp(-((x - SUNX) / (70 + 220 * t)) ** 2))
    add("sea_waves", [(x - ln / 2, y), (x + ln / 2, y + rng.normal(0, 0.6))],
        sz, jitter(c, 2.0), rng.uniform(0.025, 0.085), "round_soft", rng.uniform(0.25, 0.45))

# ---------- reflection: broad warm column + broken glitter ----------
for i in range(320):
    # more broad reflection low in the frame, where the column is wider
    t = float(rng.random() ** 0.70)
    y = HORIZON + 2 + (H - HORIZON - 3) * t
    width = 22 + 182 * (t ** 0.83)
    x = SUNX + rng.normal(0, width * 0.48)
    sz = 18 + 150 * t
    c = mix([255, 196, 126], [232, 107, 61], 0.28 + 0.50 * t)
    add("sun_path", [(x, y)], sz, c, rng.uniform(0.006, 0.020) * (1.05 - 0.25 * t),
        "round_soft", 0.035)

# narrow, hot core of the reflected sun near the horizon
for i in range(150):
    t = float(rng.random() ** 1.45)
    y = HORIZON + 2 + (H - HORIZON - 3) * t
    width = 3.5 + 42 * t
    x = SUNX + rng.normal(0, width * 0.40)
    c = mix([255, 231, 167], [255, 146, 78], 0.16 + 0.64 * t)
    add("sun_path", [(x - rng.uniform(2, 10 + 27 * t), y),
                     (x + rng.uniform(2, 10 + 27 * t), y + rng.normal(0, 0.9))],
        rng.uniform(1.2, 3.8 + 6 * t), c, rng.uniform(0.03, 0.125), "round_soft", 0.30)

# broken glitter clusters, perspective-distributed. Several tiny facets per
# group keep the path from becoming a regular ladder of horizontal bars.
for cluster in range(980):
    t = float(rng.random() ** 0.80)
    y0 = HORIZON + 3 + (H - HORIZON - 5) * t + rng.normal(0, 2.0 + 8 * t)
    width = 5 + 128 * (t ** 0.88)
    if rng.random() < 0.68:
        x0 = SUNX + rng.normal(0, width * 0.32)
    else:
        x0 = SUNX + rng.normal(0, width * 0.78)
    facets = 2 + int(rng.integers(0, 4))
    for jj in range(facets):
        x = x0 + rng.normal(0, 2.0 + width * 0.075)
        y = y0 + rng.normal(0, 1.4 + 6.0 * t)
        ln = (2.0 + 27.0 * t) * rng.uniform(0.25, 1.15)
        sz = (0.75 + 2.8 * t) * rng.uniform(0.50, 1.10)
        c = mix([255, 218, 157], [255, 126, 64], 0.10 + 0.82 * t)
        c = jitter(c, 3.0)
        op = rng.uniform(0.035, 0.23) * (1.0 - 0.25 * t)
        add("sun_glitter", curve_pts(x, y, ln, rng.normal(0, 0.018), rng.normal(0, 1.1)),
            sz, c, op, "round_soft", 0.30)

# a smaller amount of cool sky reflection and broken light away from the path
for i in range(240):
    t = float(rng.random() ** 0.90)
    y = HORIZON + 4 + (H - HORIZON - 5) * t + rng.normal(0, 2)
    x = rng.uniform(-40, W + 40)
    if abs(x - SUNX) < 80 + 160 * t:
        continue
    c = mix([180, 158, 151], [64, 87, 98], rng.uniform(0.25, 0.7))
    add("sea_waves", [(x - rng.uniform(8, 36 + 80 * t), y),
                      (x + rng.uniform(8, 36 + 80 * t), y + rng.normal(0, 1.0))],
        rng.uniform(0.8, 2.8 + 4 * t), c, rng.uniform(0.035, 0.11), "round_soft", 0.28)

# ---------- atmospheric bloom and vignette ----------
for i in range(80):
    t = float(rng.random())
    y = HORIZON + 5 + (H - HORIZON - 8) * t
    x = SUNX + rng.normal(0, 20 + 130 * t)
    c = mix([255, 209, 153], [255, 123, 68], 0.25 + 0.55 * t)
    add("atmosphere", [(x, y)], rng.uniform(30, 120) * (0.6 + 0.7 * t),
        c, rng.uniform(0.006, 0.020), "round_soft", 0.02)

# gentle darkening at all four corners; many soft dabs, no solid frame
corner_specs = [(-40, -50, 430), (W + 40, -60, 450), (-70, H + 70, 470), (W + 70, H + 80, 480)]
for cx, cy, spread in corner_specs:
    for i in range(42):
        x = cx + rng.normal(0, spread * 0.34)
        y = cy + rng.normal(0, spread * 0.28)
        add("vignette", [(x, y)], rng.uniform(150, 330), [22, 20, 34],
            rng.uniform(0.015, 0.050), "round_soft", 0.01)
# slightly darker top and bottom edges
for i in range(60):
    y = rng.choice([rng.uniform(-20, 45), rng.uniform(H - 55, H + 20)])
    x = rng.uniform(-50, W + 50)
    add("vignette", [(x, y)], rng.uniform(120, 280), [18, 19, 31],
        rng.uniform(0.012, 0.042), "round_soft", 0.01)

# fine film-like texture in the sky and far water (subtle)
for i in range(1800):
    y = float(rng.uniform(-20, H + 20))
    if y < HORIZON:
        c = sky_color(y, rng.uniform(0, W))
    else:
        t = clamp((y - HORIZON) / (H - HORIZON))
        c = sea_base_color(t, rng.uniform(0, W))
    delta = rng.normal(0, 11)
    c = [float(np.clip(c[0] + delta, 0, 255)), float(np.clip(c[1] + delta, 0, 255)),
         float(np.clip(c[2] + delta, 0, 255))]
    x = float(rng.uniform(-20, W + 20))
    add("texture", [(x, y), (x + rng.uniform(1, 5), y + rng.normal(0, 0.4))],
        rng.uniform(0.8, 2.6), c, rng.uniform(0.018, 0.055), "chalk", 0.2)

# ---------- layer order / write ----------
LAYERS = [
    ("base", "normal"),
    ("base_sky", "normal"),
    ("base_sea", "normal"),
    ("sky", "normal"),
    ("sun_glow", "screen"),
    ("sun_disc", "normal"),
    ("sun_rim", "screen"),
    ("clouds", "normal"),
    ("cloud_shadow", "multiply"),
    ("cloud_rim", "screen"),
    ("horizon_haze", "screen"),
    ("sea_base", "normal"),
    ("sea_sheen", "screen"),
    ("sea_waves", "normal"),
    ("wave_crest", "screen"),
    ("sun_path", "screen"),
    ("sun_glitter", "screen"),
    ("atmosphere", "screen"),
    ("vignette", "softlight"),
    ("texture", "softlight"),
]
layer_blends = dict(LAYERS)
layer_order = [n for n, _ in LAYERS]
OUT.mkdir(parents=True, exist_ok=True)
plan_path = OUT / "sunset.plan.json"
plan_io.dump_plan(str(plan_path), W, H, strokes,
                  layer_blends=layer_blends, layer_order=layer_order)
doc, ww, hh = plan_io.load_plan(str(plan_path))
t0 = time.perf_counter()
arr = RU.render_arr(doc, gpu=False, nthreads=8)
render_s = time.perf_counter() - t0
preview = OUT / "sunset_preview.png"
Image.fromarray(arr.astype(np.uint8), "RGBA").save(preview)
alpha = arr[..., 3]
print(json.dumps({
    "canvas": [ww, hh], "strokes": len(doc["strokes"]), "layers": len(doc["layers"]),
    "plan": str(plan_path), "preview": str(preview), "render_s": round(render_s, 3),
    "alpha_min": int(alpha.min()), "alpha_zero_pct": round(float((alpha == 0).mean() * 100), 3),
    "bytes": plan_path.stat().st_size, "presets": sorted({s["preset"] for s in doc["strokes"]}),
}, ensure_ascii=False))
