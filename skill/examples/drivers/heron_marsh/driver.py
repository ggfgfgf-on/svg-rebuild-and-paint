#!/usr/bin/env python3
"""Build the original painting "The Heron at the Marsh Edge".

No reference image is used: every mark is generated here from coordinates,
procedural colour fields, and deterministic pseudo-random variation.

Usage:
    python build_heron.py cold_out/final.plan.json
"""
from __future__ import annotations

import json
import math
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "scripts"))

import plan_io
import shape_ops

W, H = 1200, 900
HORIZON = 378.0
SUN = (884.0, 338.0)
rng = random.Random(20260312)

doc = {"canvas": [W, H], "layers": [], "strokes": []}


# ---------------------------------------------------------------------------
# Layer architecture: back-to-front, each layer owns one visual responsibility.
# ---------------------------------------------------------------------------
LAYER_ORDER = [
    "base",
    "sky_tones",
    "dawn_clouds",
    "far_marsh",
    "water_tones",
    "water_sheen",
    "reeds_far",
    "mist",
    "sun_glow",
    "reflection",
    "heron_legs",
    "heron_silhouette",
    "heron_form",
    "heron_feathers",
    "heron_detail",
    "heron_rim",
    "water_ripple",
    "reeds_front",
    "reed_rim",
    "vignette",
]
LAYER_BLENDS = {
    "base": "normal",
    "sky_tones": "normal",
    "dawn_clouds": "normal",
    "sun_glow": "screen",
    "far_marsh": "normal",
    "water_tones": "normal",
    "water_sheen": "screen",
    "mist": "screen",
    "reeds_far": "normal",
    "reflection": "normal",
    "heron_legs": "normal",
    "heron_silhouette": "normal",
    "heron_form": "normal",
    "heron_feathers": "normal",
    "heron_detail": "normal",
    "heron_rim": "screen",
    "water_ripple": "normal",
    "reeds_front": "normal",
    "reed_rim": "screen",
    "vignette": "multiply",
}


# ---------------------------------------------------------------------------
# Small marks, fills and colour helpers.
# ---------------------------------------------------------------------------
def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def mix(a, b, t):
    t = clamp(float(t), 0.0, 1.0)
    return tuple(a[i] + (b[i] - a[i]) * t for i in range(3))


def vary(c, amount=6.0):
    return tuple(clamp(c[i] + rng.uniform(-amount, amount), 0, 255) for i in range(3))


def add(layer, points, size, color, opacity=1.0, preset="round_soft",
        hardness=0.55, tag=None):
    pts = []
    for p in points:
        z = float(p[2]) if len(p) > 2 else 1.0
        pts.append([float(p[0]), float(p[1]), clamp(z, 0.0, 1.0)])
    s = {
        "layer": layer,
        "preset": preset,
        "size": float(max(0.5, size)),
        "opacity": float(clamp(opacity, 0.0, 1.0)),
        "color": [float(x) for x in color],
        "hardness": float(clamp(hardness, 0.0, 1.0)),
        "points": pts,
    }
    if tag:
        s["tag"] = tag
    doc["strokes"].append(s)


def fill(layer, polygon, **kw):
    shape_ops.fill_polygon(doc, layer, polygon, **kw)


def taper(layer, d, size, color, size_end=1.0, opacity=1.0, steps=18,
          preset="liner", hardness=0.5, copies=1, spread=0.0,
          pressure=1.0, pressure_end=None, tag=None):
    shape_ops.tapered_stroke(
        doc, layer, d, color=color, size=size, size_end=size_end,
        steps=steps, opacity=opacity, preset=preset, hardness=hardness,
        copies=copies, spread=spread, pressure=pressure,
        pressure_end=pressure_end, tag=tag,
    )


def ellipse(cx, cy, rx, ry, n=48, rotation=0.0):
    cr, sr = math.cos(math.radians(rotation)), math.sin(math.radians(rotation))
    out = []
    for i in range(n):
        a = 2.0 * math.pi * i / n
        x, y = rx * math.cos(a), ry * math.sin(a)
        out.append((cx + x * cr - y * sr, cy + x * sr + y * cr))
    return out


def wavy(x, y, length, amplitude=1.8, n=4, phase=None):
    phase = rng.uniform(0, math.tau) if phase is None else phase
    out = []
    for i in range(n + 1):
        t = i / n
        out.append((
            x + length * t,
            y + amplitude * math.sin(t * math.tau + phase) + rng.uniform(-0.7, 0.7),
        ))
    return out


def cubic_path(points):
    """Build a smooth SVG path through control points (Mac-like smooth chain)."""
    if len(points) < 2:
        return ""
    d = f"M {points[0][0]:.2f} {points[0][1]:.2f}"
    if len(points) == 2:
        d += f" L {points[1][0]:.2f} {points[1][1]:.2f}"
        return d
    for i in range(1, len(points) - 1):
        p0 = points[i - 1]
        p1 = points[i]
        p2 = points[i + 1]
        c1 = (p1[0] + (p2[0] - p0[0]) / 6.0,
              p1[1] + (p2[1] - p0[1]) / 6.0)
        c2 = (p2[0] - (p2[0] - p0[0]) / 6.0,
              p2[1] - (p2[1] - p0[1]) / 6.0)
        d += f" C {c1[0]:.2f} {c1[1]:.2f} {c2[0]:.2f} {c2[1]:.2f} {p2[0]:.2f} {p2[1]:.2f}"
    return d


SKY_STOPS = [
    (0.00, (43, 58, 78)),
    (0.18, (68, 84, 102)),
    (0.36, (111, 116, 126)),
    (0.54, (151, 140, 145)),
    (0.70, (190, 157, 143)),
    (0.84, (223, 179, 151)),
    (1.00, (240, 203, 166)),
]


def sky_color(y, x=600.0):
    t = clamp(y / HORIZON, 0.0, 1.0)
    for i in range(len(SKY_STOPS) - 1):
        a, ca = SKY_STOPS[i]
        b, cb = SKY_STOPS[i + 1]
        if t <= b:
            c = mix(ca, cb, (t - a) / max(1e-6, b - a))
            break
    else:
        c = SKY_STOPS[-1][1]
    # a little rose warmth toward the low sun and a cool left edge
    warm = math.exp(-((y - 315.0) / 140.0) ** 2) * math.exp(-((x - SUN[0]) / 560.0) ** 2)
    c = (c[0] + 10 * warm, c[1] + 5 * warm, c[2] + 1 * warm)
    return tuple(clamp(v, 0, 255) for v in c)


def water_color(y, x=600.0):
    t = clamp((y - HORIZON) / (H - HORIZON), 0.0, 1.0)
    top = (144, 143, 129)
    mid = (75, 103, 100)
    low = (31, 60, 59)
    if t < 0.42:
        c = mix(top, mid, t / 0.42)
    else:
        c = mix(mid, low, (t - 0.42) / 0.58)
    # dawn warmth remains longest near the sun's watery path
    path = math.exp(-((x - SUN[0]) / (130 + 150 * t)) ** 2) * (1.0 - 0.48 * t)
    horizon_glow = math.exp(-((y - 430) / 260.0) ** 2)
    c = (c[0] + 34 * path * horizon_glow,
         c[1] + 18 * path * horizon_glow,
         c[2] + 5 * path * horizon_glow)
    return tuple(clamp(v, 0, 255) for v in c)


# ---------------------------------------------------------------------------
# 1. Opaque ground and dawn sky.
# ---------------------------------------------------------------------------
fill("base", [(0, 0), (W, 0), (W, H), (0, H)],
     color=(28, 43, 45), size=175, spacing=46,
     opacity=1.0, preset="round_hard", hardness=0.75, edge=False)

fill("sky_tones", [(0, 0), (W, 0), (W, HORIZON + 8), (0, HORIZON + 8)],
     color=sky_color(0), color2=sky_color(HORIZON), gradient_angle=90,
     size=96, spacing=18, opacity=0.94, preset="flat", hardness=0.55,
     edge=False, jitter=0.38, seed=11)

# Long, atmospheric horizontal passages keep the sky from looking airbrushed.
for i in range(185):
    y = rng.uniform(8, 365)
    t = y / HORIZON
    length = rng.uniform(115, 470) * (0.75 + 0.5 * t)
    x = rng.uniform(-180, W + 20)
    c = vary(sky_color(y, x + length * 0.5), 8)
    add("sky_tones", wavy(x, y, length, rng.uniform(0.6, 4.0), 4),
        rng.uniform(24, 72), c, rng.uniform(0.035, 0.105),
        rng.choice(["round_soft", "flat", "round_soft"]),
        rng.uniform(0.12, 0.42))

# Layered dawn cloud banks: dark mauve undersides, peach and silver rims.
cloud_bands = [
    (92, 34, 31, (83, 94, 111), (151, 142, 147)),
    (144, 25, 26, (96, 99, 116), (169, 148, 149)),
    (207, 20, 23, (125, 110, 122), (205, 161, 149)),
    (275, 14, 18, (145, 123, 125), (230, 181, 155)),
]
for center, sy, count, dark, light in cloud_bands:
    for j in range(count):
        x = rng.uniform(-220, W + 30)
        length = rng.uniform(150, 560)
        y = center + rng.gauss(0, sy)
        c = mix(dark, light, clamp((y - (center - sy)) / (2 * sy), 0, 1))
        c = vary(c, 5)
        add("dawn_clouds", wavy(x, y, length, rng.uniform(1.8, 6.0), 5),
            rng.uniform(18, 46), c, rng.uniform(0.055, 0.15),
            rng.choice(["round_soft", "bristle"]), rng.uniform(0.18, 0.5))
    # broken lit edges, especially toward the sun
    for j in range(max(8, count // 2)):
        x = rng.uniform(350, W + 80)
        length = rng.uniform(90, 340)
        y = center + rng.uniform(6, sy * 0.75)
        c = vary(mix(light, (245, 202, 168), rng.uniform(0.15, 0.55)), 7)
        add("dawn_clouds", wavy(x, y, length, rng.uniform(0.8, 2.5), 4),
            rng.uniform(8, 22), c, rng.uniform(0.045, 0.105),
            "round_soft", rng.uniform(0.12, 0.3))

# The sun itself is almost dissolved by mist: a few soft veils and many fine dabs.
add("sun_glow", [(SUN[0], SUN[1])], 420, (255, 205, 143), 0.018, "round_soft", 0.08)
add("sun_glow", [(SUN[0], SUN[1])], 230, (255, 220, 161), 0.035, "round_soft", 0.10)
add("sun_glow", [(SUN[0], SUN[1])], 105, (255, 238, 192), 0.085, "round_soft", 0.16)
add("sun_glow", [(SUN[0], SUN[1])], 58, (255, 241, 198), 0.105, "round_soft", 0.18)
add("sun_glow", [(SUN[0], SUN[1])], 27, (255, 247, 213), 0.135, "round_soft", 0.22)
for i in range(300):
    ang = rng.uniform(0, math.tau)
    rad = abs(rng.gauss(0.0, 1.0))
    x = SUN[0] + math.cos(ang) * rad * rng.uniform(45, 245)
    y = SUN[1] + math.sin(ang) * rad * rng.uniform(18, 105)
    if y > 376:
        continue
    warm = clamp(1.0 - math.hypot((x - SUN[0]) / 300.0, (y - SUN[1]) / 110.0), 0, 1)
    c = mix((255, 205, 142), (255, 247, 217), warm)
    add("sun_glow", [(x, y)], rng.uniform(3, 18), c,
        rng.uniform(0.012, 0.052), "round_soft", rng.uniform(0.06, 0.24))
for i in range(110):
    x = SUN[0] + rng.gauss(0, 27)
    y = SUN[1] + rng.gauss(0, 14)
    add("sun_glow", [(x, y)], rng.uniform(3.0, 17.0),
        mix((255, 224, 167), (255, 247, 216), rng.random()),
        rng.uniform(0.022, 0.072), "round_soft", 0.16)
for i in range(28):
    x = SUN[0] + rng.gauss(0, 9)
    y = SUN[1] + rng.gauss(0, 6)
    add("sun_glow", [(x, y)], rng.uniform(2.0, 6.0),
        (255, 248, 218), rng.uniform(0.06, 0.14), "round_soft", 0.2)

# ---------------------------------------------------------------------------
# 2. Far marsh: low silhouette, distant reed beds, and a light horizon.
# ---------------------------------------------------------------------------
far_edge = [
    (0, 371), (55, 362), (110, 359), (164, 354), (230, 357),
    (300, 348), (360, 352), (430, 344), (500, 348), (565, 340),
    (635, 344), (705, 337), (770, 343), (835, 339), (902, 346),
    (970, 341), (1040, 347), (1110, 342), (1200, 349),
]
far_poly = far_edge + [(1200, 395), (0, 400)]
fill("far_marsh", far_poly, color=(49, 66, 64), color2=(95, 92, 71),
     gradient_angle=0, size=19, spacing=5.8, opacity=0.96,
     preset="bristle", hardness=0.58, edge=True, edge_inset=3.0, seed=22)

# A few low, rounded tree masses and far reed crowns.
for cx, cy, rx, ry, c in [
    (97, 355, 82, 25, (46, 62, 60)),
    (188, 350, 62, 18, (54, 67, 62)),
    (1035, 350, 92, 25, (51, 65, 60)),
    (1135, 353, 64, 18, (45, 60, 57)),
]:
    fill("far_marsh", ellipse(cx, cy, rx, ry, 40),
         color=c, size=12, spacing=4.6, opacity=0.82,
         preset="bristle", hardness=0.5, edge=False)

# Clumped far reeds leave openings for mist and the low sun.
far_reed_centres = [36, 92, 176, 244, 470, 548, 628, 748, 782, 1038, 1112, 1182]
for i in range(205):
    if rng.random() < 0.92:
        x = rng.choice(far_reed_centres) + rng.gauss(0, 36)
    else:
        x = rng.uniform(-10, W + 10)
    if 815 < x < 1000 and rng.random() < 0.88:
        continue
    base = rng.uniform(363, 384)
    h = rng.uniform(9, 48)
    lean = rng.uniform(-8, 8)
    c = rng.choice([(61, 75, 68), (73, 82, 69), (84, 87, 70), (96, 91, 71)])
    c = vary(c, 5)
    add("reeds_far", [(x, base), (x + lean * 0.5, base - h * 0.6), (x + lean, base - h)],
        rng.uniform(1.0, 2.5), c, rng.uniform(0.18, 0.43),
        rng.choice(["liner", "round_soft"]), rng.uniform(0.25, 0.55))

# ---------------------------------------------------------------------------
# 3. Still water: broad gradient, horizontal passages, then reflected light.
# ---------------------------------------------------------------------------
fill("water_tones", [(0, 376), (W, 376), (W, H), (0, H)],
     color=water_color(378, 600), color2=water_color(900, 600),
     gradient_angle=90, size=110, spacing=22, opacity=0.93,
     preset="flat", hardness=0.6, edge=False, jitter=0.32, seed=31)

for i in range(360):
    t = rng.random() ** 1.5
    y = 384 + (H - 384) * t
    depth = (y - HORIZON) / (H - HORIZON)
    length = rng.uniform(32, 190) * (0.55 + 1.65 * depth)
    x = rng.uniform(-120, W + 40)
    c = vary(water_color(y, x + length * 0.5), 7)
    add("water_tones", wavy(x, y, length, rng.uniform(0.4, 2.4), 4),
        rng.uniform(2.0, 15.0) * (0.75 + depth),
        c, rng.uniform(0.035, 0.115),
        rng.choice(["flat", "bristle"]), rng.uniform(0.25, 0.62))

# Pale horizon and soft broken sky reflections across the wide water.
for i in range(220):
    t = rng.random() ** 2.0
    y = 386 + 118 * t
    x = rng.uniform(-60, W)
    length = rng.uniform(28, 180)
    c = vary(sky_color(350 - 80 * t, x), 8)
    add("water_sheen", wavy(x, y, length, rng.uniform(0.3, 1.7), 3),
        rng.uniform(1.4, 7.5), c, rng.uniform(0.045, 0.14),
        "flat", rng.uniform(0.2, 0.5))

# The sun's reflection: a narrow, broken ladder of gold on still water.
for i in range(210):
    t = rng.random() ** 1.65
    y = 388 + 365 * t
    spread = 18 + 112 * t
    x = SUN[0] + rng.gauss(0, spread)
    length = rng.uniform(5, 34) * (0.8 + 2.0 * t)
    c = mix((235, 186, 118), (255, 231, 177), rng.random())
    add("water_sheen", wavy(x, y, length, rng.uniform(0.2, 1.4), 3),
        rng.uniform(1.0, 4.8), c, rng.uniform(0.08, 0.30),
        "flat", rng.uniform(0.15, 0.4))

# Luminous mist lying across the far marsh and the water's first strip.
for i in range(95):
    y = rng.gauss(385, 24)
    x = rng.uniform(-160, W + 60)
    length = rng.uniform(160, 620)
    c = mix((172, 184, 177), (231, 195, 170), rng.uniform(0.05, 0.8))
    add("mist", wavy(x, y, length, rng.uniform(1.0, 4.0), 5),
        rng.uniform(56, 150), vary(c, 4), rng.uniform(0.014, 0.055),
        "round_soft", rng.uniform(0.05, 0.19))

# ---------------------------------------------------------------------------
# 4. Reflections: reeds and the heron share the same still-water grammar.
# ---------------------------------------------------------------------------
# Near-horizon reflections of the far bed.
for i in range(120):
    x = rng.uniform(0, W)
    y0 = 386 + rng.uniform(0, 18)
    ln = rng.uniform(8, 58)
    c = rng.choice([(54, 74, 69), (88, 92, 75), (113, 109, 88)])
    pts = [(x, y0), (x + rng.uniform(-2, 2), y0 + ln * 0.55),
           (x + rng.uniform(-4, 4), y0 + ln)]
    add("reflection", pts, rng.uniform(1.0, 3.4), vary(c, 5),
        rng.uniform(0.06, 0.18), "liner", 0.35)

# Faint, broken reed reflections below the foreground stands.
for side, x0, x1 in [("left", -20, 250), ("right", 1010, 1220)]:
    for i in range(70):
        x = rng.uniform(x0, x1)
        y0 = rng.uniform(620, 790)
        ln = rng.uniform(18, 100)
        c = rng.choice([(34, 53, 47), (53, 69, 55), (74, 77, 59)])
        pts = [(x, y0), (x + rng.uniform(-7, 7), y0 + ln * 0.5),
               (x + rng.uniform(-10, 10), y0 + ln)]
        add("reflection", pts, rng.uniform(1.2, 4.0), vary(c, 4),
            rng.uniform(0.045, 0.14), "liner", 0.28)

# The heron's reflection: broad soft columns, deliberately interrupted.
for i in range(78):
    x = 338 + 218 * rng.betavariate(1.15, 1.15)
    y0 = rng.uniform(718, 742)
    ln = rng.uniform(42, 152)
    edge_dark = x < 405 or x > 523
    c = (47, 61, 65) if edge_dark else (161, 166, 158)
    if 455 < x < 525:
        c = mix(c, (219, 213, 191), 0.45)
    pts = [
        (x, y0),
        (x + rng.uniform(-11, 11), y0 + ln * 0.32),
        (x + rng.uniform(-16, 16), y0 + ln * 0.66),
        (x + rng.uniform(-22, 22), y0 + ln),
    ]
    add("reflection", pts, rng.uniform(7.0, 18.0), vary(c, 8),
        rng.uniform(0.022, 0.075), "round_soft", rng.uniform(0.10, 0.26))
for i in range(32):
    x = rng.uniform(350, 555)
    y0 = rng.uniform(744, 862)
    add("reflection", wavy(x, y0, rng.uniform(24, 78), rng.uniform(0.3, 1.6), 3),
        rng.uniform(2.0, 6.5), vary(rng.choice([(64, 83, 84), (128, 140, 133), (176, 176, 158)]), 6),
        rng.uniform(0.025, 0.075), "flat", 0.25)

# ---------------------------------------------------------------------------
# 5. The heron: a tall grey silhouette with a long S-neck and dagger bill.
# ---------------------------------------------------------------------------
# Long legs are put down before the body so the body's feather mass covers their roots.
leg_specs = [
    dict(path=[(421, 512), (397, 552), (409, 602), (398, 648), (405, 692), (394, 728)],
         size=12, end=4.5),
    dict(path=[(468, 516), (485, 566), (470, 620), (483, 670), (474, 704), (489, 730)],
         size=11, end=4.0),
]
for leg in leg_specs:
    d = cubic_path(leg["path"])
    taper("heron_legs", d, leg["size"], (58, 59, 51), size_end=leg["end"],
          opacity=0.98, steps=24, preset="round_soft", hardness=0.72)
    # a narrow lit edge on the sunward side
    lit = [(x + 2.5, y) for x, y in leg["path"]]
    taper("heron_legs", cubic_path(lit), 3.2, (139, 125, 90), size_end=1.2,
          opacity=0.28, steps=18, preset="liner", hardness=0.35)
    # toes at the waterline
    fx, fy = leg["path"][-1]
    for toe, spread, rise in [(-22, -8, 3), (2, 0, 0), (28, 9, 4)]:
        taper("heron_legs",
              f"M {fx:.1f} {fy:.1f} Q {fx + toe * 0.52 + spread:.1f} {fy + rise:.1f} {fx + toe:.1f} {fy + 3:.1f}",
              4.2, (55, 56, 48), size_end=1.2, opacity=0.72, steps=10,
              preset="liner", hardness=0.5)

# Body silhouette (slightly enlarged by the brush edge) and long tail feathers.
body = [
    (311, 477), (329, 451), (359, 430), (395, 418), (437, 412),
    (479, 418), (514, 432), (534, 452), (540, 478), (531, 506),
    (507, 529), (473, 541), (430, 544), (390, 539), (355, 528),
    (327, 510), (309, 494),
]
tail = [(336, 474), (307, 485), (281, 501), (270, 515), (289, 520),
        (317, 513), (345, 503)]
fill("heron_silhouette", body, color=(55, 68, 73), size=13, spacing=5.0,
     opacity=0.98, preset="round_hard", hardness=0.82, edge=True,
     edge_inset=2.5, seed=101)
fill("heron_silhouette", tail, color=(50, 61, 67), size=10, spacing=4.2,
     opacity=0.95, preset="bristle", hardness=0.68, edge=True,
     edge_inset=1.8, seed=102)

neck_center = [
    (497, 448), (515, 426), (530, 400), (536, 374),
    (532, 349), (522, 329), (511, 310), (503, 290), (505, 270),
]
neck_widths = [78, 68, 58, 52, 48, 44, 40, 36, 33]
neck_poly = shape_ops.ribbon(neck_center, neck_widths)
fill("heron_silhouette", neck_poly, color=(58, 71, 76), size=10, spacing=4.0,
     opacity=0.98, preset="round_hard", hardness=0.8, edge=True,
     edge_inset=2.0, seed=103)
head = ellipse(505, 259, 31, 24, 52, rotation=-8)
fill("heron_silhouette", head, color=(56, 66, 70), size=9, spacing=3.5,
     opacity=0.97, preset="round_hard", hardness=0.8, edge=True,
     edge_inset=1.6, seed=104)
bill = [(524, 249), (539, 257), (643, 280), (646, 287), (537, 275), (523, 266)]
fill("heron_silhouette", bill, color=(64, 59, 52), size=7, spacing=2.8,
     opacity=0.96, preset="round_hard", hardness=0.78, edge=True,
     edge_inset=1.2, seed=105)

# Body colour masses. The light comes from the low sun at the right.
fill("heron_form", body, color=(82, 97, 102), color2=(142, 151, 149),
     gradient_angle=315, size=11, spacing=4.6, opacity=0.78,
     preset="round_soft", hardness=0.32, edge=False, seed=111)
fill("heron_form", neck_poly, color=(164, 175, 174), color2=(218, 217, 204),
     gradient_angle=300, size=8.5, spacing=3.2, opacity=0.88,
     preset="round_soft", hardness=0.35, edge=False, seed=112)

# The soft dark channel of the neck's back, then a pale front edge.
back_neck = [(x - 9, y + 2) for x, y in neck_center]
back_poly = shape_ops.ribbon(back_neck, [30, 26, 23, 20, 19, 17, 16, 14, 12])
fill("heron_form", back_poly, color=(91, 104, 108), size=7, spacing=3.4,
     opacity=0.52, preset="round_soft", hardness=0.30, edge=False, seed=113)
front_neck = [(x + 10, y + 1) for x, y in neck_center]
front_poly = shape_ops.ribbon(front_neck, [18, 16, 14, 12, 12, 11, 10, 9, 8])
fill("heron_form", front_poly, color=(219, 221, 208), size=6, spacing=3.2,
     opacity=0.45, preset="round_soft", hardness=0.30, edge=False, seed=114)

# Head and white cheek.
fill("heron_form", head, color=(171, 181, 179), color2=(222, 220, 207),
     gradient_angle=315, size=7, spacing=3.0, opacity=0.88,
     preset="round_soft", hardness=0.32, edge=False, seed=115)
cheek = ellipse(512, 271, 21, 14, 40, rotation=-12)
fill("heron_form", cheek, color=(225, 224, 211), color2=(246, 242, 221),
     gradient_angle=315, size=5, spacing=2.6, opacity=0.86,
     preset="round_soft", hardness=0.5, edge=False, seed=116)

# Chest / shoulders, belly shade, wing shield, and dark primary feathers.
chest = [(474, 430), (505, 439), (530, 461), (537, 485),
         (523, 513), (492, 531), (455, 535), (470, 508), (479, 477)]
fill("heron_form", chest, color=(185, 191, 184), color2=(225, 221, 204),
     gradient_angle=300, size=9, spacing=3.8, opacity=0.72,
     preset="round_soft", hardness=0.30, edge=False, seed=117)
belly = [(350, 511), (388, 530), (430, 541), (474, 536), (503, 521),
         (478, 541), (432, 547), (386, 539)]
fill("heron_form", belly, color=(60, 76, 82), size=8, spacing=4.2,
     opacity=0.55, preset="round_soft", hardness=0.30, edge=False, seed=118)

wing = [(362, 444), (392, 424), (432, 415), (475, 421), (512, 439),
        (522, 463), (510, 490), (487, 514), (455, 530), (420, 530),
        (391, 514), (369, 490), (352, 467)]
fill("heron_form", wing, color=(91, 107, 112), color2=(151, 159, 157),
     gradient_angle=315, size=9, spacing=3.9, opacity=0.74,
     preset="round_soft", hardness=0.34, edge=False, seed=119)
wing_light = [(384, 435), (418, 421), (456, 421), (488, 434), (506, 451),
              (490, 460), (451, 452), (413, 452), (389, 455)]
fill("heron_form", wing_light, color=(154, 164, 163), color2=(181, 185, 180),
     gradient_angle=315, size=7, spacing=3.5, opacity=0.44,
     preset="round_soft", hardness=0.28, edge=False, seed=120)
primaries = [(353, 472), (380, 480), (398, 499), (380, 515), (338, 526),
             (302, 530), (277, 521), (305, 501)]
fill("heron_form", primaries, color=(55, 69, 76), color2=(76, 89, 94),
     gradient_angle=0, size=8, spacing=3.5, opacity=0.86,
     preset="round_soft", hardness=0.48, edge=False, seed=121)

# Bill: ochre wedge with a dark culmen and a little warm tip.
fill("heron_form", bill, color=(154, 119, 67), color2=(214, 176, 105),
     gradient_angle=0, size=5.5, spacing=2.4, opacity=0.93,
     preset="round_soft", hardness=0.62, edge=False, seed=122)
taper("heron_form", "M 527 253 C 561 259 605 270 642 282",
      3.0, (246, 213, 151), size_end=1.0, opacity=0.55, steps=22,
      preset="liner", hardness=0.4)
taper("heron_form", "M 527 276 C 563 280 608 283 642 286",
      3.0, (96, 74, 49), size_end=1.0, opacity=0.58, steps=22,
      preset="liner", hardness=0.4)

# ---------------------------------------------------------------------------
# 6. Feather architecture: coverts, scapulars, primaries, neck and head marks.
# ---------------------------------------------------------------------------
# Wing covert rows, each feather a short curved painterly mark.
for row, (ybase, yoff) in enumerate([(441, 0), (454, 4), (467, 7), (480, 9)]):
    n = 13 - row
    for i in range(n):
        t = i / max(1, n - 1)
        x0 = 382 + 96 * t + rng.uniform(-4, 4)
        y0 = ybase + 15 * t + rng.uniform(-3, 3)
        ln = rng.uniform(19, 36)
        c = mix((99, 115, 118), (191, 194, 185), rng.uniform(0.28, 0.82))
        add("heron_feathers", [
            (x0, y0),
            (x0 - ln * 0.52, y0 + rng.uniform(-1, 3)),
            (x0 - ln, y0 + rng.uniform(3, 8)),
        ], rng.uniform(2.2, 5.0), vary(c, 7),
            rng.uniform(0.28, 0.62), "bristle", rng.uniform(0.3, 0.6))

# Long scapular feathers over the back.
for i in range(32):
    x0 = rng.uniform(356, 477)
    y0 = rng.uniform(423, 461)
    ln = rng.uniform(30, 82)
    c = mix((84, 100, 105), (157, 163, 159), rng.uniform(0.2, 0.9))
    add("heron_feathers", [
        (x0, y0),
        (x0 - ln * 0.5, y0 + rng.uniform(2, 12)),
        (x0 - ln, y0 + rng.uniform(8, 26)),
    ], rng.uniform(3.0, 6.8), vary(c, 6),
        rng.uniform(0.2, 0.5), "bristle", rng.uniform(0.3, 0.6))

# Dark primaries: long, slightly separated blades toward the tail tip.
for i in range(13):
    t = i / 12.0
    x0 = 397 - 48 * t
    y0 = 478 + 24 * t
    ln = rng.uniform(74, 124)
    add("heron_feathers", [
        (x0, y0),
        (x0 - ln * 0.48, y0 + rng.uniform(8, 20)),
        (x0 - ln, y0 + rng.uniform(18, 38)),
    ], rng.uniform(3.2, 7.2), vary(mix((49, 63, 70), (99, 110, 111), rng.random()), 5),
        rng.uniform(0.48, 0.82), "bristle", rng.uniform(0.4, 0.7))

# Breast and flank striations.
for i in range(34):
    x0 = rng.uniform(455, 522)
    y0 = rng.uniform(440, 526)
    ln = rng.uniform(12, 38)
    c = mix((112, 123, 123), (233, 228, 211), rng.uniform(0.2, 0.9))
    add("heron_feathers", [(x0, y0), (x0 - ln * 0.4, y0 + ln * 0.45),
                            (x0 - ln * 0.8, y0 + ln)],
        rng.uniform(1.2, 3.4), vary(c, 5), rng.uniform(0.15, 0.42),
        "liner", rng.uniform(0.3, 0.55))

# Neck feather lines: fine highlights on the front, smoke on the back.
for i in range(18):
    t = i / 17.0
    xoff = rng.uniform(-8, 8)
    ystart = 435 - 155 * t
    xstart = 506 + 12 * math.sin(t * 4.4) + xoff
    ln = rng.uniform(24, 56)
    c = rng.choice([(215, 216, 204), (162, 174, 173), (104, 118, 121)])
    add("heron_feathers", [
        (xstart, ystart),
        (xstart + rng.uniform(-5, 8), ystart - ln * 0.55),
        (xstart + rng.uniform(-9, 6), ystart - ln),
    ], rng.uniform(1.5, 4.0), vary(c, 6), rng.uniform(0.16, 0.42),
        "liner", rng.uniform(0.28, 0.52))

# Head details: black crown, trailing crest, eye stripe, white face.
crown = [(477, 248), (483, 238), (496, 232), (512, 232), (526, 238),
         (532, 247), (522, 252), (505, 247), (489, 250)]
fill("heron_detail", crown, color=(31, 39, 42), color2=(57, 65, 65),
     gradient_angle=0, size=5, spacing=2.3, opacity=0.94,
     preset="round_soft", hardness=0.72, edge=True, edge_inset=1.0, seed=140)
for i in range(4):
    y = 239 + i * 3.2
    taper("heron_detail", f"M 486 {y:.1f} C 468 {y+2.5:.1f} 447 {y+7:.1f} 427 {y+15:.1f}",
          rng.uniform(2.0, 3.8), (34, 43, 46), size_end=0.6,
          opacity=rng.uniform(0.42, 0.68), steps=18, preset="liner", hardness=0.42)
taper("heron_detail", "M 489 244 C 463 242 435 249 412 266",
      2.8, (38, 46, 48), size_end=0.6, opacity=0.48, steps=22,
      preset="liner", hardness=0.38)

# Eye: black surround, amber iris, black pupil, one pinpoint of dawn.
add("heron_detail", [(519, 256)], 11.0, (29, 37, 40), 0.94, "round_hard", 0.82)
add("heron_detail", [(519, 256)], 6.2, (166, 116, 54), 1.0, "round_hard", 0.9)
add("heron_detail", [(519.4, 256.2)], 3.4, (22, 29, 31), 1.0, "round_hard", 0.95)
add("heron_detail", [(520.0, 254.9)], 1.3, (247, 236, 203), 0.9, "round_soft", 0.8)

# A few fine facial lines and the dark gape.
taper("heron_detail", "M 525 265 C 538 269 555 274 578 279",
      2.0, (93, 71, 46), size_end=0.7, opacity=0.6, steps=15,
      preset="liner", hardness=0.4)
taper("heron_detail", "M 523 263 C 508 266 497 268 489 272",
      1.6, (78, 76, 65), size_end=0.6, opacity=0.42, steps=12,
      preset="liner", hardness=0.4)
add("heron_detail", [(536, 266)], 2.4, (54, 57, 54), 0.8, "round_soft", 0.7)
# nostril
add("heron_detail", [(548, 264)], 2.3, (66, 55, 43), 0.8, "round_soft", 0.7)

# Sparse fine feathers overlapping the silhouette edge.
for i in range(24):
    x0 = rng.uniform(320, 510)
    y0 = rng.uniform(420, 525)
    ln = rng.uniform(10, 28)
    add("heron_feathers", [(x0, y0), (x0 - ln, y0 + rng.uniform(2, 11))],
        rng.uniform(1.2, 2.8), vary((123, 136, 137), 8),
        rng.uniform(0.12, 0.32), "liner", 0.35)

# Rim light on the sunward edges: neck, head, shoulder and wing.
rim_paths = [
    ("M 520 442 C 540 416 555 387 551 360 C 547 335 532 319 521 297 C 515 286 514 277 515 269", 4.5, 1.2, 0.28),
    ("M 512 236 C 527 237 536 245 536 254 C 536 261 531 266 524 268", 3.2, 1.0, 0.38),
    ("M 498 429 C 527 439 542 458 536 482 C 533 494 527 503 518 512", 4.0, 1.1, 0.22),
    ("M 390 426 C 422 416 460 417 490 432", 3.5, 1.0, 0.18),
]
for d, s0, s1, op in rim_paths:
    taper("heron_rim", d, s0, (243, 224, 181), size_end=s1,
          opacity=op, steps=22, preset="liner", hardness=0.32)
# A warm edge on the bill.
taper("heron_rim", "M 535 250 C 573 258 615 268 642 279",
      2.2, (255, 233, 174), size_end=0.7, opacity=0.34, steps=20,
      preset="liner", hardness=0.3)

# ---------------------------------------------------------------------------
# 7. Water surface response at the heron's feet.
# ---------------------------------------------------------------------------
for cx, cy, base in [(398, 730, 34), (486, 732, 30)]:
    for k in range(3):
        rx = base + 20 * k
        ry = 2.8 + 1.7 * k
        pts = []
        for j in range(15):
            a = math.pi * (j / 14.0)
            pts.append((cx - rx + 2 * rx * j / 14.0,
                        cy + math.sin(a) * ry + rng.uniform(-0.5, 0.5)))
        add("water_ripple", pts, 1.10 - 0.10 * k,
            (191, 198, 182), 0.095 - 0.02 * k, "liner", 0.25)

# Wide, quiet glints that unify the foreground water.
for i in range(120):
    y = rng.uniform(610, 875)
    x = rng.uniform(-60, W)
    ln = rng.uniform(18, 120)
    c = rng.choice([(151, 160, 145), (109, 130, 124), (193, 183, 155)])
    add("water_ripple", wavy(x, y, ln, rng.uniform(0.3, 1.5), 3),
        rng.uniform(0.9, 2.6), vary(c, 6), rng.uniform(0.03, 0.11),
        "flat", rng.uniform(0.15, 0.38))

# Horizontal water colour cuts across the reflected columns so they read as
# broken reflections rather than vertical hatching.
for i in range(88):
    y = rng.uniform(729, 876)
    x = rng.uniform(285, 620)
    ln = rng.uniform(14, 88)
    c = vary(water_color(y, x + ln * 0.5), 5)
    add("water_ripple", wavy(x, y, ln, rng.uniform(0.15, 1.0), 3),
        rng.uniform(1.8, 7.0), c, rng.uniform(0.07, 0.22),
        "flat", rng.uniform(0.15, 0.35))

# ---------------------------------------------------------------------------
# 8. Reed framing: tall stems, blades, cattails, and a few lit edges.
# ---------------------------------------------------------------------------
def reed_cluster(x0, x1, y0, y1, count, h0, h1, palette, cattail_p, seed):
    local = random.Random(seed)
    for i in range(count):
        bx = local.uniform(x0, x1)
        by = local.uniform(y0, y1)
        h = local.uniform(h0, h1)
        lean = local.uniform(-58, 58) * (h / 500.0)
        topx = bx + lean
        topy = by - h
        midx = bx + lean * 0.26 + local.uniform(-14, 14)
        midy = by - h * 0.58
        d = f"M {bx:.1f} {by:.1f} Q {midx:.1f} {midy:.1f} {topx:.1f} {topy:.1f}"
        c = vary(local.choice(palette), 6)
        taper("reeds_front", d, local.uniform(2.4, 5.0), c,
              size_end=local.uniform(0.65, 1.15),
              opacity=local.uniform(0.56, 0.94), steps=14,
              preset=local.choice(["liner", "round_soft"]),
              hardness=local.uniform(0.38, 0.68))
        # seed head / cattail on some stems
        if local.random() < cattail_p:
            ch = local.uniform(28, 58)
            cx = topx
            add("reeds_front", [(cx, topy + 4), (cx + local.uniform(-2, 2), topy - ch * 0.5),
                                 (cx + local.uniform(-4, 4), topy - ch)],
                local.uniform(10, 16), vary(local.choice([(66, 49, 35), (79, 57, 39), (55, 47, 36)]), 6),
                local.uniform(0.78, 0.96), "round_soft", local.uniform(0.5, 0.75))
            add("reeds_front", [(cx + 2, topy + 2), (cx + 3, topy - ch * 0.52), (cx + 4, topy - ch)],
                1.6, (102, 79, 52), local.uniform(0.14, 0.28), "liner", 0.35)
            taper("reeds_front", f"M {topx:.1f} {topy-ch:.1f} L {topx+local.uniform(-3,3):.1f} {topy-ch-local.uniform(14,36):.1f}",
                  1.4, c, size_end=0.5, opacity=0.7, steps=7, preset="liner", hardness=0.4)
        # arched leaves
        for j in range(local.randint(1, 3)):
            fy = by - h * local.uniform(0.14, 0.72)
            fx = bx + lean * (by - fy) / max(1.0, h)
            side = local.choice([-1, 1])
            reach = local.uniform(36, 150)
            curve = local.uniform(18, 72)
            ex = fx + side * reach
            ey = fy - local.uniform(16, 95)
            dleaf = (f"M {fx:.1f} {fy:.1f} Q {fx + side*curve:.1f} {fy-local.uniform(10,45):.1f} "
                     f"{ex:.1f} {ey:.1f}")
            lc = vary(local.choice([(38, 57, 46), (48, 65, 49), (63, 73, 51), (76, 81, 57)]), 7)
            taper("reeds_front", dleaf, local.uniform(3.0, 6.8), lc,
                  size_end=local.uniform(0.45, 0.9),
                  opacity=local.uniform(0.42, 0.82), steps=13,
                  preset=local.choice(["liner", "bristle"]),
                  hardness=local.uniform(0.3, 0.58))
        # occasional seed plume
        if local.random() < 0.18:
            for k in range(4):
                px, py = topx, topy - local.uniform(0, 18)
                taper("reeds_front",
                      f"M {px:.1f} {py:.1f} Q {px+local.uniform(-18,18):.1f} {py-local.uniform(10,25):.1f} {px+local.uniform(-34,34):.1f} {py-local.uniform(24,54):.1f}",
                      1.4, (114, 99, 71), size_end=0.4, opacity=0.54, steps=8,
                      preset="liner", hardness=0.3)
        # occasional warm sun-caught rim on the right of a stem
        if local.random() < 0.18:
            hx = bx + 2.0
            taper("reed_rim",
                  f"M {hx:.1f} {by-local.uniform(40,120):.1f} Q {midx+3:.1f} {midy:.1f} {topx+2:.1f} {topy+local.uniform(10,50):.1f}",
                  1.5, (201, 160, 100), size_end=0.4,
                  opacity=local.uniform(0.08, 0.18), steps=12,
                  preset="liner", hardness=0.3)

# Left-hand stand: the strongest dark frame, kept outside the bird.
reed_cluster(-34, 198, 815, 945, 34, 335, 690,
             [(33, 51, 44), (42, 60, 47), (52, 66, 51), (63, 71, 51)],
             0.17, 701)
reed_cluster(104, 300, 760, 900, 15, 220, 400,
             [(42, 60, 49), (57, 71, 54), (74, 80, 57)],
             0.08, 702)
# Right-hand stand: lighter, quieter, allowing the sun room.
reed_cluster(1000, 1240, 775, 940, 21, 250, 560,
             [(38, 56, 47), (52, 66, 50), (68, 74, 53)],
             0.10, 703)
reed_cluster(926, 1080, 820, 930, 9, 150, 300,
             [(45, 62, 49), (61, 72, 54)],
             0.06, 704)

# Two closer, almost-black blades crossing the left margin for depth.
for d, size, c, op in [
    ("M -8 900 C 78 760 84 618 142 470", 8.0, (29, 45, 40), 0.9),
    ("M 44 900 C 137 793 158 692 235 577", 5.2, (37, 53, 43), 0.82),
    ("M 205 900 C 170 790 150 706 137 612", 4.2, (47, 62, 44), 0.72),
]:
    taper("reeds_front", d, size, c, size_end=0.8, opacity=op,
          steps=18, preset="round_soft", hardness=0.5)

# A few foreground ripple sheens around the reed bases, drawn after the reeds.
for i in range(55):
    y = rng.uniform(720, 885)
    x = rng.uniform(-30, 300) if rng.random() < 0.7 else rng.uniform(890, W + 20)
    ln = rng.uniform(14, 92)
    add("water_ripple", wavy(x, y, ln, rng.uniform(0.2, 1.2), 3),
        rng.uniform(0.8, 2.2), (178, 177, 151), rng.uniform(0.025, 0.075),
        "flat", 0.25)

# ---------------------------------------------------------------------------
# 9. Final optical glaze: soft corner darkening, no painted frame.
# ---------------------------------------------------------------------------
for i in range(44):
    edge = rng.choice(["tl", "tr", "bl", "br", "left", "right"])
    if edge == "tl":
        x, y = rng.uniform(-80, 230), rng.uniform(-80, 180)
    elif edge == "tr":
        x, y = rng.uniform(970, 1280), rng.uniform(-80, 180)
    elif edge == "bl":
        x, y = rng.uniform(-80, 230), rng.uniform(720, 980)
    elif edge == "br":
        x, y = rng.uniform(970, 1280), rng.uniform(720, 980)
    elif edge == "left":
        x, y = rng.uniform(-80, 100), rng.uniform(150, 750)
    else:
        x, y = rng.uniform(1100, 1280), rng.uniform(150, 750)
    add("vignette", [(x, y)], rng.uniform(190, 330),
        (27, 45, 47), rng.uniform(0.010, 0.035), "round_soft", 0.06)


# ---------------------------------------------------------------------------
# Serialize the single source of truth and print a compact build report.
# ---------------------------------------------------------------------------
def main():
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "cold_out" / "final.plan.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    count = plan_io.dump_plan(
        str(out), W, H, doc["strokes"],
        layer_blends=LAYER_BLENDS,
        layer_order=LAYER_ORDER,
    )
    elapsed = time.perf_counter() - t0
    present_layers = [n for n in LAYER_ORDER if any(s["layer"] == n for s in doc["strokes"])]
    presets = sorted({s["preset"] for s in doc["strokes"]})
    print(json.dumps({
        "title": "The Heron at the Marsh Edge",
        "canvas": [W, H],
        "strokes": count,
        "layers": len(present_layers),
        "layer_names": present_layers,
        "presets": presets,
        "plan": str(out),
        "plan_bytes": out.stat().st_size,
        "build_s": round(elapsed, 3),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
