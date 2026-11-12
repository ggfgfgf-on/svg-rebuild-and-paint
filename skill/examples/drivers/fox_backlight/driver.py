#!/usr/bin/env python3
"""Build "The Fox at the Forest Edge" from scratch.

The scene is assembled as a documented plan (canvas + layers + strokes) and is
rendered by scripts/render_plan.py.  No reference image is read or traced.
"""
from __future__ import annotations

import math
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import plan_io
import shape_ops

W, H = 1200, 900
OUT = Path(__file__).resolve().parent
rng = random.Random(0xF0C5_1200)
SEED = [7100]

LAYER_ORDER = [
    "base", "sky", "dawn_glow", "distant_forest", "back_mist",
    "godrays", "midforest", "canopy", "ground", "ground_detail",
    "floor_mist", "boulder", "boulder_detail", "moss",
    "fox_under", "fox_coat", "fox_detail", "fox_rim",
    "foreground", "atmosphere", "vignette", "grade",
    "color_glow",
]
LAYER_BLENDS = {
    "base": "normal", "sky": "normal", "dawn_glow": "screen",
    "distant_forest": "normal", "back_mist": "normal",
    "midforest": "normal", "canopy": "normal", "godrays": "screen",
    "ground": "normal", "ground_detail": "normal", "floor_mist": "normal",
    "boulder": "normal", "boulder_detail": "normal", "moss": "normal",
    "fox_under": "normal", "fox_coat": "normal", "fox_detail": "normal",
    "fox_rim": "screen", "foreground": "normal", "atmosphere": "screen",
    "vignette": "softlight", "grade": "softlight", "color_glow": "overlay",
}
doc = {
    "canvas": [W, H],
    "layers": [{"name": n, "blend": LAYER_BLENDS[n]} for n in LAYER_ORDER],
    "strokes": [],
    "count": 0,
}


def clamp(v, lo=0.0, hi=255.0):
    return max(lo, min(hi, v))


def C(color):
    return [clamp(float(color[0])), clamp(float(color[1])), clamp(float(color[2]))]


def mix(a, b, t):
    t = max(0.0, min(1.0, float(t)))
    return [a[0] + (b[0] - a[0]) * t,
            a[1] + (b[1] - a[1]) * t,
            a[2] + (b[2] - a[2]) * t]


def vary(color, amt, local=None):
    r = local or rng
    return C([color[0] + r.uniform(-amt, amt),
              color[1] + r.uniform(-amt, amt),
              color[2] + r.uniform(-amt, amt)])


def S(layer, pts, size, color, opacity=1.0, preset="round_soft",
      hardness=0.6, pressure=1.0):
    points = []
    for p in pts:
        points.append([float(p[0]), float(p[1]),
                       float(p[2]) if len(p) > 2 else float(pressure)])
    doc["strokes"].append({
        "layer": layer,
        "preset": preset,
        "size": float(max(0.5, size)),
        "opacity": float(max(0.0, min(1.0, opacity))),
        "color": C(color),
        "hardness": float(max(0.0, min(1.0, hardness))),
        "points": points,
    })


def dab(layer, x, y, size, color, opacity=1.0, preset="round_soft",
        hardness=0.5):
    S(layer, [(x, y, 1.0)], size, color, opacity, preset, hardness)


def point_in(x, y, poly):
    inside = False
    j = len(poly) - 1
    for i in range(len(poly)):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def bbox(poly):
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    return min(xs), min(ys), max(xs), max(ys)


def sample_line(points, t):
    pts = [(float(p[0]), float(p[1])) for p in points]
    seg = [math.dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1)]
    total = sum(seg)
    target = max(0.0, min(1.0, t)) * total
    acc = 0.0
    for i, sl in enumerate(seg):
        if acc + sl >= target or i == len(seg) - 1:
            u = 0.0 if sl < 1e-9 else (target - acc) / sl
            x = pts[i][0] + (pts[i + 1][0] - pts[i][0]) * u
            y = pts[i][1] + (pts[i + 1][1] - pts[i][1]) * u
            tx = (pts[i + 1][0] - pts[i][0]) / max(1e-6, sl)
            ty = (pts[i + 1][1] - pts[i][1]) / max(1e-6, sl)
            return x, y, tx, ty
        acc += sl
    x, y = pts[-1]
    return x, y, 1.0, 0.0


def smooth_path(points):
    pts = [(float(p[0]), float(p[1])) for p in points]
    if len(pts) < 2:
        raise ValueError("need >=2 points")
    d = f"M {pts[0][0]:.2f} {pts[0][1]:.2f}"
    if len(pts) == 2:
        return d + f" L {pts[1][0]:.2f} {pts[1][1]:.2f}"
    for i in range(len(pts) - 1):
        p0 = pts[i - 1] if i > 0 else pts[i]
        p1 = pts[i]
        p2 = pts[i + 1]
        p3 = pts[i + 2] if i + 2 < len(pts) else pts[i + 1]
        c1x = p1[0] + (p2[0] - p0[0]) / 6.0
        c1y = p1[1] + (p2[1] - p0[1]) / 6.0
        c2x = p2[0] - (p3[0] - p1[0]) / 6.0
        c2y = p2[1] - (p3[1] - p1[1]) / 6.0
        d += f" C {c1x:.2f} {c1y:.2f} {c2x:.2f} {c2y:.2f} {p2[0]:.2f} {p2[1]:.2f}"
    return d


def next_seed():
    SEED[0] += 1
    return SEED[0]


def fill_poly(layer, poly, color, color2=None, size=20.0, spacing=None,
              opacity=1.0, preset="round_soft", jitter=0.35, angle=0.0,
              gradient_angle=0.0, hardness=0.6, edge=True, edge_inset=0.0,
              pressure=1.0):
    shape_ops.fill_polygon(
        doc, layer, poly, color=color, color2=color2, size=size,
        spacing=spacing, opacity=opacity, preset=preset, jitter=jitter,
        angle=angle, gradient_angle=gradient_angle, hardness=hardness,
        edge=edge, edge_inset=edge_inset, pressure=pressure,
        seed=next_seed(),
    )


def taper(layer, points, color, size, size_end, steps=20, opacity=1.0,
          preset="round_soft", hardness=0.6, copies=1, spread=0.0,
          pressure=1.0, pressure_end=None):
    d = points if isinstance(points, str) else smooth_path(points)
    shape_ops.tapered_stroke(
        doc, layer, d, color=color, size=size, size_end=size_end,
        steps=steps, opacity=opacity, preset=preset, hardness=hardness,
        copies=copies, spread=spread, pressure=pressure,
        pressure_end=pressure_end, seed=next_seed(),
    )


def ribbon_fill(layer, points, widths, color, color2=None, size=14.0,
                spacing=None, opacity=1.0, preset="bristle", angle=90.0,
                hardness=0.65, edge=True, edge_inset=0.0):
    poly = shape_ops.ribbon(points, widths)
    fill_poly(layer, poly, color, color2=color2, size=size, spacing=spacing,
              opacity=opacity, preset=preset, angle=angle,
              gradient_angle=90.0, hardness=hardness, edge=edge,
              edge_inset=edge_inset)
    return poly


def scatter_fill(layer, poly, n, size_range, color_fn, opacity_range=(0.15, 0.5),
                 preset_choices=("round_soft",), hardness_range=(0.3, 0.7),
                 angle_fn=None, len_factor=(0.15, 0.65), max_tries=None):
    x0, y0, x1, y1 = bbox(poly)
    count = 0
    tries = 0
    limit = max_tries or n * 18
    while count < n and tries < limit:
        tries += 1
        x = rng.uniform(x0, x1)
        y = rng.uniform(y0, y1)
        if not point_in(x, y, poly):
            continue
        size = rng.uniform(*size_range)
        ang = angle_fn(x, y, rng=rng) if angle_fn else rng.uniform(-math.pi, math.pi)
        L = size * rng.uniform(*len_factor)
        p1 = [x - math.cos(ang) * L * 0.5, y - math.sin(ang) * L * 0.5]
        p2 = [x + math.cos(ang) * L * 0.5, y + math.sin(ang) * L * 0.5]
        op = rng.uniform(*opacity_range)
        preset = rng.choice(preset_choices)
        hard = rng.uniform(*hardness_range)
        S(layer, [p1, p2], size, color_fn(x, y), op, preset, hard)
        count += 1


def radial_glow(layer, cx, cy, rx, ry, n, center_color, edge_color,
                size0, size1, op0, op1, preset="round_soft", hardness=0.12):
    for _ in range(n):
        ang = rng.uniform(0, math.tau)
        rr = math.sqrt(rng.random())
        x = cx + math.cos(ang) * rx * rr
        y = cy + math.sin(ang) * ry * rr
        t = rr
        size = size0 + (size1 - size0) * (rr ** 0.8) + rng.uniform(-8, 8)
        op = op0 + (op1 - op0) * (rr ** 1.2)
        dab(layer, x, y, max(2.0, size), mix(center_color, edge_color, t),
            max(0.002, op), preset, hardness)


def fog_cloud(layer, cx, cy, rx, ry, n, color0, color1,
              size0, size1, op0, op1=0.0003, hardness=0.03):
    """An elliptical fog pocket whose alpha falls off before its edge."""
    for _ in range(n):
        ang = rng.uniform(0, math.tau)
        rr = math.sqrt(rng.random())
        x = cx + math.cos(ang) * rx * rr
        y = cy + math.sin(ang) * ry * rr
        fall = max(0.0, 1.0 - rr)
        col = mix(color0, color1, rr * 0.85)
        size = size0 + (size1 - size0) * rr
        op = op1 + (op0 - op1) * (fall ** 1.25)
        dab(layer, x, y, max(2.0, size + rng.uniform(-size * 0.16, size * 0.16)),
            col, op * rng.uniform(0.55, 1.0), "round_soft", hardness)


# ---------------------------------------------------------------------------
# 1. opaque base and dawn sky
# ---------------------------------------------------------------------------
dab("base", W * 0.5, H * 0.5, 1660, [24, 34, 35], 1.0, "round_hard", 1.0)

SKY_STOPS = [
    (0.00, [31, 48, 55]),
    (0.14, [52, 72, 75]),
    (0.32, [93, 110, 108]),
    (0.49, [179, 154, 126]),
    (0.62, [228, 174, 132]),
    (0.76, [241, 195, 147]),
]


def sky_gradient(y):
    y = max(0.0, min(0.999, y / H))
    for i in range(len(SKY_STOPS) - 1):
        y0, c0 = SKY_STOPS[i]
        y1, c1 = SKY_STOPS[i + 1]
        if y0 <= y <= y1:
            t = (y - y0) / max(1e-6, y1 - y0)
            return mix(c0, c1, t)
    return list(SKY_STOPS[-1][1])


def sky_color(x, y):
    base = sky_gradient(y)
    dx = (x - 860.0) / 500.0
    dy = (y - 300.0) / 270.0
    glow = math.exp(-(dx * dx + dy * dy) * 1.8)
    base = mix(base, [237, 193, 146], 0.50 * glow)
    edge = max(0.0, abs(x - 600.0) / 600.0)
    base = mix(base, [30, 45, 50], 0.16 * (edge ** 2.2) * (1.0 - y / H))
    return vary(base, 6)


# broad overlapping sky strokes: opaque enough to bury the base, soft enough to read as air
for y in range(-40, 670, 24):
    for k in range(14):
        x = rng.uniform(-80, W + 80)
        L = rng.uniform(90, 290)
        ang = rng.uniform(-0.10, 0.10)
        yy = y + rng.uniform(-14, 14)
        col = sky_color(x, yy)
        S("sky", [(x - L * math.cos(ang), yy - L * math.sin(ang)),
                  (x + L * math.cos(ang), yy + L * math.sin(ang))],
          rng.uniform(70, 175), col, rng.uniform(0.20, 0.52),
          "round_soft", rng.uniform(0.06, 0.24))

# horizontal high cloud veils
for _ in range(170):
    x = rng.uniform(30, W - 30)
    y = rng.uniform(40, 420)
    L = rng.uniform(80, 390)
    col = mix(sky_gradient(y), [225, 188, 176] if y > 220 else [136, 153, 159],
              rng.uniform(0.10, 0.40))
    S("sky", [(x - L / 2, y + rng.uniform(-6, 6)),
              (x, y + rng.uniform(-10, 3)),
              (x + L / 2, y + rng.uniform(-7, 7))],
      rng.uniform(8, 38), col, rng.uniform(0.025, 0.10),
      "round_soft" if rng.random() < 0.8 else "dry",
      rng.uniform(0.08, 0.25))

# visible brushwork through the opening, so the sky is painted rather than a gradient
for _ in range(280):
    x = rng.uniform(330, 1080)
    y = rng.uniform(70, 570)
    L = rng.uniform(75, 300)
    aa = rng.uniform(-0.16, 0.10)
    col = mix(sky_color(x, y), [206, 172, 149] if y > 250 else [123, 142, 149],
              rng.uniform(0.04, 0.20))
    S("sky", [(x - math.cos(aa) * L / 2, y - math.sin(aa) * L / 2),
              (x + math.cos(aa) * L / 2, y + math.sin(aa) * L / 2)],
      rng.uniform(14, 66), vary(col, 6),
      rng.uniform(0.020, 0.072),
      rng.choice(("round_soft", "dry", "chalk")), rng.uniform(0.12, 0.38))
for _ in range(80):
    x = rng.uniform(250, 1020)
    y = rng.uniform(150, 470)
    L = rng.uniform(60, 240)
    aa = rng.uniform(-0.20, 0.12)
    col = mix([224, 184, 164], [176, 177, 166], rng.uniform(0, 0.7))
    S("sky", [(x - math.cos(aa) * L / 2, y - math.sin(aa) * L / 2),
              (x + math.cos(aa) * L / 2, y + math.sin(aa) * L / 2)],
      rng.uniform(9, 38), vary(col, 7), rng.uniform(0.025, 0.085),
      "round_soft", rng.uniform(0.10, 0.28))

# the first warm light, kept as a diffuse pool so no hard-edged sun disc
radial_glow("dawn_glow", 860, 294, 430, 250, 310,
            [255, 237, 197], [243, 181, 126], 120, 330, 0.020, 0.006)
for _ in range(64):
    ang = rng.uniform(0, math.tau)
    rr = math.sqrt(rng.random()) * 70
    dab("dawn_glow", 860 + math.cos(ang) * rr,
        294 + math.sin(ang) * rr * 0.72,
        rng.uniform(10, 34), [255, 239, 205],
        rng.uniform(0.018, 0.050), "round_soft", 0.08)

# ---------------------------------------------------------------------------
# 2. distant forest: low-contrast conifers and trunks in the mist
# ---------------------------------------------------------------------------
far_trees = [
    (28, 610, 370, 132), (82, 605, 305, 100), (137, 612, 260, 78),
    (194, 604, 340, 114), (250, 610, 240, 82), (305, 607, 300, 96),
    (366, 611, 215, 69), (423, 606, 292, 89), (490, 612, 175, 59),
    (548, 608, 232, 67), (616, 613, 174, 51), (675, 608, 216, 64),
    (733, 612, 168, 52), (795, 605, 245, 72), (858, 612, 205, 61),
    (920, 607, 286, 91), (985, 612, 334, 112), (1050, 606, 302, 108),
    (1120, 611, 350, 127), (1180, 605, 288, 136),
]
far_cols = [[54, 72, 63], [66, 82, 67], [82, 93, 72], [93, 100, 78]]
for idx, (x, base, h, w) in enumerate(far_trees):
    top = base - h
    col = far_cols[idx % len(far_cols)]
    op = 0.20 + 0.10 * ((idx * 7) % 5) / 4.0
    # soft central spire with a broad, mist-eaten base
    S("distant_forest", [(x + rng.uniform(-2, 2), top),
                          (x + rng.uniform(-5, 5), base)],
      max(2.0, w * 0.042), mix(col, [48, 62, 57], 0.35),
      op * 0.85, "round_soft", 0.32)
    for _ in range(8):
        t = rng.uniform(0.35, 0.98)
        dab("distant_forest", x + rng.uniform(-w * 0.12, w * 0.12),
            top + h * t, rng.uniform(7, 22), vary(col, 5),
            rng.uniform(0.025, 0.075), "round_soft", 0.12)
    # a soft triangular underpainting gives the conifer a readable mass
    tri = [(x, top), (x - w * 0.46, base), (x + w * 0.46, base)]
    fill_poly("distant_forest", tri,
              mix(col, [110, 112, 88], 0.22),
              color2=mix(col, [120, 118, 90], 0.36),
              size=26, spacing=19, opacity=0.065 + op * 0.10,
              preset="round_soft", jitter=0.72, angle=90,
              gradient_angle=90, hardness=0.18, edge=False)
    # irregular branch dabs make a conical mass instead of a ladder
    for k in range(56):
        t = (k + rng.uniform(-0.35, 0.35)) / 56.0
        t = max(0.04, min(0.98, t))
        y = top + h * t
        reach = w * 0.50 * (t ** 0.82)
        side = rng.choice([-1, 1])
        x0 = x + rng.uniform(-reach * 0.20, reach * 0.20)
        x1 = x + side * reach * rng.uniform(0.65, 1.08)
        droop = h * 0.028 * rng.uniform(0.4, 1.2)
        size = rng.uniform(1.6, 7.2)
        col2 = mix(col, [119, 119, 88], rng.uniform(0.0, 0.18))
        S("distant_forest", [(x0, y), (x1, y + droop)],
          size, vary(col2, 5), op * rng.uniform(0.44, 0.94),
          rng.choice(("dry", "round_soft", "chalk")), rng.uniform(0.25, 0.55))
    # a little ragged light on the sunward crown
    for _ in range(5):
        yy = top + h * rng.uniform(0.05, 0.55)
        side = 1
        reach2 = w * 0.5 * ((yy - top) / max(1, h)) ** 0.82
        S("distant_forest",
          [(x + reach2 * rng.uniform(0.30, 0.58), yy),
           (x + reach2 * rng.uniform(0.72, 1.00), yy + rng.uniform(2, 10))],
          rng.uniform(0.8, 2.6), mix(col2, [141, 136, 97], 0.35),
          op * rng.uniform(0.28, 0.58), "liner", 0.30)

# thin far trunks, less structured than the conifers
for _ in range(58):
    x = rng.uniform(0, W)
    top = rng.uniform(120, 390)
    base = rng.uniform(545, 635)
    w = rng.uniform(2.0, 10.0)
    col = vary(rng.choice(far_cols), 5)
    S("distant_forest", [(x, top), (x + rng.uniform(-12, 12), base)],
      w, col, rng.uniform(0.10, 0.26), "dry", 0.45)
    if rng.random() < 0.45:
        side = -1 if rng.random() < 0.5 else 1
        yy = top + rng.uniform(45, 160)
        S("distant_forest", [(x, yy),
                             (x + side * rng.uniform(18, 65), yy + rng.uniform(8, 38))],
          rng.uniform(0.7, 1.9), col, rng.uniform(0.04, 0.11), "round_soft", 0.25)

# broad, fog-veiled trees add depth behind the opening
for _ in range(24):
    x = rng.uniform(250, 1010)
    top = rng.uniform(160, 390)
    base = rng.uniform(590, 640)
    width = rng.uniform(9, 37)
    col = mix(rng.choice(far_cols), [136, 136, 111], rng.uniform(0.10, 0.35))
    side = rng.uniform(-35, 35)
    S("distant_forest", [(x, top), (x + side, (top + base) / 2),
                          (x + side * 0.5, base)],
      width, col, rng.uniform(0.075, 0.16), "round_soft", 0.20)
    for _ in range(3):
        yy = top + (base - top) * rng.uniform(0.18, 0.72)
        ss = rng.choice([-1, 1])
        S("distant_forest", [(x + side * 0.5, yy),
                             (x + side * 0.5 + ss * rng.uniform(20, 85),
                              yy + rng.uniform(8, 35))],
          rng.uniform(1.8, 4.5), col, rng.uniform(0.045, 0.11),
          "round_soft", 0.25)

# distant undergrowth and a low, fog-softened forest floor
for _ in range(220):
    x = rng.uniform(-30, W + 30)
    y = max(490, min(618, rng.gauss(556, 32)))
    L = rng.uniform(18, 86)
    col = mix([58, 77, 64], [94, 99, 77], rng.uniform(0, 0.8))
    S("distant_forest", [(x - L / 2, y + rng.uniform(-3, 3)),
                          (x + L / 2, y + rng.uniform(-3, 3))],
      rng.uniform(10, 36), vary(col, 5),
      rng.uniform(0.028, 0.105), "round_soft", 0.25)
for cx, cy, rx, ry, n in [(230, 580, 210, 38, 85),
                           (470, 592, 170, 34, 65),
                           (880, 585, 190, 36, 75),
                           (1110, 570, 170, 38, 75)]:
    for _ in range(n):
        ang = rng.uniform(0, math.tau)
        rr = math.sqrt(rng.random())
        x = cx + math.cos(ang) * rx * rr
        y = cy + math.sin(ang) * ry * rr
        size = rng.uniform(3, 14)
        S("distant_forest", [(x, y), (x + rng.uniform(-9, 9), y - size * rng.uniform(0.4, 1.7))],
          size, vary(mix([42, 62, 50], [84, 91, 66], rng.random()), 7),
          rng.uniform(0.04, 0.16), rng.choice(("dry", "chalk", "sponge")),
          rng.uniform(0.25, 0.55))

# ---------------------------------------------------------------------------
# 3. back mist: pale atmosphere between the distant and mid trees
# ---------------------------------------------------------------------------
# fog pockets, not a rectangular veil: alpha falls off inside each pocket
for cx, cy, rx, ry, n, c0, c1, s0, s1, op0 in [
    (130, 545, 280, 105, 140, [204, 201, 181], [188, 193, 178], 42, 130, 0.0075),
    (420, 492, 300, 120, 160, [218, 210, 188], [199, 198, 180], 38, 125, 0.0070),
    (735, 448, 340, 105, 190, [236, 205, 173], [208, 199, 179], 40, 145, 0.0080),
    (1060, 538, 280, 115, 150, [207, 205, 184], [190, 195, 178], 36, 122, 0.0072),
    (610, 606, 330, 78, 130, [207, 203, 181], [190, 194, 176], 28, 92, 0.0055),
]:
    fog_cloud("back_mist", cx, cy, rx, ry, n, c0, c1, s0, s1, op0, 0.0003, 0.03)
# warm air pooling under the opening, and a cooler pocket in the left shadow
fog_cloud("back_mist", 810, 495, 330, 120, 180,
          [235, 201, 168], [211, 197, 176], 28, 105, 0.0060, 0.0002, 0.025)
fog_cloud("back_mist", 205, 585, 235, 85, 130,
          [156, 178, 173], [146, 172, 168], 34, 112, 0.0060, 0.0002, 0.03)
fog_cloud("back_mist", 300, 470, 300, 125, 160,
          [167, 185, 180], [153, 177, 173], 28, 104, 0.0055, 0.0002, 0.025)

# ---------------------------------------------------------------------------
# 4. mid-ground tree trunks, branches and canopy
# ---------------------------------------------------------------------------
TRUNKS = [
    dict(pts=[(62, -60), (78, 180), (72, 390), (92, 570), (116, 710)],
         widths=[98, 105, 112, 134, 172], shadow=[29, 37, 32],
         mid=[49, 55, 43], light=[94, 90, 61], n=430),
    dict(pts=[(255, -60), (258, 205), (270, 430), (294, 610)],
         widths=[39, 48, 60, 88], shadow=[25, 35, 31],
         mid=[45, 53, 42], light=[84, 83, 58], n=300),
    dict(pts=[(408, -40), (396, 180), (404, 390), (424, 598)],
         widths=[22, 30, 42, 56], shadow=[39, 49, 42],
         mid=[62, 68, 51], light=[101, 98, 70], n=220),
    dict(pts=[(1160, -80), (1144, 185), (1148, 440), (1118, 700)],
         widths=[104, 120, 142, 188], shadow=[27, 35, 31],
         mid=[49, 54, 41], light=[98, 91, 62], n=470),
    dict(pts=[(1030, -50), (1022, 205), (1040, 440), (1048, 626)],
         widths=[38, 50, 66, 84], shadow=[30, 39, 34],
         mid=[51, 57, 44], light=[88, 86, 61], n=260),
]

for tr in TRUNKS:
    pts, widths = tr["pts"], tr["widths"]
    poly = shape_ops.ribbon(pts, widths)
    fill_poly("midforest", poly, tr["mid"], color2=tr["light"],
              size=22, spacing=9, opacity=0.96, preset="bristle",
              jitter=0.50, angle=89, gradient_angle=0.0,
              hardness=0.72, edge=True, edge_inset=4)
    # broad vertical masses give the trunk a cylindrical read
    broad = 42
    for i in range(broad):
        t = (i + rng.random()) / broad
        cx, cy, tx, ty = sample_line(pts, t)
        u = t * (len(widths) - 1)
        wi = min(len(widths) - 2, int(u))
        wt = u - wi
        width = widths[wi] * (1 - wt) + widths[wi + 1] * wt
        nx, ny = -ty, tx
        side = rng.uniform(-0.80, 0.80)
        off = -side * width * 0.36
        col = mix(tr["shadow"], tr["light"], max(0.0, min(1.0, (1.0 - side) * 0.52)))
        L = rng.uniform(55, 170)
        ang = math.atan2(ty, tx) + rng.uniform(-0.025, 0.025)
        S("midforest",
          [(cx + nx * off - math.cos(ang) * L * 0.5,
            cy + ny * off - math.sin(ang) * L * 0.5),
           (cx + nx * off + math.cos(ang) * L * 0.5,
            cy + ny * off + math.sin(ang) * L * 0.5)],
          rng.uniform(20, 62), vary(col, 4),
          rng.uniform(0.08, 0.23), "round_soft", 0.34)
    # a broken warm ridge down the sunward side
    edge_pts = []
    for k in range(22):
        t = k / 21.0
        cx, cy, tx, ty = sample_line(pts, t)
        u = t * (len(widths) - 1)
        wi = min(len(widths) - 2, int(u))
        wt = u - wi
        width = widths[wi] * (1 - wt) + widths[wi + 1] * wt
        nx, ny = -ty, tx
        edge_pts.append((cx - nx * width * 0.34 + rng.uniform(-2, 2),
                         cy - ny * width * 0.34 + rng.uniform(-3, 3)))
    taper("midforest", edge_pts, mix(tr["light"], [158, 134, 84], 0.30),
          8.0, 2.8, steps=24, opacity=0.26, preset="dry", hardness=0.42)

    # dark/light side accents first, then bark scratches
    n = int(tr["n"] * 0.78)
    for i in range(n):
        t = (i + rng.random()) / n
        cx, cy, tx, ty = sample_line(pts, t)
        # total-width interpolation
        ws = widths
        wsx = []
        for p in pts:
            wsx.append(0.0)
        u = t * (len(widths) - 1)
        wi = min(len(widths) - 2, int(u))
        wt = u - wi
        width = widths[wi] * (1 - wt) + widths[wi + 1] * wt
        nx, ny = -ty, tx
        # bias toward both edges because bark ridges live there
        side = rng.uniform(-1.0, 1.0)
        if rng.random() < 0.40:
            side = rng.choice([-1, 1]) * rng.uniform(0.70, 0.98)
        off = side * width * 0.5
        x = cx + nx * off + rng.uniform(-2.5, 2.5)
        y = cy + ny * off + rng.uniform(-4, 4)
        lit = max(0.0, min(1.0, (1.0 - side) * 0.5 + rng.uniform(-0.13, 0.13)))
        col = mix(tr["shadow"], tr["light"], lit)
        if rng.random() < 0.22:
            col = mix(col, [152, 132, 84], 0.18)
        L = rng.uniform(10, 52)
        ang = math.atan2(ty, tx) + rng.uniform(-0.05, 0.05)
        S("midforest",
          [(x - math.cos(ang) * L * 0.5, y - math.sin(ang) * L * 0.5),
           (x + math.cos(ang) * L * 0.5, y + math.sin(ang) * L * 0.5)],
          rng.uniform(2.4, 9.0), vary(col, 4),
          rng.uniform(0.09, 0.31),
          rng.choice(("dry", "bristle", "chalk")),
          rng.uniform(0.34, 0.72))
    # roots
    basex, basey = pts[-1]
    for _ in range(5):
        side = -1 if rng.random() < 0.5 else 1
        root_pts = [(basex + side * rng.uniform(2, width / 3), basey - rng.uniform(58, 110)),
                    (basex + side * rng.uniform(35, 75), basey - rng.uniform(15, 45)),
                    (basex + side * rng.uniform(80, 150), basey + rng.uniform(2, 22))]
        taper("midforest", root_pts, tr["shadow"], rng.uniform(9, 18),
              1.2, steps=12, opacity=0.72, preset="dry", hardness=0.6)

# lichen, moss and small hollows keep the bark from reading as a flat texture
for tr in TRUNKS:
    poly = shape_ops.ribbon(tr["pts"], tr["widths"])
    for _ in range(95):
        x0, y0, x1, y1 = bbox(poly)
        x = rng.uniform(x0, x1)
        y = rng.uniform(y0, y1)
        if not point_in(x, y, poly):
            continue
        size = rng.uniform(1.5, 10.0)
        col = mix([55, 78, 43], [139, 129, 67], rng.uniform(0.05, 0.85))
        L = size * rng.uniform(0.4, 1.6)
        aa = rng.uniform(-1.2, 1.2)
        S("midforest", [(x, y), (x + math.cos(aa) * L, y + math.sin(aa) * L)],
          size, vary(col, 8), rng.uniform(0.10, 0.32),
          rng.choice(("sponge", "chalk", "dry")), rng.uniform(0.30, 0.62))
    # a couple of deep bark hollows
    for _ in range(2):
        x0, y0, x1, y1 = bbox(poly)
        x = rng.uniform(x0, x1)
        y = rng.uniform(y0, y1)
        if not point_in(x, y, poly):
            continue
        dab("midforest", x, y, rng.uniform(5, 16), [25, 33, 29],
            rng.uniform(0.12, 0.32), "round_soft", 0.20)

# big limbs: dark branch webs, with a fine warm top edge on the light side
branches = [
    ([(108, 218), (205, 155), (302, 139), (430, 178)], 34, 3, 0.82),
    ([(92, 390), (178, 330), (285, 322), (372, 360)], 24, 2, 0.70),
    ([(1138, 210), (1048, 166), (967, 148), (886, 170)], 34, 2, 0.82),
    ([(1145, 368), (1060, 306), (1000, 280), (930, 294)], 25, 2, 0.72),
    ([(1035, 160), (968, 100), (918, 79), (854, 85)], 20, 1.5, 0.68),
    ([(410, 228), (452, 184), (498, 173), (548, 190)], 11, 1.2, 0.54),
]
for pts, w0, w1, op in branches:
    # a solid tapered ribbon gives a continuous limb; stamp-by-stamp tapers
    # leave dotted tips when the brush becomes very small
    branch_poly = shape_ops.ribbon(pts, [w0, w1])
    fill_poly("midforest", branch_poly, [33, 39, 33], color2=[65, 62, 44],
              size=max(8.0, min(20.0, w0 * 0.56)),
              spacing=max(4.5, w0 * 0.18), opacity=op * 0.98,
              preset="bristle", jitter=0.35, angle=0, gradient_angle=0,
              hardness=0.60, edge=True, edge_inset=2.5)
    # one continuous warm ridge along the upper side
    ridge = []
    for i, (x, y) in enumerate(pts):
        u = i / max(1, len(pts) - 1)
        width = w0 * (1 - u) + w1 * u
        p0 = pts[max(0, i - 1)]
        p1 = pts[min(len(pts) - 1, i + 1)]
        tx, ty = p1[0] - p0[0], p1[1] - p0[1]
        ln = max(1e-6, math.hypot(tx, ty))
        nx, ny = -ty / ln, tx / ln
        if ny > 0:
            nx, ny = -nx, -ny
        ridge.append((x + nx * width * 0.23, y + ny * width * 0.23))
    S("midforest", ridge, max(2.0, w0 * 0.13), [105, 96, 64],
      op * 0.34, "round_soft", 0.35)
    # irregular twigs: a few continuous, curving strokes rather than dotted chains
    for _ in range(6):
        t = rng.uniform(0.25, 0.92)
        x, y, tx, ty = sample_line(pts, t)
        nx, ny = -ty, tx
        side = rng.choice([-1, 1])
        length = rng.uniform(22, 72) * (1.0 - 0.5 * t)
        twig = [(x, y),
                (x + nx * side * length * 0.45 + tx * length * 0.20,
                 y + ny * side * length * 0.45 + ty * length * 0.20),
                (x + nx * side * length * 0.82 + tx * length * 0.45,
                 y + ny * side * length * 0.82 + ty * length * 0.45)]
        S("midforest", twig, rng.uniform(1.0, 3.2), [37, 44, 35],
          rng.uniform(0.22, 0.48), "round_soft", 0.42)
    # a dark crease and one or two bark scratches along the limb
    for _ in range(2):
        t = rng.uniform(0.10, 0.85)
        x, y, tx, ty = sample_line(pts, t)
        nx, ny = -ty, tx
        side = -1 if rng.random() < 0.5 else 1
        L = rng.uniform(8, 22)
        S("midforest", [(x + nx * side * w0 * 0.18, y + ny * side * w0 * 0.18),
                        (x + tx * L + nx * side * w0 * 0.20,
                         y + ty * L + ny * side * w0 * 0.20)],
          rng.uniform(0.8, 1.8), [24, 32, 28],
          rng.uniform(0.12, 0.28), "liner", 0.35)

# canopy masses at the top and upper corners; broken edges, not cartoon triangles
canopy_centers = [
    (20, 85, 245, 150, 260), (175, 70, 210, 135, 240),
    (345, 52, 185, 125, 175), (470, 82, 150, 105, 85),
    (735, 58, 170, 120, 95), (860, 53, 190, 135, 120),
    (1000, 54, 220, 150, 245), (1140, 72, 245, 165, 285),
    (1160, 230, 190, 190, 190), (88, 250, 185, 190, 205),
    (980, 235, 150, 150, 145),
]
for ci, (cx, cy, rx, ry, count) in enumerate(canopy_centers):
    # a soft dark underpainting binds each leaf cluster together
    for _ in range(38):
        ang = rng.uniform(0, math.tau)
        rr = math.sqrt(rng.random()) * 0.82
        x = cx + math.cos(ang) * rx * rr
        y = cy + math.sin(ang) * ry * rr
        dab("canopy", x, y, rng.uniform(88, 205),
            vary([22, 35, 28], 4), rng.uniform(0.018, 0.050),
            "round_soft", 0.045)
    for _ in range(62):
        ang = rng.uniform(0, math.tau)
        rr = math.sqrt(rng.random()) * 0.90
        x = cx + math.cos(ang) * rx * rr
        y = cy + math.sin(ang) * ry * rr
        dab("canopy", x, y, rng.uniform(42, 118),
            vary([23, 36, 29], 5), rng.uniform(0.022, 0.062),
            "round_soft", 0.06)
    for _ in range(int(count * 1.70)):
        ang = rng.uniform(0, math.tau)
        rr = math.sqrt(rng.random())
        # clusters are slightly scalloped
        scal = 0.70 + 0.30 * abs(math.sin(ang * 3.0 + ci))
        x = cx + math.cos(ang) * rx * rr * scal
        y = cy + math.sin(ang) * ry * rr * scal
        light = max(0.0, min(1.0, 0.20 + 0.55 * (x - cx) / max(1, rx) +
                              0.25 * (cy - y) / max(1, ry)))
        col = mix([22, 34, 29], [65, 76, 50], light * 0.75 + rng.uniform(0, 0.18))
        size = rng.uniform(2.4, 11.0)
        ang2 = math.pi * 0.5 + rng.uniform(-1.15, 0.95)
        L = size * rng.uniform(1.0, 3.0)
        S("canopy",
          [(x - math.cos(ang2) * L * 0.35, y - math.sin(ang2) * L * 0.35),
           (x + math.cos(ang2) * L * 0.65, y + math.sin(ang2) * L * 0.65)],
          size, vary(col, 5), rng.uniform(0.12, 0.46),
          rng.choice(("dry", "liner", "chalk", "dry", "sponge")),
          rng.uniform(0.22, 0.62))
    # warm broken edge on the sunward side of each mass
    for _ in range(max(7, count // 14)):
        ang = rng.uniform(-0.9, 0.8)
        rr = rng.uniform(0.58, 1.02)
        x = cx + math.cos(ang) * rx * rr
        y = cy + math.sin(ang) * ry * rr
        S("canopy", [(x - 8, y + rng.uniform(-2, 5)),
                      (x + 8, y + rng.uniform(-8, 1))],
          rng.uniform(2, 8), vary([94, 101, 61], 8),
          rng.uniform(0.08, 0.28), "dry", 0.4)

# a dark leafy roof ties the separate clusters together along the top edge
for _ in range(230):
    x = rng.uniform(-40, W + 40)
    y = max(-40, min(160, rng.gauss(48, 56)))
    size = rng.uniform(60, 190)
    S("canopy", [(x - rng.uniform(8, 45), y + rng.uniform(-5, 8)),
                  (x + rng.uniform(8, 45), y + rng.uniform(-7, 5))],
      size, vary(mix([20, 33, 28], [46, 58, 40], rng.uniform(0, 0.55)), 5),
      rng.uniform(0.012, 0.045), "round_soft", 0.05)

# leaf-scale accents: fine pointed dabs along the hanging edges
for ci, (cx, cy, rx, ry, count) in enumerate(canopy_centers):
    edge_n = max(28, count // 3)
    for _ in range(edge_n):
        ang = rng.uniform(0.30, 1.35) if cy < 300 else rng.uniform(-1.2, 1.1)
        rr = rng.uniform(0.55, 1.04)
        x = cx + math.cos(ang) * rx * rr
        y = cy + math.sin(ang) * ry * rr
        # leaves are short, pointed and mostly angled downward
        aa = math.pi * 0.5 + rng.uniform(-0.8, 0.8)
        L = rng.uniform(3, 11)
        lit = 0.15 + 0.55 * max(0.0, (x - cx) / max(1.0, rx))
        col = mix([30, 47, 34], [103, 106, 63], lit + rng.uniform(-0.15, 0.15))
        S("canopy", [(x - math.cos(aa) * L * 0.20,
                      y - math.sin(aa) * L * 0.20),
                     (x + math.cos(aa) * L * 0.80,
                      y + math.sin(aa) * L * 0.80)],
          rng.uniform(1.3, 4.2), vary(col, 7), rng.uniform(0.08, 0.30),
          "liner", rng.uniform(0.28, 0.52))

# ---------------------------------------------------------------------------
# 5. god rays behind the ground and animal
# ---------------------------------------------------------------------------
# a second, stronger dawn pool sits over the canopy edge and behind the fox
radial_glow("godrays", 835, 392, 510, 325, 430,
            [255, 238, 198], [212, 198, 169], 70, 258, 0.013, 0.0035)
radial_glow("godrays", 820, 310, 300, 215, 170,
            [255, 229, 177], [238, 200, 151], 32, 150, 0.011, 0.004)
# a diffuse sun core, not a hard disc
radial_glow("godrays", 852, 306, 62, 50, 95,
            [255, 243, 211], [255, 218, 166], 6, 30, 0.019, 0.004)
for _ in range(16):
    ang = rng.uniform(0, math.tau)
    rr = rng.uniform(0, 34)
    dab("godrays", 852 + math.cos(ang) * rr,
        306 + math.sin(ang) * rr * 0.7, rng.uniform(3, 16),
        [255, 245, 218], rng.uniform(0.020, 0.060), "round_soft", 0.05)
# warm amber air in the opening
radial_glow("godrays", 822, 414, 440, 285, 240,
            [255, 205, 135], [242, 175, 111], 65, 220, 0.0060, 0.0010)
# a low warm halo separates the fox silhouette from the mist
radial_glow("godrays", 770, 510, 330, 205, 180,
            [255, 219, 170], [255, 187, 128], 42, 150, 0.0045, 0.0008)
radial_glow("godrays", 852, 306, 165, 118, 210,
            [255, 220, 162], [255, 174, 104], 16, 85, 0.011, 0.0015)
for _ in range(28):
    ang = rng.uniform(0, math.tau)
    rr = math.sqrt(rng.random()) * 56
    dab("godrays", 852 + math.cos(ang) * rr,
        306 + math.sin(ang) * rr * 0.7, rng.uniform(7, 34),
        [255, 228, 177], rng.uniform(0.012, 0.042), "round_soft", 0.04)
# five readable hero rays, still soft-edged
for _ in range(5):
    x0 = rng.uniform(760, 930)
    y0 = rng.uniform(245, 340)
    x1 = x0 - rng.uniform(260, 430)
    y1 = rng.uniform(560, 735)
    pts = [(x0, y0), ((x0 + x1) / 2 + rng.uniform(-30, 30),
                      (y0 + y1) / 2 + rng.uniform(-20, 20)), (x1, y1)]
    S("godrays", pts, rng.uniform(45, 95), [255, 208, 142],
      rng.uniform(0.038, 0.068), "round_soft", 0.035)
    for _ in range(12):
        t = rng.uniform(0, 1)
        x, y, _, _ = sample_line(pts, t)
        dab("godrays", x + rng.uniform(-18, 18), y + rng.uniform(-10, 10),
            rng.uniform(6, 26), [255, 222, 163],
            rng.uniform(0.006, 0.024), "round_soft", 0.05)

for i in range(15):
    y0 = rng.uniform(200, 365)
    x0 = rng.uniform(720, 1050)
    x1 = x0 - rng.uniform(260, 620)
    y1 = rng.uniform(560, 765)
    pts = [(x0, y0), ((x0 + x1) / 2 + rng.uniform(-40, 40),
                      (y0 + y1) / 2 + rng.uniform(-30, 30)), (x1, y1)]
    op = rng.uniform(0.016, 0.048)
    S("godrays", pts, rng.uniform(24, 105), [255, 219, 158], op,
      "round_soft", 0.04)
    for _ in range(20):
        t = rng.uniform(0, 1)
        x, y, _, _ = sample_line(pts, t)
        x += rng.uniform(-40, 40)
        y += rng.uniform(-20, 20)
        dab("godrays", x, y, rng.uniform(12, 58), [255, 226, 171],
            op * rng.uniform(0.20, 0.65), "round_soft", 0.04)

# ---------------------------------------------------------------------------
# 6. forest floor
# ---------------------------------------------------------------------------
ground_top = [(0, 584), (120, 592), (250, 604), (390, 616),
              (520, 632), (650, 639), (790, 626), (930, 608),
              (1060, 596), (1200, 585)]
ground_poly = ground_top + [(1200, 940), (0, 940)]
fill_poly("ground", ground_poly, [27, 42, 34], color2=[57, 60, 38],
          size=52, spacing=22, opacity=1.0, preset="bristle",
          jitter=0.55, angle=4, gradient_angle=90, hardness=0.72,
          edge=False)

# broad wet-earth and moss bands
for y in range(590, 910, 22):
    for k in range(11):
        x = rng.uniform(-20, W + 20)
        L = rng.uniform(60, 250)
        yy = y + rng.uniform(-14, 14)
        col = mix([30, 44, 34], [77, 77, 46], rng.uniform(0.0, 0.75))
        if rng.random() < 0.20:
            col = mix(col, [91, 72, 44], 0.5)
        S("ground", [(x - L / 2, yy + rng.uniform(-5, 5)),
                     (x + L / 2, yy + rng.uniform(-5, 5))],
          rng.uniform(25, 68), col, rng.uniform(0.12, 0.42),
          "round_soft" if rng.random() < 0.6 else "bristle",
          rng.uniform(0.25, 0.7))

for _ in range(560):
    x = rng.uniform(-30, W + 30)
    y = rng.uniform(592, 900)
    light = (y - 592) / 308.0
    col = mix([35, 49, 35], [62, 58, 39], rng.uniform(0, 0.55))
    if rng.random() < 0.18:
        col = mix(col, [99, 80, 48], 0.6)
    size = rng.uniform(3, 14)
    ang = rng.uniform(-0.25, 0.25)
    L = size * rng.uniform(0.8, 3.2)
    S("ground", [(x - math.cos(ang) * L / 2, y - math.sin(ang) * L / 2),
                  (x + math.cos(ang) * L / 2, y + math.sin(ang) * L / 2)],
      size, col, rng.uniform(0.10, 0.32),
      rng.choice(("dry", "sponge", "bristle")), rng.uniform(0.25, 0.65))

# mossy patches left and right of the boulder
left_moss = [(0, 620), (95, 606), (210, 615), (310, 634), (355, 680),
             (300, 735), (170, 754), (40, 742), (0, 710)]
right_moss = [(900, 612), (1000, 598), (1125, 600), (1200, 618),
              (1200, 738), (1090, 760), (980, 738), (910, 690)]
for poly, col in [(left_moss, [49, 69, 40]), (right_moss, [55, 73, 41])]:
    # a low-opacity stain, broken by hundreds of tufts, rather than a green cut-out
    fill_poly("ground_detail", poly, mix(col, [28, 44, 33], 0.52),
              color2=mix(col, [101, 100, 56], 0.30), size=34, spacing=17,
              opacity=0.18, preset="sponge", jitter=0.72, angle=20,
              gradient_angle=0, hardness=0.27, edge=False)
    scatter_fill("ground_detail", poly, 330, (2.5, 12.0),
                 lambda x, y, c=col: vary(mix(c, [139, 137, 66],
                                             rng.uniform(0, 0.55)), 9),
                 opacity_range=(0.09, 0.43),
                 preset_choices=("sponge", "dry", "chalk"),
                 angle_fn=lambda x, y, rng=None: -1.55 + rng.uniform(-0.5, 0.5),
                 len_factor=(0.30, 1.2))
    # scrubby dark edge bites into the moss to keep the silhouette loose
    for _ in range(170):
        x = rng.uniform(*[min(p[0] for p in poly), max(p[0] for p in poly)])
        y = rng.uniform(*[min(p[1] for p in poly), max(p[1] for p in poly)])
        if not point_in(x, y, poly):
            continue
        dab("ground_detail", x, y, rng.uniform(5, 24),
            vary(rng.choice([[31, 47, 34], [47, 58, 37], [72, 64, 42]]), 7),
            rng.uniform(0.05, 0.22), "round_soft", 0.22)

# warm leaf litter woven across the lower clearing
for cx, cy, rx, ry, n in [(260, 790, 260, 100, 220),
                           (940, 790, 260, 120, 220)]:
    for _ in range(n):
        ang = rng.uniform(0, math.tau)
        rr = math.sqrt(rng.random())
        x = cx + math.cos(ang) * rx * rr
        y = cy + math.sin(ang) * ry * rr
        L = rng.uniform(2, 11)
        aa = rng.uniform(-0.5, 0.5)
        S("ground_detail", [(x, y),
                            (x + math.cos(aa) * L, y + math.sin(aa) * L)],
          rng.uniform(1.0, 3.6), vary([88, 69, 41], 16),
          rng.uniform(0.10, 0.44), "dry", rng.uniform(0.28, 0.65))

# roots at the bases of the big trunks
for bx, by, side, scale in [(95, 700, -1, 1.15), (280, 625, 1, 0.75),
                            (1110, 690, 1, 1.0), (1030, 620, -1, 0.70)]:
    for _ in range(5):
        s = side * rng.choice([-1, 1])
        pts = [(bx + rng.uniform(-10, 10), by - rng.uniform(48, 88)),
               (bx + s * rng.uniform(32, 72) * scale, by - rng.uniform(12, 32)),
               (bx + s * rng.uniform(82, 160) * scale, by + rng.uniform(4, 30))]
        taper("ground_detail", pts, [38, 44, 35], rng.uniform(5, 11) * scale,
              0.8, steps=14, opacity=0.42, preset="round_soft", hardness=0.48)

# a few dark stones and leaf-litter flecks
for cx, cy, rx, ry, col in [(155, 706, 44, 17, [50, 57, 47]),
                            (1005, 690, 48, 18, [57, 61, 48]),
                            (875, 740, 34, 13, [47, 55, 44])]:
    poly = [(cx - rx, cy + ry * 0.3), (cx - rx * 0.55, cy - ry),
            (cx + rx * 0.35, cy - ry * 0.75), (cx + rx, cy + ry * 0.15),
            (cx + rx * 0.4, cy + ry), (cx - rx * 0.5, cy + ry * 0.8)]
    fill_poly("ground_detail", poly, mix(col, [20, 30, 26], 0.25),
              color2=mix(col, [97, 90, 63], 0.35), size=10, spacing=5,
              opacity=0.9, preset="flat", jitter=0.4, angle=5,
              gradient_angle=0, hardness=0.7, edge=True, edge_inset=2)
for _ in range(300):
    x = rng.uniform(20, W - 20)
    y = rng.uniform(620, 890)
    if 300 < x < 940 and y < 760:
        continue
    col = vary(rng.choice([[86, 69, 42], [108, 87, 47], [50, 67, 40]]), 10)
    ang = rng.uniform(-1.6, 1.6)
    L = rng.uniform(2, 10)
    S("ground_detail", [(x, y), (x + math.cos(ang) * L, y + math.sin(ang) * L)],
      rng.uniform(1.0, 3.2), col, rng.uniform(0.18, 0.58),
      "dry", rng.uniform(0.3, 0.7))

# tiny background ferns and grass silhouettes before the boulder
def fern(layer, base, tip, bend, scale, dark, light, leaflets=16,
         opacity=0.84):
    bx, by = base
    tx, ty = tip
    mx, my = (bx + tx) / 2, (by + ty) / 2
    dx, dy = tx - bx, ty - by
    ln = max(1.0, math.hypot(dx, dy))
    nx, ny = -dy / ln, dx / ln
    ctrl = (mx + nx * bend, my + ny * bend)
    stem = []
    for i in range(26):
        t = i / 25.0
        x = (1 - t) ** 2 * bx + 2 * (1 - t) * t * ctrl[0] + t ** 2 * tx
        y = (1 - t) ** 2 * by + 2 * (1 - t) * t * ctrl[1] + t ** 2 * ty
        stem.append((x, y))
    taper(layer, stem, dark, 6.5 * scale, 0.8 * scale, steps=24,
          opacity=opacity * 0.95, preset="round_soft", hardness=0.55)
    for i in range(leaflets):
        t = 0.10 + 0.84 * i / max(1, leaflets - 1)
        x, y, txv, tyv = sample_line(stem, t)
        ln2 = max(1.0, math.hypot(txv, tyv))
        nx2, ny2 = -tyv / ln2, txv / ln2
        side = 1 if i % 2 == 0 else -1
        reach = 25 * scale * (math.sin(math.pi * min(1.0, t * 1.05)) ** 0.70)
        reach *= rng.uniform(0.72, 1.06)
        p0 = (x, y)
        p1 = (x + nx2 * side * reach * 0.55 + txv * reach * 0.20,
              y + ny2 * side * reach * 0.55 + tyv * reach * 0.20)
        p2 = (x + nx2 * side * reach + txv * reach * 0.45,
              y + ny2 * side * reach + tyv * reach * 0.45)
        c = light if side > 0 and rng.random() < 0.35 else dark
        w = max(1.0, 3.15 * scale * (0.38 + math.sin(math.pi * t) * 0.78))
        op = opacity * rng.uniform(0.60, 0.95)
        # broad, leaf-shaped pinnae in three overlapping planes
        blade_tone = mix(dark, light, 0.40 if scale > 0.9 else 0.18)
        S(layer, [p0, p1, p2], w * 1.26,
          vary(blade_tone, 6), op * 0.94,
          "round_soft", 0.48)
        S(layer, [p0, p1, p2], w * 0.72,
          vary(mix(blade_tone, light, 0.28), 5), op * 0.82,
          "round_soft", 0.42)
        lx, ly = p2[0] - p0[0], p2[1] - p0[1]
        ll = max(1e-6, math.hypot(lx, ly))
        npx, npy = -ly / ll, lx / ll
        off = w * 0.34
        q0 = (p0[0] + npx * off, p0[1] + npy * off)
        q1 = (p1[0] + npx * off, p1[1] + npy * off)
        r0 = (p0[0] - npx * off, p0[1] - npy * off)
        r1 = (p1[0] - npx * off, p1[1] - npy * off)
        tone = light if (side * nx2) > -0.1 else mix(dark, light, 0.42)
        S(layer, [q0, q1, p2], w * 0.50, vary(tone, 7),
          op * 0.76, "round_soft", 0.42)
        S(layer, [r0, r1, p2], w * 0.44,
          vary(mix(dark, light, 0.20), 5), op * 0.62,
          "dry", 0.38)
        # a fine cream-green backlit edge on the larger foreground fronds
        if scale > 0.95:
            S(layer, [q0, q1, p2], max(0.7, w * 0.15),
              vary([113, 111, 59], 8), op * 0.52,
              "liner", 0.35)
        # two notches make the pinna edge feathery rather than a simple blade
        for notch in (0.42, 0.68):
            vx = p0[0] + lx * notch + npx * w * 0.52
            vy = p0[1] + ly * notch + npy * w * 0.52
            S(layer, [(vx - lx * 0.05, vy - ly * 0.05),
                      (vx + npx * w * 0.22, vy + npy * w * 0.22)],
              max(0.7, w * 0.18), vary(dark, 5), op * 0.50,
              "liner", 0.40)

for base, tip, bend, sc in [((180, 725), (315, 590), -28, 0.64),
                            ((275, 702), (352, 594), 16, 0.50),
                            ((855, 708), (775, 578), 24, 0.56),
                            ((990, 728), (1084, 592), -22, 0.60)]:
    fern("ground_detail", base, tip, bend, sc, [35, 55, 38], [69, 83, 47],
         leaflets=24, opacity=0.58)

# separate low pockets of ground fog, fading out before the foreground
for cx, cy, rx, ry, n, col0, col1, s0, s1, op0 in [
    (120, 648, 230, 58, 75, [211, 205, 184], [190, 195, 179], 20, 72, 0.0060),
    (520, 684, 250, 55, 80, [218, 207, 183], [195, 197, 180], 18, 68, 0.0050),
    (870, 635, 245, 58, 80, [220, 208, 183], [195, 197, 180], 18, 68, 0.0050),
    (1110, 690, 210, 62, 70, [210, 205, 183], [189, 194, 179], 18, 64, 0.0055),
]:
    fog_cloud("floor_mist", cx, cy, rx, ry, n, col0, col1,
              s0, s1, op0, 0.0002, 0.03)

# ---------------------------------------------------------------------------
# 7. the mossy boulder
# ---------------------------------------------------------------------------
BOULDER = [(330, 900), (348, 848), (373, 798), (419, 757), (477, 728),
           (548, 711), (616, 701), (689, 699), (760, 707), (820, 727),
           (868, 757), (903, 806), (920, 900)]
fill_poly("boulder", BOULDER, [45, 56, 52], color2=[104, 97, 74],
          size=38, spacing=16, opacity=0.98, preset="bristle",
          jitter=0.52, angle=8, gradient_angle=0, hardness=0.72,
          edge=True, edge_inset=4)

# broad planes: cool shadow left, lit top shelf, warm right face, dark front foot
faces = [
    ([(390, 830), (424, 770), (479, 737), (548, 719), (616, 707),
      (694, 706), (758, 715), (808, 735), (756, 772), (656, 793),
      (548, 790), (474, 776), (429, 800)], [69, 78, 65], [145, 137, 97]),
    ([(345, 880), (365, 805), (420, 762), (474, 780), (490, 858),
      (462, 904), (362, 926)], [38, 49, 48], [58, 67, 59]),
    ([(746, 738), (818, 754), (862, 785), (893, 835), (900, 900),
      (784, 916), (753, 832), (734, 785)], [75, 80, 67], [143, 130, 91]),
    ([(480, 790), (556, 796), (653, 800), (747, 784), (765, 846),
      (800, 900), (462, 908), (448, 842)], [42, 52, 47], [70, 74, 58]),
]
for poly, c0, c1 in faces:
    fill_poly("boulder", poly, c0, color2=c1, size=28, spacing=12,
              opacity=0.73, preset="bristle", jitter=0.58, angle=12,
              gradient_angle=0, hardness=0.60, edge=False)

# a stronger sunward plane and a cool shadow plane carve the rock volume
fill_poly("boulder", [(746, 748), (815, 765), (861, 796), (892, 842),
                      (900, 900), (804, 900), (765, 832), (737, 785)],
          [98, 96, 74], color2=[186, 159, 107], size=25, spacing=10,
          opacity=0.56, preset="bristle", jitter=0.55, angle=0,
          gradient_angle=0, hardness=0.52, edge=False)
fill_poly("boulder", [(350, 860), (370, 798), (421, 763), (473, 788),
                      (482, 866), (452, 900), (348, 900)],
          [27, 39, 41], color2=[51, 60, 54], size=25, spacing=11,
          opacity=0.52, preset="bristle", jitter=0.50, angle=80,
          gradient_angle=90, hardness=0.46, edge=False)
# broad directional strokes carve the sunlit and shadowed faces
for _ in range(120):
    x = rng.uniform(742, 888)
    y = rng.uniform(760, 900)
    if not point_in(x, y, BOULDER):
        continue
    L = rng.uniform(2, 10)
    S("boulder_detail", [(x, y), (x + L * 0.7, y + L)],
      rng.uniform(5, 19), vary(mix([122, 112, 82], [196, 166, 112],
                                   rng.uniform(0.1, 0.8)), 8),
      rng.uniform(0.05, 0.20), "dry", 0.35)
for _ in range(90):
    x = rng.uniform(350, 480)
    y = rng.uniform(776, 900)
    if not point_in(x, y, BOULDER):
        continue
    L = rng.uniform(2, 9)
    S("boulder_detail", [(x, y), (x + L * 0.3, y + L)],
      rng.uniform(4, 16), vary(mix([24, 37, 40], [58, 66, 57],
                                   rng.uniform(0.1, 0.7)), 6),
      rng.uniform(0.05, 0.18), "dry", 0.35)
# continuous lip shadow underneath the moss shelf
taper("boulder_detail", [(425, 770), (500, 744), (590, 730), (670, 731),
                          (745, 746)], [29, 40, 39], 11, 2.5, steps=34,
      opacity=0.38, preset="dry", hardness=0.40)

# stone grain and chipped facets
for _ in range(720):
    x = rng.uniform(360, 910)
    y = rng.uniform(713, 900)
    if not point_in(x, y, BOULDER):
        continue
    lit = max(0.0, min(1.0, (x - 360) / 560.0 * 0.55 +
                       (730 - y) / 200.0 * 0.30 + rng.uniform(-0.20, 0.20)))
    col = mix([39, 50, 48], [149, 136, 96], lit)
    if rng.random() < 0.16:
        col = mix(col, [84, 101, 58], 0.45)
    size = rng.uniform(2, 18)
    ang = rng.uniform(-0.8, 0.8) + (rng.choice([0, 0.6, -0.6]))
    L = size * rng.uniform(0.3, 1.8)
    S("boulder", [(x - math.cos(ang) * L / 2, y - math.sin(ang) * L / 2),
                  (x + math.cos(ang) * L / 2, y + math.sin(ang) * L / 2)],
      size, vary(col, 4), rng.uniform(0.07, 0.35),
      rng.choice(("dry", "bristle", "flat", "sponge")),
      rng.uniform(0.35, 0.85))

# cracks and rim highlights on the rock
cracks = [
    [(428, 780), (459, 810), (451, 849), (441, 888)],
    [(600, 714), (610, 754), (596, 786), (616, 824)],
    [(784, 744), (768, 790), (789, 822), (776, 860)],
    [(527, 770), (548, 803), (534, 846), (550, 873)],
    [(681, 786), (694, 823), (682, 865)],
    [(835, 770), (858, 803), (880, 850)],
]
for pts in cracks:
    taper("boulder_detail", pts, [30, 38, 34], 5.5, 1.0, steps=18,
          opacity=0.48, preset="liner", hardness=0.65)
    ridge = [(x + 3, y - 4) for x, y in pts]
    taper("boulder_detail", ridge, [175, 151, 101], 1.7, 0.5, steps=16,
          opacity=0.24, preset="liner", hardness=0.55)
# warm top-right rim where dawn catches the stone
for pts, w, op in [
    ([(690, 704), (760, 716), (820, 741), (865, 773), (890, 814)], 7.0, 0.50),
    ([(472, 742), (525, 720), (590, 708), (658, 704)], 5.0, 0.34),
    ([(348, 845), (365, 804), (385, 778)], 3.5, 0.28),
]:
    S("boulder_detail", pts, w, [212, 178, 116], op,
      "round_soft", 0.34)
    S("boulder_detail", [(x + 3, y + 2) for x, y in pts], max(1.0, w * 0.35),
      [255, 214, 147], op * 0.55, "liner", 0.3)
# cool lip shadow under the top shelf
for x in range(410, 880, 18):
    yy = 748 + (x - 550) * 0.035
    if not point_in(x, yy, BOULDER):
        continue
    S("boulder_detail", [(x, yy), (x + rng.uniform(4, 18), yy + rng.uniform(1, 8))],
      rng.uniform(2, 7), [31, 43, 43], rng.uniform(0.10, 0.25),
      "dry", 0.45)

# moss cap and side pads: base, then tuft structure and golden lit tips
MOSS_TOP = [(444, 735), (474, 716), (524, 703), (584, 697), (649, 697),
            (708, 700), (762, 711), (801, 728), (775, 742), (730, 746),
            (685, 739), (636, 746), (585, 742), (533, 749), (482, 746)]
MOSS_LEFT = [(365, 810), (397, 778), (438, 766), (465, 792), (449, 837),
             (419, 858), (382, 846)]
MOSS_RIGHT = [(804, 743), (848, 764), (879, 800), (858, 833), (820, 818),
              (788, 782)]
for poly, shift in [(MOSS_TOP, 0), (MOSS_LEFT, 0), (MOSS_RIGHT, 0)]:
    fill_poly("moss", poly, [37, 56, 34], color2=[101, 102, 55],
              size=14, spacing=6.8, opacity=0.92, preset="sponge",
              jitter=0.74, angle=90, gradient_angle=0, hardness=0.42,
              edge=True, edge_inset=2)
    scatter_fill("moss", poly, 520, (2.2, 11.5),
                 lambda x, y: vary(mix([38, 62, 34], [158, 142, 68],
                                       max(0.0, min(1.0, (x - 420) / 390 +
                                           rng.uniform(-0.30, 0.30)))), 10),
                 opacity_range=(0.13, 0.56),
                 preset_choices=("sponge", "dry", "chalk"),
                 hardness_range=(0.24, 0.58),
                 angle_fn=lambda x, y, rng=None: -1.48 + rng.uniform(-0.48, 0.48),
                 len_factor=(0.25, 1.35))
    # dark clump shadows and sun-gilt tips break up the green blanket
    for _ in range(135):
        x0, y0, x1, y1 = bbox(poly)
        x = rng.uniform(x0, x1)
        y = rng.uniform(y0, y1)
        if not point_in(x, y, poly):
            continue
        dab("moss", x, y, rng.uniform(2.0, 10.0), vary([24, 41, 27], 7),
            rng.uniform(0.08, 0.34), "round_soft", 0.22)
    for _ in range(90):
        x0, y0, x1, y1 = bbox(poly)
        x = rng.uniform(x0, x1)
        y = rng.uniform(y0, y1)
        if not point_in(x, y, poly):
            continue
        L = rng.uniform(2, 9)
        S("moss", [(x, y), (x + rng.uniform(-2, 5), y - L)],
          rng.uniform(0.8, 2.8), vary([160, 144, 69], 10),
          rng.uniform(0.10, 0.38), "liner", 0.40)

# little upright moss filaments along the cap edge
for x in range(455, 795, 4):
    if not point_in(x, 718, MOSS_TOP):
        continue
    for _ in range(2):
        x0 = x + rng.uniform(-4, 4)
        y0 = 720 + rng.uniform(-5, 8)
        L = rng.uniform(3, 12)
        S("moss", [(x0, y0), (x0 + rng.uniform(-5, 5), y0 - L)],
          rng.uniform(0.8, 2.6), vary([92, 107, 49], 12),
          rng.uniform(0.22, 0.65), "liner", 0.45)
# pale lichen blooms and small crusts on the stone
for _ in range(130):
    x = rng.uniform(400, 880)
    y = rng.uniform(748, 858)
    if not point_in(x, y, BOULDER):
        continue
    size = rng.uniform(1.0, 8.0)
    col = vary(rng.choice([[137, 145, 103], [161, 158, 111],
                           [104, 122, 88], [185, 173, 123]]), 8)
    L = size * rng.uniform(0.3, 1.4)
    aa = rng.uniform(-1.2, 1.2)
    S("boulder_detail", [(x, y), (x + math.cos(aa) * L, y + math.sin(aa) * L)],
      size, col, rng.uniform(0.06, 0.24),
      rng.choice(("chalk", "sponge", "dry")), 0.30)

# two tiny mushrooms growing from the shadowed edge
for cx, cy, sc in [(388, 812, 0.90), (406, 826, 0.68)]:
    taper("boulder_detail", [(cx, cy), (cx - 1, cy - 12 * sc)],
          [211, 193, 151], 4.0 * sc, 2.3 * sc, steps=6,
          opacity=0.78, preset="round_soft", hardness=0.5)
    cap = [(cx - 10 * sc, cy - 12 * sc), (cx - 7 * sc, cy - 17 * sc),
           (cx + 1 * sc, cy - 19 * sc), (cx + 9 * sc, cy - 15 * sc),
           (cx + 11 * sc, cy - 10 * sc), (cx + 3 * sc, cy - 9 * sc),
           (cx - 5 * sc, cy - 10 * sc)]
    fill_poly("boulder_detail", cap, [139, 82, 45], color2=[189, 127, 64],
              size=3.0, spacing=1.5, opacity=0.95, preset="round_hard",
              jitter=0.3, angle=0, gradient_angle=0, hardness=0.6,
              edge=True, edge_inset=0.8)

# small fern and grass tufts rooted in the moss cap
fern("moss", (466, 728), (503, 646), -9, 0.34,
     [41, 63, 36], [101, 102, 53], leaflets=22, opacity=0.68)
fern("moss", (842, 744), (861, 676), 8, 0.29,
     [43, 64, 37], [96, 99, 53], leaflets=18, opacity=0.60)
for bx, by, h in [(486, 720, 24), (500, 716, 18), (827, 741, 20),
                   (853, 741, 26), (865, 746, 17)]:
    S("moss", [(bx, by), (bx + rng.uniform(-6, 6), by - h)],
      rng.uniform(1.0, 2.4), vary([84, 104, 50], 8),
      rng.uniform(0.20, 0.50), "liner", 0.40)

# soft contact shadows and a cast shadow on the moss, above the rock but below the fox
for _ in range(52):
    x = rng.gauss(664, 52)
    y = rng.gauss(710, 7)
    dab("moss", x, y, rng.uniform(12, 44), vary([29, 42, 27], 5),
        rng.uniform(0.05, 0.18), "round_soft", 0.12)
for x, y, w in [(568, 703, 23), (592, 706, 28), (711, 704, 23), (731, 707, 30)]:
    for _ in range(10):
        dab("moss", x + rng.uniform(-w * 0.7, w * 0.3),
            y + rng.uniform(-1, 5), rng.uniform(5, 14),
            vary([25, 36, 24], 4), rng.uniform(0.08, 0.23),
            "round_soft", 0.12)
# a sliver of warm reflected light touches the moss at the right paw
for _ in range(16):
    x = rng.uniform(733, 780)
    y = rng.uniform(700, 716)
    dab("moss", x, y, rng.uniform(2, 7), vary([150, 140, 72], 8),
        rng.uniform(0.06, 0.22), "dry", 0.30)

# ---------------------------------------------------------------------------
# 8. fox: dark under-painting first, then coat, detail and rim light
# ---------------------------------------------------------------------------
TORSO = [(558, 592), (574, 571), (602, 556), (636, 547), (672, 544),
         (702, 548), (726, 558), (740, 575), (741, 596), (734, 612),
         (716, 624), (690, 630), (658, 633), (625, 629), (598, 620),
         (575, 607)]
NECK = [(704, 550), (723, 529), (739, 503), (755, 486), (774, 484),
        (787, 505), (779, 532), (763, 556), (752, 580), (740, 606),
        (723, 625), (706, 616), (698, 590)]
HEAD = [(756, 500), (760, 476), (775, 466), (786, 476), (800, 471),
        (815, 476), (826, 483), (845, 489), (861, 496), (859, 503),
        (844, 510), (832, 515), (814, 521), (800, 532), (782, 539),
        (765, 535), (753, 522), (747, 508)]
MUZZLE = [(820, 489), (847, 493), (866, 499), (874, 505), (871, 513),
          (855, 517), (836, 522), (820, 528), (806, 516), (807, 501)]
CHEEK_CREAM = [(759, 511), (773, 497), (789, 491), (806, 495), (816, 504),
               (828, 508), (822, 516), (807, 524), (795, 538), (781, 531),
               (767, 522)]
CHEST = [(755, 545), (770, 550), (773, 562), (766, 578), (757, 596),
         (748, 614), (739, 626), (731, 629), (728, 617), (734, 600),
         (742, 580), (749, 560)]
BELLY = [(626, 617), (652, 627), (680, 628), (700, 622), (696, 615),
         (675, 618), (653, 617), (632, 611)]
EAR_FAR = [(762, 477), (773, 452), (781, 428), (787, 419), (793, 430),
           (799, 455), (802, 475), (785, 473)]
EAR_NEAR = [(791, 476), (801, 451), (810, 425), (817, 419), (824, 430),
            (831, 456), (835, 480), (816, 474)]
EAR_FAR_INNER = [(771, 469), (780, 442), (786, 430), (791, 435),
                 (793, 456), (794, 469)]
EAR_NEAR_INNER = [(800, 470), (808, 442), (815, 431), (820, 437),
                  (823, 457), (824, 473)]
TAIL_PTS = [(562, 604), (528, 618), (495, 647), (470, 681), (454, 714),
            (439, 735), (419, 739)]
TAIL_W = [38, 61, 78, 79, 66, 44, 16]
TAIL_TIP = [(460, 704), (454, 714), (439, 735), (419, 739)]
TAIL_TIP_W = [42, 34, 22, 8]

HIND_FAR = [(570, 607), (590, 640), (578, 669), (567, 700)]
HIND_NEAR = [(596, 603), (620, 640), (606, 671), (592, 703)]
FRONT_FAR = [(712, 602), (722, 641), (716, 673), (710, 701)]
FRONT_NEAR = [(733, 600), (745, 642), (739, 674), (731, 703)]
LEGS = [
    (HIND_FAR, [27, 19, 10, 6]),
    (HIND_NEAR, [38, 26, 13, 8]),
    (FRONT_FAR, [20, 14, 8, 5]),
    (FRONT_NEAR, [24, 17, 9, 6]),
]

# contact shadows belong to the rock, beneath the fox
for x, y, w, h in [(662, 704, 148, 15), (588, 704, 42, 13),
                   (605, 705, 34, 12), (716, 704, 36, 12),
                   (733, 706, 38, 13), (468, 735, 42, 12)]:
    for _ in range(12):
        dab("boulder_detail", x + rng.uniform(-w / 2, w / 2),
            y + rng.uniform(-h / 2, h / 2), rng.uniform(8, 23),
            [30, 36, 31], rng.uniform(0.06, 0.18), "round_soft", 0.15)

# under-painting: one continuous dark silhouette, with overlaps at joints
under_poly = TORSO + list(reversed(NECK))
fill_poly("fox_under", under_poly, [53, 31, 24], size=22, spacing=8,
          opacity=1.0, preset="round_soft", jitter=0.35, angle=0,
          hardness=0.68, edge=True, edge_inset=3)
fill_poly("fox_under", NECK, [53, 31, 24], size=20, spacing=8,
          opacity=1.0, preset="round_soft", angle=0, hardness=0.68,
          edge=True, edge_inset=2)
fill_poly("fox_under", HEAD, [53, 31, 24], size=16, spacing=7,
          opacity=1.0, preset="round_soft", angle=0, hardness=0.65,
          edge=True, edge_inset=2)
for ear in (EAR_FAR, EAR_NEAR):
    fill_poly("fox_under", ear, [45, 28, 23], size=10, spacing=5,
              opacity=1.0, preset="round_soft", angle=90, hardness=0.66,
              edge=True, edge_inset=1)
ribbon_fill("fox_under", TAIL_PTS, [w + 7 for w in TAIL_W], [53, 31, 24],
            size=26, spacing=9, opacity=1.0, preset="round_soft",
            angle=90, hardness=0.66, edge=True, edge_inset=4)
for pts, wd in LEGS:
    ribbon_fill("fox_under", pts, [w + 5 for w in wd], [53, 31, 24],
                size=14, spacing=6, opacity=1.0, preset="round_soft",
                angle=90, hardness=0.68, edge=True, edge_inset=2)

# coat: tail first so the body overlaps at the hip
ribbon_fill("fox_coat", TAIL_PTS, TAIL_W, [145, 59, 29],
            color2=[206, 101, 43], size=22, spacing=8, opacity=1.0,
            preset="bristle", angle=90, hardness=0.58, edge=True,
            edge_inset=5)
# white tail tip (over the last curve segment)
ribbon_fill("fox_coat", TAIL_TIP, TAIL_TIP_W, [190, 167, 134],
            color2=[224, 202, 170], size=16, spacing=6, opacity=0.90,
            preset="round_soft", angle=90, hardness=0.5, edge=True,
            edge_inset=4)
# dark dorsal wash on the tail
for _ in range(80):
    t = rng.uniform(0.05, 0.66)
    x, y, tx, ty = sample_line(TAIL_PTS, t)
    nx, ny = -ty, tx
    off = -rng.uniform(0.15, 0.95) * 30
    S("fox_coat", [(x + nx * off, y + ny * off),
                    (x + nx * off + tx * rng.uniform(5, 18),
                     y + ny * off + ty * rng.uniform(5, 18))],
      rng.uniform(2, 8), vary([106, 43, 27], 9), rng.uniform(0.12, 0.38),
      "dry", 0.45)

for li, (pts, wd) in enumerate(LEGS):
    far_leg = li in (0, 2)
    # upper leg warm, lower leg nearly black; far legs are cooler and darker
    if far_leg:
        up0, up1 = [112, 49, 29], [165, 77, 35]
        low0, low1 = [47, 42, 36], [33, 31, 28]
    else:
        up0, up1 = [169, 72, 35], [216, 104, 42]
        low0, low1 = [48, 37, 29], [29, 26, 22]
    ribbon_fill("fox_coat", pts[:3], [max(7, w - 1) for w in wd[:3]],
                up0, color2=up1, size=11, spacing=4.5, opacity=1.0,
                preset="bristle", angle=90, hardness=0.52,
                edge=True, edge_inset=2)
    ribbon_fill("fox_coat", pts[2:], [max(4, wd[2] - 1), max(4, wd[3] - 1)],
                low0, color2=low1, size=8, spacing=3.5, opacity=0.98,
                preset="round_soft", angle=90, hardness=0.58,
                edge=True, edge_inset=1)
    # tapered paw, toes pointing right
    fx, fy = pts[-1]
    pw = max(14.0, wd[-1] * 2.2)
    foot = [(fx - pw * 0.52, fy - 3), (fx + pw * 0.08, fy - 4),
            (fx + pw * 0.72, fy - 1), (fx + pw * 0.88, fy + 2),
            (fx + pw * 0.64, fy + 6), (fx - pw * 0.10, fy + 6),
            (fx - pw * 0.52, fy + 3)]
    fill_poly("fox_coat", foot, [61, 50, 40] if far_leg else [56, 43, 33],
              color2=[28, 25, 22], size=4.8, spacing=2.0, opacity=0.98,
              preset="round_soft", angle=0, gradient_angle=0,
              hardness=0.55, edge=True, edge_inset=0.6)
    # a narrow warm ankle band separates coat from the black sock
    ankle = [(fx - 7, fy - 14), (fx + 6, fy - 12)]
    S("fox_detail", ankle, 2.0, [155, 89, 51], 0.26, "dry", 0.4)

# torso coat, with a warm shoulder and a dark rump
fill_poly("fox_coat", TORSO, [129, 56, 30], color2=[201, 95, 38],
          size=20, spacing=7.5, opacity=1.0, preset="bristle",
          jitter=0.55, angle=2, gradient_angle=0, hardness=0.52,
          edge=True, edge_inset=5)
for _ in range(110):
    # warm back / shoulder
    x = rng.uniform(600, 744)
    y = rng.uniform(548, 600)
    if not point_in(x, y, TORSO):
        continue
    L = rng.uniform(5, 24)
    S("fox_coat", [(x - L / 2, y + rng.uniform(-2, 2)),
                    (x + L / 2, y + rng.uniform(-3, 4))],
      rng.uniform(3, 13), vary(mix([194, 91, 39], [232, 141, 65],
                                   rng.uniform(0, 0.8)), 5),
      rng.uniform(0.12, 0.38), "bristle", 0.44)
for _ in range(84):
    # shadow under the barrel and at the rump
    x = rng.uniform(565, 690)
    y = rng.uniform(600, 633)
    if not point_in(x, y, TORSO):
        continue
    S("fox_coat", [(x - rng.uniform(3, 15), y + rng.uniform(-2, 2)),
                    (x + rng.uniform(3, 16), y + rng.uniform(-3, 4))],
      rng.uniform(4, 14), vary([109, 46, 30], 9),
      rng.uniform(0.08, 0.26), "dry", 0.4)

# a separate haunch and shoulder plane over the barrel; volume before outline
HAUNCH = [(574, 590), (601, 586), (622, 599), (628, 618),
          (618, 638), (601, 650), (584, 640), (573, 617)]
SHOULDER = [(697, 555), (723, 558), (738, 574), (734, 597),
            (720, 613), (701, 608), (690, 584)]
fill_poly("fox_coat", HAUNCH, [124, 51, 29], color2=[201, 92, 38],
          size=17, spacing=7, opacity=0.72, preset="bristle",
          jitter=0.48, angle=70, gradient_angle=0, hardness=0.48,
          edge=False)
fill_poly("fox_coat", SHOULDER, [137, 57, 31], color2=[219, 111, 44],
          size=15, spacing=6.5, opacity=0.67, preset="bristle",
          jitter=0.45, angle=72, gradient_angle=0, hardness=0.46,
          edge=False)

# neck and head
fill_poly("fox_coat", NECK, [124, 53, 30], color2=[196, 91, 39],
          size=16, spacing=6, opacity=1.0, preset="bristle",
          angle=80, gradient_angle=90, hardness=0.50,
          edge=True, edge_inset=4)
fill_poly("fox_coat", HEAD, [163, 70, 34], color2=[204, 100, 41],
          size=13, spacing=5.4, opacity=1.0, preset="round_soft",
          jitter=0.48, angle=12, gradient_angle=0, hardness=0.55,
          edge=True, edge_inset=3)
fill_poly("fox_coat", MUZZLE, [180, 88, 44], color2=[222, 135, 67],
          size=10, spacing=4.2, opacity=0.98, preset="round_soft",
          angle=0, gradient_angle=0, hardness=0.48, edge=False,
          edge_inset=1)
fill_poly("fox_coat", CHEEK_CREAM, [168, 139, 108], color2=[219, 193, 157],
          size=10, spacing=4.2, opacity=0.92, preset="round_soft",
          angle=75, gradient_angle=90, hardness=0.45, edge=False,
          edge_inset=1)
fill_poly("fox_coat", CHEST, [154, 125, 97], color2=[216, 189, 153],
          size=10, spacing=4.6, opacity=0.82, preset="round_soft",
          angle=95, gradient_angle=90, hardness=0.42, edge=False,
          edge_inset=1)
fill_poly("fox_coat", BELLY, [138, 114, 88], color2=[183, 158, 127],
          size=8, spacing=5.2, opacity=0.28, preset="round_soft",
          angle=4, gradient_angle=90, hardness=0.34, edge=False,
          edge_inset=1)

# ears: orange outer, black tips and margins, cream inner
for ear, inner in ((EAR_FAR, EAR_FAR_INNER), (EAR_NEAR, EAR_NEAR_INNER)):
    fill_poly("fox_coat", ear, [132, 51, 29], color2=[190, 85, 38],
              size=10, spacing=4.2, opacity=1.0, preset="flat",
              jitter=0.25, angle=90, gradient_angle=90, hardness=0.65,
              edge=True, edge_inset=1)
    fill_poly("fox_coat", inner, [154, 126, 99], color2=[224, 195, 155],
              size=7, spacing=3.0, opacity=0.92, preset="round_soft",
              jitter=0.26, angle=90, gradient_angle=90, hardness=0.46,
              edge=False, edge_inset=1)
# black ear tips
for poly in ([(781, 428), (787, 419), (793, 430), (792, 440), (786, 438)],
             [(810, 425), (817, 419), (824, 430), (822, 442), (815, 438)]):
    fill_poly("fox_detail", poly, [30, 25, 22], size=4.0, spacing=1.8,
              opacity=0.92, preset="flat", jitter=0.2, angle=90,
              hardness=0.65, edge=False, edge_inset=0.5)
# soft dark ear rims, with warm inner fur strokes
for ear, inner in ((EAR_FAR, EAR_FAR_INNER), (EAR_NEAR, EAR_NEAR_INNER)):
    for a, b in zip(ear, ear[1:] + [ear[0]]):
        S("fox_detail", [a, b], 1.3, [38, 29, 24], 0.28, "liner", 0.50)
    scatter_fill("fox_detail", inner, 34, (0.8, 2.8),
                 lambda x, y: vary(mix([174, 145, 111], [229, 201, 162],
                                       rng.uniform(0.1, 0.9)), 6),
                 opacity_range=(0.06, 0.24),
                 preset_choices=("liner", "dry", "round_soft"),
                 angle_fn=lambda x, y, rng=None: 1.35 + rng.uniform(-0.25, 0.25),
                 len_factor=(0.3, 1.0))

# ---------------------------------------------------------------------------
# 9. fur detail
# ---------------------------------------------------------------------------
# body guard-hair passes
for _ in range(350):
    x = rng.uniform(558, 748)
    y = rng.uniform(545, 633)
    if not point_in(x, y, TORSO):
        continue
    u = (x - 560) / 188.0
    v = (y - 545) / 88.0
    shadow = max(0.0, min(1.0, 0.25 + 0.38 * v + 0.08 * (1 - u)))
    base = mix([220, 116, 49], [112, 48, 30], shadow)
    if y < 580:
        base = mix(base, [236, 151, 76], 0.26)
    ang = rng.uniform(-0.16, 0.14) + (0.48 if x > 715 else 0)
    L = rng.uniform(4, 18)
    S("fox_detail", [(x - math.cos(ang) * L / 2, y - math.sin(ang) * L / 2),
                      (x + math.cos(ang) * L / 2, y + math.sin(ang) * L / 2)],
      rng.uniform(1.2, 4.6), vary(base, 8), rng.uniform(0.08, 0.30),
      rng.choice(("dry", "bristle", "liner", "round_soft")),
      rng.uniform(0.30, 0.65))

# broad warm and cool masses model the barrel before the fine fur
for _ in range(105):
    x = rng.uniform(575, 738)
    y = rng.uniform(546, 586)
    if not point_in(x, y, TORSO):
        continue
    dab("fox_detail", x, y, rng.uniform(8, 28),
        vary(mix([207, 106, 44], [237, 145, 70], rng.random()), 5),
        rng.uniform(0.035, 0.13), "round_soft", 0.18)
for _ in range(120):
    x = rng.uniform(565, 722)
    y = rng.uniform(596, 633)
    if not point_in(x, y, TORSO):
        continue
    dab("fox_detail", x, y, rng.uniform(8, 30),
        vary([91, 39, 28], 6), rng.uniform(0.030, 0.12),
        "round_soft", 0.15)
# a reflected cool note in the deepest flank shadow
for _ in range(40):
    x = rng.uniform(570, 645)
    y = rng.uniform(604, 631)
    if not point_in(x, y, TORSO):
        continue
    dab("fox_detail", x, y, rng.uniform(10, 28), [78, 49, 41],
        rng.uniform(0.02, 0.07), "round_soft", 0.12)

# shoulder and haunch contour strokes keep the leg masses distinct
for poly, warm, shadow in [(HAUNCH, [215, 108, 44], [100, 43, 29]),
                           (SHOULDER, [228, 125, 52], [119, 51, 31])]:
    scatter_fill("fox_detail", poly, 80, (2.0, 8.0),
                 lambda x, y, w=warm, s=shadow: vary(
                     mix(w, s, rng.uniform(0.15, 0.65)), 7),
                 opacity_range=(0.08, 0.28),
                 preset_choices=("dry", "bristle", "round_soft"),
                 angle_fn=lambda x, y, rng=None: 0.9 + rng.uniform(-0.35, 0.35),
                 len_factor=(0.35, 1.1))
taper("fox_detail", [(576, 619), (596, 637), (618, 645), (632, 642)],
      [96, 42, 29], 5.2, 1.0, steps=14, opacity=0.26,
      preset="dry", hardness=0.38)
taper("fox_detail", [(688, 607), (705, 617), (721, 619)],
      [99, 44, 30], 4.0, 0.8, steps=10, opacity=0.22,
      preset="dry", hardness=0.35)

# chest and belly ruff
for _ in range(140):
    x = rng.uniform(718, 778)
    y = rng.uniform(545, 634)
    if not point_in(x, y, CHEST):
        continue
    ang = rng.uniform(1.30, 1.80)
    L = rng.uniform(3, 15)
    S("fox_detail", [(x - math.cos(ang) * L / 2, y - math.sin(ang) * L / 2),
                      (x + math.cos(ang) * L / 2, y + math.sin(ang) * L / 2)],
      rng.uniform(1.1, 4.2), vary(mix([223, 203, 171], [155, 128, 101],
                                      rng.uniform(0, 0.55)), 7),
      rng.uniform(0.10, 0.36), rng.choice(("dry", "round_soft", "liner")),
      rng.uniform(0.25, 0.55))
# the cream chest turns into a warm shadow along its lower edge
for _ in range(62):
    x = rng.uniform(730, 772)
    y = rng.uniform(588, 628)
    if not point_in(x, y, CHEST):
        continue
    L = rng.uniform(2, 9)
    S("fox_detail", [(x - L / 2, y + rng.uniform(-2, 2)),
                      (x + L / 2, y + rng.uniform(-1, 3))],
      rng.uniform(0.8, 2.8), vary(mix([142, 113, 87], [188, 157, 122],
                                      rng.uniform(0.2, 0.8)), 6),
      rng.uniform(0.06, 0.20), "dry", 0.38)
# cream whiskers of fur break the chest silhouette
for _ in range(34):
    x = rng.uniform(722, 764)
    y = rng.uniform(585, 628)
    if not point_in(x, y, CHEST):
        continue
    aa = rng.uniform(1.1, 2.0)
    L = rng.uniform(3, 10)
    S("fox_detail", [(x, y), (x + math.cos(aa) * L, y + math.sin(aa) * L)],
      rng.uniform(0.8, 2.4), vary([226, 201, 164], 6),
      rng.uniform(0.08, 0.24), "liner", 0.35)

# a ruff of cream and russet hairs blends the throat into the chest
for _ in range(64):
    x = rng.uniform(734, 772)
    y = rng.uniform(548, 628)
    if not point_in(x, y, CHEST):
        continue
    aa = rng.uniform(1.0, 2.2)
    L = rng.uniform(3, 12)
    tone = mix([214, 188, 153], [177, 87, 48], rng.uniform(0.0, 0.55))
    S("fox_detail", [(x, y), (x + math.cos(aa) * L, y + math.sin(aa) * L)],
      rng.uniform(0.8, 3.0), vary(tone, 7),
      rng.uniform(0.08, 0.28), rng.choice(("dry", "liner", "round_soft")),
      rng.uniform(0.30, 0.55))
# dark russet shadow under the jaw and along the neck
taper("fox_detail", [(758, 548), (750, 565), (745, 587), (739, 610)],
      [106, 47, 30], 4.2, 1.0, steps=16, opacity=0.26,
      preset="dry", hardness=0.35)

for _ in range(48):
    x = rng.uniform(618, 708)
    y = rng.uniform(613, 633)
    if not point_in(x, y, BELLY):
        continue
    S("fox_detail", [(x - rng.uniform(4, 14), y + rng.uniform(-2, 2)),
                      (x + rng.uniform(4, 14), y + rng.uniform(-2, 2))],
      rng.uniform(1.2, 3.4), vary([214, 188, 154], 7),
      rng.uniform(0.05, 0.22), "round_soft", 0.36)

# tail fur follows the curve; top is lit, underside is deep russet
for _ in range(300):
    t = rng.uniform(0.02, 0.99)
    x, y, tx, ty = sample_line(TAIL_PTS, t)
    nx, ny = -ty, tx
    off = rng.uniform(-1.0, 1.0)
    # width interpolation
    u = t * (len(TAIL_W) - 1)
    wi = min(len(TAIL_W) - 2, int(u))
    wt = u - wi
    width = TAIL_W[wi] * (1 - wt) + TAIL_W[wi + 1] * wt
    px = x + nx * off * width * 0.43
    py = y + ny * off * width * 0.43
    if t > 0.73:
        col = mix([231, 209, 176], [166, 141, 112], rng.uniform(0, 0.65))
    else:
        col = mix([216, 103, 40], [119, 49, 29],
                  max(0.0, min(1.0, 0.25 + off * 0.32 + rng.uniform(0, 0.25))))
        if off < -0.35:
            col = mix(col, [241, 164, 91], 0.18)
    L = rng.uniform(4, 20)
    ang = math.atan2(ty, tx) + rng.uniform(-0.14, 0.14)
    S("fox_detail", [(px - math.cos(ang) * L / 2, py - math.sin(ang) * L / 2),
                      (px + math.cos(ang) * L / 2, py + math.sin(ang) * L / 2)],
      rng.uniform(1.1, 5.6), vary(col, 8), rng.uniform(0.09, 0.38),
      rng.choice(("dry", "bristle", "liner", "round_soft")),
      rng.uniform(0.28, 0.62))
# loose cream hairs at the very tip make a pointed, wind-raked brush
for _ in range(55):
    t = rng.uniform(0.80, 0.995)
    x, y, tx, ty = sample_line(TAIL_PTS, t)
    nx, ny = -ty, tx
    u = t * (len(TAIL_W) - 1)
    wi = min(len(TAIL_W) - 2, int(u))
    wt = u - wi
    width = TAIL_W[wi] * (1 - wt) + TAIL_W[wi + 1] * wt
    off = rng.uniform(-0.50, 0.50) * width
    px, py = x + nx * off, y + ny * off
    L = rng.uniform(4, 16)
    ang = math.atan2(ty, tx) + rng.uniform(-0.18, 0.18)
    S("fox_detail", [(px - math.cos(ang) * L / 2, py - math.sin(ang) * L / 2),
                      (px + math.cos(ang) * L / 2, py + math.sin(ang) * L / 2)],
      rng.uniform(0.9, 3.8), vary(mix([242, 226, 199], [180, 155, 124],
                                      rng.uniform(0, 0.6)), 6),
      rng.uniform(0.10, 0.40), rng.choice(("liner", "dry", "round_soft")),
      rng.uniform(0.26, 0.56))

# a few dark separations between tail locks
for t in (0.18, 0.33, 0.48, 0.59):
    x, y, tx, ty = sample_line(TAIL_PTS, t)
    nx, ny = -ty, tx
    pts = [(x + nx * 20, y + ny * 20), (x + nx * 8 + tx * 10, y + ny * 8 + ty * 10),
           (x + nx * 2 + tx * 18, y + ny * 2 + ty * 18)]
    taper("fox_detail", pts, [95, 40, 28], 4.5, 0.7, steps=10,
          opacity=0.22, preset="dry", hardness=0.35)

# head fur and cream cheek/muzzle strokes
for _ in range(180):
    x = rng.uniform(746, 870)
    y = rng.uniform(466, 540)
    if not point_in(x, y, HEAD):
        continue
    cream = point_in(x, y, CHEEK_CREAM) or point_in(x, y, MUZZLE)
    if cream:
        col = mix([233, 210, 172], [171, 139, 105], rng.uniform(0, 0.55))
        ang = rng.uniform(-0.20, 0.25)
    else:
        col = mix([214, 104, 42], [126, 51, 29], rng.uniform(0, 0.58))
        ang = rng.uniform(-0.10, 0.55) if y < 500 else rng.uniform(-0.7, 0.1)
    L = rng.uniform(2, 10)
    S("fox_detail", [(x - math.cos(ang) * L / 2, y - math.sin(ang) * L / 2),
                      (x + math.cos(ang) * L / 2, y + math.sin(ang) * L / 2)],
      rng.uniform(0.8, 3.5), vary(col, 6), rng.uniform(0.10, 0.40),
      rng.choice(("liner", "dry", "round_soft")), rng.uniform(0.25, 0.55))

# loose cheek and neck ruff, catching a trace of dawn on the outer tips
ruff_curve = [(752, 520), (760, 538), (762, 556), (754, 576), (746, 592)]
for _ in range(54):
    x, y, tx, ty = sample_line(ruff_curve, rng.random())
    nx, ny = -ty, tx
    # outward is camera-left here
    if nx > 0:
        nx, ny = -nx, -ny
    L = rng.uniform(3, 13)
    tone = mix([196, 102, 49], [224, 190, 151], rng.uniform(0.0, 0.75))
    S("fox_detail", [(x, y), (x + nx * L, y + ny * L + rng.uniform(-2, 3))],
      rng.uniform(0.8, 2.8), vary(tone, 7),
      rng.uniform(0.08, 0.30), "liner", 0.35)
    if rng.random() < 0.30:
        S("fox_rim", [(x + nx * L * 0.7, y + ny * L * 0.7),
                       (x + nx * L * 1.2, y + ny * L * 1.2)],
          0.8, [255, 209, 146], rng.uniform(0.05, 0.16),
          "liner", 0.32)

# leg fur, joint accents and separate toes
for li, (pts, wd) in enumerate(LEGS):
    far_leg = li in (0, 2)
    for _ in range(34):
        t = rng.uniform(0.0, 0.58)
        x, y, tx, ty = sample_line(pts, t)
        nx, ny = -ty, tx
        off = rng.uniform(-0.42, 0.42) * wd[0]
        px, py = x + nx * off, y + ny * off
        ang = math.atan2(ty, tx)
        L = rng.uniform(2, 10)
        col = [129, 57, 33] if far_leg else [177, 84, 41]
        S("fox_detail", [(px - math.cos(ang) * L / 2, py - math.sin(ang) * L / 2),
                          (px + math.cos(ang) * L / 2, py + math.sin(ang) * L / 2)],
          rng.uniform(0.9, 3.0), vary(col, 8),
          rng.uniform(0.07, 0.26), "dry", 0.4)
    # hock / wrist crease and a sun-catching edge
    hx, hy, _, _ = sample_line(pts, 0.73)
    S("fox_detail", [(hx - 7, hy + rng.uniform(-2, 2)),
                      (hx + 7, hy + rng.uniform(1, 5))],
      2.2, [78, 43, 32] if not far_leg else [55, 38, 30],
      0.26, "dry", 0.35)
    S("fox_detail", [(hx + 2, hy - 2), (hx + 8, hy + 7)],
      1.4, [222, 139, 77] if not far_leg else [163, 104, 63],
      0.26, "liner", 0.4)
    # toe grooves
    fx, fy = pts[-1]
    # top-of-foot catch light and four separate toes
    S("fox_detail", [(fx - 7, fy - 1), (fx + 3, fy - 4), (fx + 14, fy - 1)],
      1.7, [141, 113, 88], 0.32, "liner", 0.40)
    for j in range(4):
        tx0 = fx + 1.5 + j * 4.0
        S("fox_detail", [(tx0, fy - 2), (tx0 + 0.7, fy + 5)],
          1.3, [124, 105, 86] if not far_leg else [92, 80, 67],
          rng.uniform(0.20, 0.42), "liner", 0.5)
    # small warm glints and cool nail tips on the toes
    for j in range(3):
        S("fox_rim", [(fx + 2 + j * 4.2, fy - 3), (fx + 8 + j * 4.2, fy + 1)],
          1.0, [255, 205, 138], rng.uniform(0.10, 0.25),
          "liner", 0.34)
        S("fox_detail", [(fx + 10.5 + j * 3.8, fy + 2),
                          (fx + 11.4 + j * 3.8, fy + 5.2)],
          1.1, [49, 45, 40], 0.36, "liner", 0.5)

# cylindrical shading on the legs: dark camera-left edge, faint warm right edge
for li, (pts, wd) in enumerate(LEGS):
    far_leg = li in (0, 2)
    for k in range(11):
        t = 0.40 + 0.055 * k
        x, y, tx, ty = sample_line(pts, t)
        nx, ny = -ty, tx
        u = t * (len(wd) - 1)
        wi = min(len(wd) - 2, int(u))
        wt = u - wi
        width = wd[wi] * (1 - wt) + wd[wi + 1] * wt
        # left / shadow side
        px = x + nx * width * 0.33
        py = y + ny * width * 0.33
        S("fox_detail", [(px, py), (px + tx * rng.uniform(3, 8),
                                     py + ty * rng.uniform(3, 8))],
          rng.uniform(0.8, 2.0), [46, 34, 27] if far_leg else [57, 37, 28],
          rng.uniform(0.10, 0.28), "liner", 0.35)
        # sunward edge
        px = x - nx * width * 0.37
        py = y - ny * width * 0.37
        S("fox_rim", [(px, py), (px + tx * rng.uniform(2, 7),
                                  py + ty * rng.uniform(2, 7))],
          rng.uniform(0.5, 1.3), [204, 147, 93] if far_leg else [244, 178, 109],
          rng.uniform(0.06, 0.18), "liner", 0.32)

# whisker roots and mouth line
S("fox_detail", [(870, 509), (858, 514), (846, 519), (834, 522)],
  1.6, [52, 35, 28], 0.56, "liner", 0.6)
S("fox_detail", [(861, 515), (852, 520), (842, 524)],
  1.2, [59, 42, 34], 0.42, "liner", 0.55)
# fine pale whiskers, barely there against the mist
for yy, ang, L in [(508, -0.12, 28), (512, 0.10, 33), (516, 0.25, 26)]:
    S("fox_detail", [(844, yy), (844 + L * math.cos(ang), yy + L * math.sin(ang))],
      0.8, [241, 224, 191], 0.20, "liner", 0.35)

# the eye: dark socket, amber iris, vertical pupil, small hard catchlight
eye = [(802, 489), (807, 483), (815, 481), (823, 484), (826, 490),
       (821, 496), (813, 498), (806, 495), (803, 492)]
fill_poly("fox_detail", eye, [42, 28, 21], size=4.4, spacing=1.7,
          opacity=0.98, preset="round_soft", jitter=0.22, angle=0,
          hardness=0.45, edge=True, edge_inset=0.5)
eye_iris = [(806, 488), (811, 484), (817, 484), (822, 488), (821, 493),
            (816, 496), (809, 493)]
fill_poly("fox_detail", eye_iris, [190, 117, 36], color2=[235, 171, 63],
          size=3.0, spacing=1.0, opacity=1.0, preset="round_hard",
          jitter=0.2, angle=90, gradient_angle=90, hardness=0.55,
          edge=False, edge_inset=0.2)
S("fox_detail", [(814.0, 483.8), (813.7, 487.4), (814.2, 492.8)],
  4.5, [17, 14, 12], 0.98, "round_hard", 0.72)
dab("fox_detail", 817.1, 485.6, 2.5, [255, 246, 219], 0.93,
    "round_hard", 0.9)
S("fox_detail", [(803, 497), (810, 497), (819, 496)],
  1.4, [43, 29, 23], 0.62, "liner", 0.5)
# hooded upper lid and a small warm brow catch
S("fox_detail", [(804, 483), (811, 480), (820, 482), (825, 487)],
  2.2, [62, 34, 25], 0.68, "liner", 0.52)
S("fox_detail", [(806, 493), (812, 495), (819, 493)],
  1.0, [78, 46, 32], 0.42, "liner", 0.45)
S("fox_rim", [(806, 481), (813, 479), (820, 481)],
  1.1, [255, 213, 148], 0.20, "liner", 0.4)
# eyebrow / brow ridge
S("fox_detail", [(801, 479), (810, 477), (822, 480), (830, 485)],
  2.0, [103, 47, 30], 0.42, "dry", 0.4)

# nose and a highlight
NOSE = [(857, 498), (869, 500), (873, 506), (868, 512), (860, 510),
        (856, 505)]
fill_poly("fox_detail", NOSE, [23, 20, 18], size=3.6, spacing=1.5,
          opacity=1.0, preset="round_hard", jitter=0.18, angle=0,
          hardness=0.6, edge=True, edge_inset=0.4)
dab("fox_detail", 864.5, 502.5, 2.1, [184, 172, 158], 0.72,
    "round_soft", 0.3)
dab("fox_detail", 868.0, 507.0, 1.4, [94, 87, 79], 0.38,
    "round_soft", 0.3)

# ragged edge tufts around the silhouette
def edge_tufts(points, n, color, size=2.0, opacity=0.35):
    for i in range(n):
        t = (i + rng.random()) / n
        x, y, tx, ty = sample_line(points, t)
        nx, ny = -ty, tx
        side = rng.choice([-1, 1])
        d = rng.uniform(1, 6)
        L = rng.uniform(2, 8)
        S("fox_detail", [(x + nx * side * d, y + ny * side * d),
                          (x + nx * side * (d + L), y + ny * side * (d + L) +
                           rng.uniform(-2, 2))],
          size * rng.uniform(0.5, 1.2), vary(color, 5),
          opacity * rng.uniform(0.5, 1.1), "liner", 0.45)

edge_tufts([(586, 570), (630, 551), (676, 545), (710, 550), (735, 568)],
           84, [207, 102, 43], 1.9, 0.30)
edge_tufts([(566, 600), (600, 628), (650, 633), (700, 630)], 54,
           [112, 46, 29], 1.6, 0.27)
edge_tufts([(773, 466), (798, 468), (820, 477), (846, 488)], 46,
           [224, 128, 58], 1.4, 0.30)
edge_tufts([(747, 508), (757, 531), (781, 538), (801, 532)], 38,
           [214, 171, 130], 1.3, 0.26)
edge_tufts([(774, 550), (770, 576), (758, 598), (745, 620), (733, 630)],
           42, [216, 188, 151], 1.2, 0.24)
edge_tufts(TAIL_PTS, 92, [221, 106, 43], 1.7, 0.33)
# softer pale border hairs along the white tip
edge_tufts([(460, 704), (454, 714), (439, 735), (419, 739)],
           38, [225, 204, 172], 1.2, 0.26)

# ---------------------------------------------------------------------------
# 10. fox rim light (single screen layer)
# ---------------------------------------------------------------------------
# back rim
back_pts = [(596, 568), (635, 551), (680, 548), (716, 556), (735, 571)]
for _ in range(32):
    t = rng.random()
    x, y, tx, ty = sample_line(back_pts, t)
    nx, ny = -ty, tx
    L = rng.uniform(2, 7)
    S("fox_rim", [(x + nx * rng.uniform(0, 2), y + ny * rng.uniform(0, 2)),
                   (x + nx * rng.uniform(1, 3) + math.cos(-0.1) * L,
                    y + ny * rng.uniform(1, 3) + math.sin(-0.1) * L)],
      rng.uniform(0.6, 2.0), vary([255, 197, 121], 11),
      rng.uniform(0.08, 0.30), "liner", 0.46)
# head and muzzle rim, following the sunward edge
head_rim = [(770, 468), (796, 471), (820, 480), (841, 488),
            (858, 495), (866, 500)]
for _ in range(32):
    x, y = head_rim[rng.randrange(len(head_rim) - 1)]
    x2, y2 = head_rim[min(len(head_rim) - 1, head_rim.index((x, y)) + 1)]
    t = rng.random()
    px, py = x + (x2 - x) * t, y + (y2 - y) * t
    S("fox_rim", [(px + rng.uniform(-2, 2), py + rng.uniform(-1, 2)),
                   (px + rng.uniform(4, 11), py + rng.uniform(-3, 2))],
      rng.uniform(0.6, 2.1), vary([255, 210, 143], 10),
      rng.uniform(0.10, 0.36), "liner", 0.42)
# ear rim light only on the sunward edge and tip, never a closed outline
for pts in ([(787, 419), (793, 430), (799, 455), (802, 475)],
            [(817, 419), (824, 430), (831, 456), (835, 480)]):
    for _ in range(24):
        i = rng.randrange(len(pts) - 1)
        t = rng.random()
        x = pts[i][0] + (pts[i + 1][0] - pts[i][0]) * t
        y = pts[i][1] + (pts[i + 1][1] - pts[i][1]) * t
        S("fox_rim", [(x + rng.uniform(-1, 1), y + rng.uniform(-1, 1)),
                       (x + rng.uniform(1.5, 5), y + rng.uniform(-1, 2))],
          rng.uniform(0.7, 2.0), vary([255, 216, 154], 8),
          rng.uniform(0.16, 0.44), "liner", 0.45)
# chest / shoulder rim
chest_rim = [(776, 553), (775, 573), (762, 598), (748, 623), (732, 643)]
for _ in range(26):
    t = rng.random()
    x, y, _, _ = sample_line(chest_rim, t)
    S("fox_rim", [(x + rng.uniform(0, 2), y + rng.uniform(-2, 2)),
                   (x + rng.uniform(3, 8), y + rng.uniform(3, 10))],
      rng.uniform(0.6, 1.8), vary([255, 209, 143], 9),
      rng.uniform(0.07, 0.26), "liner", 0.40)
# tail top rim
for _ in range(46):
    t = rng.uniform(0.05, 0.76)
    x, y, tx, ty = sample_line(TAIL_PTS, t)
    nx, ny = -ty, tx
    # choose the upper side of the tail relative to screen y
    side = -1 if ny < 0 else 1
    px, py = x + nx * side * rng.uniform(18, 27), y + ny * side * rng.uniform(18, 27)
    ang = math.atan2(ty, tx)
    L = rng.uniform(2, 8)
    S("fox_rim", [(px - math.cos(ang) * L / 2, py - math.sin(ang) * L / 2),
                   (px + math.cos(ang) * L / 2, py + math.sin(ang) * L / 2)],
      rng.uniform(0.6, 1.9), vary([255, 209, 143], 11),
      rng.uniform(0.06, 0.25), "liner", 0.38)
# front leg rim and paw highlights
for pts in (FRONT_NEAR, FRONT_FAR, HIND_NEAR):
    x, y, tx, ty = sample_line(pts, rng.uniform(0.2, 0.8))
    for k in range(8):
        px = x + rng.uniform(2, 8)
        py = y + rng.uniform(8, 28) + k * 2.5
        S("fox_rim", [(px, py), (px + rng.uniform(1, 4), py + rng.uniform(2, 8))],
          rng.uniform(0.7, 1.8), [255, 206, 143], rng.uniform(0.08, 0.26),
          "liner", 0.4)

# ---------------------------------------------------------------------------
# 11. foreground ferns and grasses, vignette and final atmosphere
# ---------------------------------------------------------------------------
# soft undergrowth masses anchor the spikier foreground plants
for cx, cy, rx, ry, n in [(40, 840, 290, 150, 90),
                           (1170, 840, 250, 160, 90),
                           (330, 885, 210, 90, 55),
                           (895, 890, 210, 95, 55)]:
    for _ in range(n):
        ang = rng.uniform(0, math.tau)
        rr = math.sqrt(rng.random())
        x = cx + math.cos(ang) * rx * rr
        y = cy + math.sin(ang) * ry * rr
        size = rng.uniform(12, 65)
        S("foreground", [(x, y),
                          (x + rng.uniform(-size * 0.9, size * 0.9),
                           y - size * rng.uniform(0.15, 1.1))],
          size, vary(mix([22, 39, 29], [47, 62, 36], rng.random()), 6),
          rng.uniform(0.035, 0.16), "round_soft", 0.08)
# a handful of broad shade leaves break the fern linework
for _ in range(70):
    x = rng.choice([rng.uniform(-30, 320), rng.uniform(880, 1230)])
    y = rng.uniform(700, 920)
    aa = rng.uniform(-1.1, 1.1)
    L = rng.uniform(35, 100)
    w = rng.uniform(9, 28)
    S("foreground", [(x, y),
                      (x + math.cos(aa) * L * 0.5 + rng.uniform(-12, 12),
                       y + math.sin(aa) * L * 0.5 + rng.uniform(-12, 12)),
                      (x + math.cos(aa) * L, y + math.sin(aa) * L)],
      w, vary(mix([24, 43, 31], [56, 70, 39], rng.random()), 6),
      rng.uniform(0.10, 0.34), "round_soft", rng.uniform(0.18, 0.45))

# framing fern fronds: close, dark and out of focus at the very edges
for base, tip, bend, sc in [((20, 965), (286, 642), -70, 1.45),
                            ((185, 980), (430, 724), 38, 1.16),
                            ((1195, 960), (928, 636), 70, 1.50),
                            ((1085, 980), (832, 736), -34, 1.12)]:
    fern("foreground", base, tip, bend, sc, [26, 44, 32], [88, 98, 50],
         leaflets=40, opacity=0.88)
for base, tip, bend, sc in [((340, 980), (478, 806), 30, 0.94),
                            ((760, 980), (648, 814), -22, 0.90)]:
    fern("foreground", base, tip, bend, sc, [31, 48, 35], [74, 86, 46],
         leaflets=32, opacity=0.76)

# grass blades around the lower edges
for _ in range(150):
    side_left = rng.random() < 0.52
    base_x = rng.uniform(-20, 390) if side_left else rng.uniform(820, 1220)
    base_y = rng.uniform(810, 930)
    h = rng.uniform(35, 165)
    tip_x = base_x + rng.uniform(-55, 55) + ((-1 if side_left else 1) * rng.uniform(0, 35))
    tip_y = base_y - h
    mid_x = (base_x + tip_x) / 2 + rng.uniform(-20, 20)
    mid_y = (base_y + tip_y) / 2
    col = vary(rng.choice([[28, 48, 32], [48, 64, 35], [70, 76, 40]]), 7)
    S("foreground", [(base_x, base_y), (mid_x, mid_y), (tip_x, tip_y)],
      rng.uniform(1.0, 4.6), col, rng.uniform(0.25, 0.72),
      rng.choice(("round_soft", "dry", "liner")), rng.uniform(0.3, 0.65))

# short grasses in front of the boulder base tie the stone to the forest floor
for _ in range(85):
    bx = rng.uniform(295, 905)
    by = rng.uniform(895, 930)
    h = rng.uniform(18, 78)
    tipx = bx + rng.uniform(-26, 26)
    tipy = by - h
    midx = (bx + tipx) / 2 + rng.uniform(-8, 8)
    midy = (by + tipy) / 2
    col = vary(rng.choice([[27, 44, 32], [43, 59, 37], [69, 73, 41]]), 6)
    S("foreground", [(bx, by), (midx, midy), (tipx, tipy)],
      rng.uniform(0.9, 3.6), col, rng.uniform(0.22, 0.64),
      rng.choice(("round_soft", "dry", "liner")), rng.uniform(0.28, 0.60))

# final warm air and a few motes in the light
for _ in range(105):
    x = rng.uniform(430, 1080)
    y = rng.uniform(320, 700)
    col = vary([255, 226, 169], 12)
    dab("atmosphere", x, y, rng.uniform(0.8, 4.2), col,
        rng.uniform(0.10, 0.56), "round_soft", 0.25)
for _ in range(26):
    x = rng.uniform(470, 1050)
    y = rng.uniform(340, 680)
    dab("atmosphere", x, y, rng.uniform(7, 22), [255, 218, 155],
        rng.uniform(0.012, 0.045), "round_soft", 0.06)
# a final almost invisible warm beam over the scene
S("atmosphere", [(920, 250), (760, 440), (535, 725)],
  138, [255, 211, 148], 0.018, "round_soft", 0.04)
# warm catches on foreground fronds
for _ in range(45):
    x = rng.uniform(0, W)
    y = rng.uniform(640, 895)
    if 330 < x < 860 and y < 800:
        continue
    S("atmosphere", [(x, y), (x + rng.uniform(-7, 7), y - rng.uniform(2, 15))],
      rng.uniform(0.6, 2.0), [255, 212, 143], rng.uniform(0.08, 0.28),
      "liner", 0.4)

# final dawn accents: a soft sun in the opening, warm trunks, boulder rim
for _ in range(72):
    ang = rng.uniform(0, math.tau)
    rr = math.sqrt(rng.random())
    x = 838 + math.cos(ang) * 92 * rr
    y = 292 + math.sin(ang) * 66 * rr
    dab("atmosphere", x, y, rng.uniform(8, 56),
        mix([255, 236, 196], [255, 188, 112], rr),
        rng.uniform(0.004, 0.020) * (1.0 - rr * 0.5),
        "round_soft", 0.05)
for _ in range(38):
    ang = rng.uniform(0, math.tau)
    rr = math.sqrt(rng.random()) * 24
    dab("atmosphere", 838 + math.cos(ang) * rr,
        292 + math.sin(ang) * rr * 0.7, rng.uniform(3, 16),
        [255, 241, 207], rng.uniform(0.015, 0.045), "round_soft", 0.05)
# warm light catches the sunward edges of the framing trunks
for x, y0, y1, w, op in [(1100, 100, 610, 26, 0.052),
                           (1026, 120, 600, 15, 0.038),
                           (330, 150, 580, 12, 0.032),
                           (165, 210, 560, 10, 0.028),
                           (244, 90, 430, 14, 0.026)]:
    for _ in range(34):
        yy = rng.uniform(y0, y1)
        dab("atmosphere", x + rng.uniform(-w, w), yy,
            rng.uniform(5, 18), [255, 206, 145],
            rng.uniform(0.008, op), "round_soft", 0.10)
# warm rim on the boulder and moss edge
for pts, w, op in [([(650, 702), (725, 706), (800, 722), (862, 754), (892, 800)],
                    5.5, 0.15),
                    ([(470, 744), (530, 719), (600, 706), (666, 702)],
                    3.2, 0.11)]:
    for _ in range(36):
        t = rng.random()
        x, y, tx, ty = sample_line(pts, t)
        L = rng.uniform(1.5, 9) * (1.0 - 0.35 * t)
        S("atmosphere", [(x + rng.uniform(-2, 2), y + rng.uniform(-1.5, 1.5)),
                          (x + tx * L + rng.uniform(-1, 1),
                           y + ty * L + rng.uniform(-1, 1))],
          rng.uniform(1.0, max(1.5, w * 0.55)),
          vary([255, 208, 140], 7), op * rng.uniform(0.35, 1.0),
          rng.choice(("round_soft", "liner")), 0.22)
# a few low-opacity atmospheric beams drifting across the clearing
for _ in range(5):
    x0 = rng.uniform(700, 940)
    y0 = rng.uniform(250, 370)
    x1 = x0 - rng.uniform(220, 430)
    y1 = rng.uniform(550, 760)
    S("atmosphere", [(x0, y0), ((x0 + x1) / 2, (y0 + y1) / 2 + 20), (x1, y1)],
      rng.uniform(50, 120), [255, 217, 157],
      rng.uniform(0.006, 0.017), "round_soft", 0.03)

# vignette: soft, irregular darkening at the edges
for _ in range(240):
    edge = rng.random()
    if edge < 0.28:
        x = rng.uniform(-100, W + 100)
        y = rng.uniform(-40, 120)
    elif edge < 0.58:
        x = rng.uniform(-100, 120)
        y = rng.uniform(-20, H + 40)
    elif edge < 0.83:
        x = rng.uniform(W - 120, W + 120)
        y = rng.uniform(-20, H + 40)
    else:
        x = rng.uniform(-50, W + 50)
        y = rng.uniform(H - 100, H + 80)
    dab("vignette", x, y, rng.uniform(90, 300), [11, 22, 20],
        rng.uniform(0.012, 0.055), "round_soft", 0.03)

# final glaze: cool greens in the shadows, a kiss of amber near the glow
for _ in range(95):
    x = rng.uniform(-60, 540)
    y = rng.uniform(250, 860)
    dab("grade", x, y, rng.uniform(120, 310), [26, 51, 52],
        rng.uniform(0.008, 0.028), "round_soft", 0.04)
for _ in range(70):
    x = rng.uniform(680, 1220)
    y = rng.uniform(150, 720)
    dab("grade", x, y, rng.uniform(110, 280), [255, 180, 103],
        rng.uniform(0.006, 0.022), "round_soft", 0.04)

# final color division, laid on an overlay layer so the dawn gains a little
# amber in the opening and a little sea-green in the deep shade
for _ in range(190):
    x = max(-70, min(W + 70, rng.gauss(835, 235)))
    y = max(-60, min(H + 60, rng.gauss(385, 240)))
    dab("color_glow", x, y, rng.uniform(90, 290),
        mix([255, 218, 151], [252, 174, 96], rng.random()),
        rng.uniform(0.004, 0.018), "round_soft", 0.04)
for _ in range(135):
    x = rng.uniform(-60, 530)
    y = rng.uniform(260, 900)
    dab("color_glow", x, y, rng.uniform(100, 330),
        mix([24, 62, 72], [44, 83, 78], rng.random()),
        rng.uniform(0.004, 0.017), "round_soft", 0.04)
for _ in range(75):
    x = max(390, min(1010, rng.gauss(690, 210)))
    y = max(80, min(700, rng.gauss(330, 190)))
    dab("color_glow", x, y, rng.uniform(120, 360),
        [255, 200, 128], rng.uniform(0.0035, 0.014),
        "round_soft", 0.04)

# ---------------------------------------------------------------------------
# write plan
# ---------------------------------------------------------------------------
OUT.mkdir(parents=True, exist_ok=True)
plan_path = OUT / "final.plan.json"
count = plan_io.dump_plan(
    str(plan_path), W, H, doc["strokes"],
    layer_blends=LAYER_BLENDS,
    layer_order=LAYER_ORDER,
)
print(f"plan={plan_path}")
print(f"canvas={W}x{H} layers={len(LAYER_ORDER)} strokes={count} bytes={plan_path.stat().st_size}")
