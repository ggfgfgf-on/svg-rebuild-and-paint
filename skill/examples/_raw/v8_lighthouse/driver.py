from __future__ import annotations

import json
import math
import random
import sys
import time
from pathlib import Path

SKILL = Path(r"D:/deepseek-harness-dsh-v0.2.2rc1/.dsh-home/skills/svg-rebuild-and-paint")
OUT = Path(r"D:/DSHwork/flower/promo/assets/cold-lighthouse-v8")
sys.path.insert(0, str(SKILL / "scripts"))

import plan_io
import shape_ops

W, H = 1200, 900
SEED = 20260309
rng = random.Random(SEED)

LAYERS = [
    ("base", "normal"),
    ("sky_base", "normal"),
    ("sky_paint", "normal"),
    ("stars", "screen"),
    ("cloud_shadow", "normal"),
    ("cloud_body", "normal"),
    ("cloud_rim", "screen"),
    ("horizon_glow", "screen"),
    ("sea_base", "normal"),
    ("sea_paint", "normal"),
    ("sea_glint", "screen"),
    ("beam", "screen"),
    ("rock_far", "normal"),
    ("rock_mid", "normal"),
    ("rock_near", "normal"),
    ("rock_face", "normal"),
    ("rock_rim", "screen"),
    ("foam", "screen"),
    ("tower_body", "normal"),
    ("tower_shade", "normal"),
    ("tower_band", "normal"),
    ("tower_detail", "normal"),
    ("contact_shadow", "normal"),
    ("iron", "normal"),
    ("iron_rim", "screen"),
    ("lamp", "screen"),
    ("vignette", "softlight"),
]
LAYER_BLENDS = dict(LAYERS)
LAYER_ORDER = [name for name, _ in LAYERS]

doc = {"canvas": [W, H], "layers": [{"name": n, "blend": b} for n, b in LAYERS], "strokes": []}


def clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def lerp(a, b, t):
    return a + (b - a) * t


def mix(a, b, t):
    t = clamp(t)
    return [lerp(a[i], b[i], t) for i in range(3)]


def grad(stops, t):
    t = clamp(t)
    for i in range(len(stops) - 1):
        t0, c0 = stops[i]
        t1, c1 = stops[i + 1]
        if t <= t1:
            u = 0.0 if t1 == t0 else (t - t0) / (t1 - t0)
            return mix(c0, c1, u)
    return list(stops[-1][1])


def add_pts(layer, pts, size, color, opacity=1.0, preset="round_soft",
            hardness=0.45, pressure=1.0, tag=None):
    points = []
    for p in pts:
        if len(p) >= 3:
            points.append([float(p[0]), float(p[1]), float(p[2])])
        else:
            points.append([float(p[0]), float(p[1]), float(pressure)])
    s = {
        "layer": layer,
        "preset": preset,
        "size": max(0.5, float(size)),
        "opacity": clamp(float(opacity)),
        "color": [float(color[0]), float(color[1]), float(color[2])],
        "hardness": clamp(float(hardness)),
        "points": points,
    }
    if tag:
        s["tag"] = tag
    doc["strokes"].append(s)
    return s


def dab(layer, x, y, size, color, opacity=1.0, preset="round_soft",
        hardness=0.45, pressure=1.0):
    return add_pts(layer, [(x, y, pressure)], size, color, opacity, preset, hardness, pressure)


def dash(layer, x, y, length, size, color, opacity=1.0, preset="bristle",
         hardness=0.5, angle=0.0, pressure=1.0):
    a = math.radians(angle)
    dx = math.cos(a) * length * 0.5
    dy = math.sin(a) * length * 0.5
    return add_pts(layer, [(x - dx, y - dy, pressure), (x + dx, y + dy, pressure)],
                   size, color, opacity, preset, hardness, pressure)


def poly_fill(layer, poly, color, color2=None, size=18, opacity=1.0,
              preset="bristle", spacing=None, jitter=0.35, hardness=0.55,
              edge=True, edge_inset=0.0, clip=False, gradient_angle=0.0,
              pressure=1.0, seed=0):
    shape_ops.fill_polygon(
        doc, layer, poly,
        color=color, color2=color2, size=size, opacity=opacity,
        preset=preset, spacing=spacing, jitter=jitter, hardness=hardness,
        edge=edge, edge_inset=edge_inset, clip=clip,
        gradient_angle=gradient_angle, pressure=pressure, seed=seed,
    )


def ellipse_poly(cx, cy, rx, ry, seed=0, n=30, lump=0.09):
    er = random.Random(seed)
    pts = []
    for i in range(n):
        a = math.tau * i / n
        wobble = 1.0 + lump * math.sin(a * 3.0 + er.uniform(-0.45, 0.45))
        wobble += 0.035 * math.sin(a * 5.0 + er.uniform(-0.5, 0.5))
        pts.append((cx + math.cos(a) * rx * wobble,
                    cy + math.sin(a) * ry * wobble))
    return pts


def taper(layer, d, color, size, size_end=1.0, steps=24, opacity=1.0,
          preset="round_soft", hardness=0.6, copies=1, spread=0.0,
          pressure=1.0, pressure_end=None, seed=0):
    shape_ops.tapered_stroke(
        doc, layer, d, color=color, size=size, size_end=size_end,
        steps=steps, opacity=opacity, preset=preset, hardness=hardness,
        copies=copies, spread=spread, pressure=pressure,
        pressure_end=pressure_end, seed=seed,
    )


# ---------------------------------------------------------------------------
# 0. Opaque bed. Large physical strokes, not a flat vector fill.
# ---------------------------------------------------------------------------
for i, y in enumerate((-40, 120, 300, 480, 660, 840, 960)):
    c = [25 + i, 28 + i, 47 + i]
    add_pts("base", [(-160, y, 1.0), (W + 160, y + rng.uniform(-12, 12), 1.0)],
            520, c, 1.0, "flat", 1.0)

# ---------------------------------------------------------------------------
# 1. Twilight sky: vertical color field plus visible dragged paint.
# ---------------------------------------------------------------------------
SKY_STOPS = [
    (0.00, [20, 25, 56]),
    (0.18, [37, 38, 72]),
    (0.38, [73, 56, 86]),
    (0.55, [126, 78, 96]),
    (0.70, [183, 105, 94]),
    (0.82, [224, 139, 92]),
    (0.91, [242, 169, 111]),
    (1.00, [247, 191, 134]),
]
poly_fill(
    "sky_base", [(0, 0), (W, 0), (W, 585), (0, 585)],
    color=SKY_STOPS[0][1], color2=SKY_STOPS[-1][1],
    size=112, opacity=0.62, preset="round_soft", spacing=32,
    jitter=0.58, hardness=0.26, edge=False, gradient_angle=90, seed=11,
)

for i in range(580):
    t = rng.random() ** 0.93
    y = 8 + t * 566
    base = grad(SKY_STOPS, y / 585.0)
    shift = rng.uniform(-16, 13)
    c = [clamp(base[0] + shift, 0, 255),
         clamp(base[1] + shift * 0.55, 0, 255),
         clamp(base[2] - shift * 0.25, 0, 255)]
    x = rng.uniform(-160, W + 160)
    length = rng.uniform(100, 400)
    size = rng.uniform(34, 108)
    angle = rng.uniform(-4.5, 3.5)
    dash("sky_paint", x, y, length, size, c,
         opacity=rng.uniform(0.045, 0.105),
         preset=rng.choice(["flat", "flat", "round_soft"]),
         hardness=rng.uniform(0.16, 0.42), angle=angle,
         pressure=rng.uniform(0.65, 1.0))

# A few broad cross-body sweeps make the thick-paint direction legible.
for i in range(80):
    y = rng.uniform(40, 520)
    c = grad(SKY_STOPS, y / 585.0)
    c = mix(c, [255, 193, 148], rng.uniform(0.0, 0.18))
    dash("sky_paint", rng.uniform(0, W), y, rng.uniform(280, 620),
         rng.uniform(76, 145), c, opacity=rng.uniform(0.022, 0.052),
         preset="flat", hardness=0.18, angle=rng.uniform(-10, 6))

# ---------------------------------------------------------------------------
# 2. Sparse stars, painted as tiny cool impasto points.
# ---------------------------------------------------------------------------
for i in range(34):
    x = rng.uniform(20, W - 20)
    y = rng.uniform(25, 330)
    if rng.random() < 0.45 and y > 220:
        continue
    c = mix([181, 194, 220], [248, 229, 192], rng.random())
    dab("stars", x, y, rng.uniform(1.3, 2.8), c,
        opacity=rng.uniform(0.25, 0.68), preset="round_soft", hardness=0.85)

# ---------------------------------------------------------------------------
# 3. Cloud banks. Shadow -> body -> warm lower rims.
# ---------------------------------------------------------------------------
CLOUDS = [
    dict(cx=185, cy=226, rx=320, ry=105, dark=[32, 33, 60], mid=[75, 62, 91],
         lit=[207, 131, 118], count=560, seed=31),
    dict(cx=435, cy=350, rx=350, ry=86, dark=[53, 45, 74], mid=[111, 76, 100],
         lit=[235, 145, 110], count=520, seed=32),
    dict(cx=1018, cy=272, rx=300, ry=112, dark=[38, 37, 66], mid=[84, 65, 91],
         lit=[215, 128, 109], count=470, seed=33),
    dict(cx=252, cy=521, rx=365, ry=47, dark=[126, 86, 98], mid=[205, 127, 106],
         lit=[255, 190, 130], count=330, seed=34),
    dict(cx=643, cy=461, rx=260, ry=38, dark=[104, 74, 87], mid=[171, 104, 102],
         lit=[247, 165, 113], count=235, seed=35),
]

for ci, cl in enumerate(CLOUDS):
    cr = random.Random(cl["seed"])
    cx, cy, rx, ry = cl["cx"], cl["cy"], cl["rx"], cl["ry"]
    low_cloud = cy >= 450
    body_mid = mix(cl["dark"], cl["mid"], 0.50)
    body_low = mix(cl["mid"], cl["lit"], 0.16)
    # A soft, irregular veil gives each bank one readable silhouette before detail.
    veil = ellipse_poly(cx, cy, rx, ry, seed=cl["seed"] + 900, n=34, lump=0.12)
    poly_fill("cloud_body", veil, body_mid, body_low,
              size=64, opacity=0.075 if not low_cloud else 0.052,
              preset="round_soft", spacing=20, jitter=0.70,
              hardness=0.13, edge=False, gradient_angle=90,
              seed=cl["seed"] + 901)
    inner = ellipse_poly(cx + rx * 0.10, cy - ry * 0.06,
                         rx * 0.72, ry * 0.62, seed=cl["seed"] + 910,
                         n=30, lump=0.15)
    poly_fill("cloud_body", inner, mix(cl["dark"], cl["mid"], 0.72),
              mix(cl["mid"], cl["lit"], 0.22), size=48,
              opacity=0.060 if not low_cloud else 0.045,
              preset="round_soft", spacing=17, jitter=0.74,
              hardness=0.16, edge=False, gradient_angle=90,
              seed=cl["seed"] + 911)
    # Dense underside, kept off the crisp top edge.
    for i in range(cl["count"] // 4):
        a = cr.uniform(0, math.tau)
        rr = math.sqrt(cr.random())
        x = cx + math.cos(a) * rx * rr
        y = cy + math.sin(a) * ry * rr * 0.92 + ry * 0.20
        c = mix(cl["mid"], cl["dark"], 0.12 + 0.30 * cr.random())
        if low_cloud:
            c = mix(cl["mid"], [112, 77, 92], 0.20)
        dash("cloud_shadow", x, y, cr.uniform(45, 150), cr.uniform(24, 62), c,
             opacity=cr.uniform(0.035, 0.095),
             preset="round_soft",
             hardness=cr.uniform(0.10, 0.30), angle=cr.uniform(-4, 2))
    # Main cloud body; undersides curve toward the warm horizon.
    for i in range(cl["count"]):
        a = cr.uniform(0, math.tau)
        rr = math.sqrt(cr.random())
        x = cx + math.cos(a) * rx * rr
        y = cy + math.sin(a) * ry * rr
        vt = clamp((y - (cy - ry)) / (2 * ry))
        c = mix(cl["dark"], cl["mid"], 0.18 + 0.82 * vt)
        c = mix(c, cl["lit"], max(0.0, vt - 0.62) * 0.28)
        if low_cloud:
            c = mix(c, [205, 135, 113], 0.24)
        L = cr.uniform(38, 165) * (0.70 + 0.45 * rr)
        dash("cloud_body", x, y, L, cr.uniform(17, 46), c,
             opacity=cr.uniform(0.036, 0.108),
             preset=cr.choice(["flat", "round_soft", "round_soft"]),
             hardness=cr.uniform(0.14, 0.42), angle=cr.uniform(-5, 2))
    # Warm broken lower silhouette. No continuous neon outline.
    for i in range(cl["count"] // 2):
        x = cx + cr.uniform(-0.96, 0.96) * rx
        q = clamp(1.0 - ((x - cx) / rx) ** 2, 0.0, 1.0)
        y = cy + ry * (0.44 + 0.38 * math.sqrt(q))
        y += cr.gauss(0, ry * 0.10)
        if cr.random() < 0.35:
            continue
        c = mix(cl["mid"], cl["lit"], cr.uniform(0.35, 0.95))
        dash("cloud_rim", x, y, cr.uniform(25, 125), cr.uniform(4, 17), c,
             opacity=cr.uniform(0.040, 0.155),
             preset=cr.choice(["round_soft", "flat"]),
             hardness=cr.uniform(0.18, 0.48), angle=cr.uniform(-4, 2))

# ---------------------------------------------------------------------------
# 4. Low horizon glow behind the sea.
# ---------------------------------------------------------------------------
for i in range(460):
    a = rng.uniform(0, math.tau)
    rr = math.sqrt(rng.random())
    x = 330 + math.cos(a) * 430 * rr
    y = 544 + math.sin(a) * 100 * rr
    c = mix([255, 217, 151], [222, 123, 96], rr)
    dab("horizon_glow", x, y, rng.uniform(22, 86), c,
        opacity=rng.uniform(0.016, 0.055), preset="round_soft",
        hardness=rng.uniform(0.05, 0.22))

for i in range(110):
    y = rng.uniform(492, 575)
    c = mix([255, 210, 142], [235, 140, 104], (y - 492) / 83)
    dash("horizon_glow", rng.uniform(0, 900), y, rng.uniform(40, 240),
         rng.uniform(4, 18), c, opacity=rng.uniform(0.018, 0.075),
         preset="round_soft", hardness=0.20, angle=rng.uniform(-1.5, 1.5))

# ---------------------------------------------------------------------------
# 5. Sea: rear plane, painterly swells, then broken reflected light.
# ---------------------------------------------------------------------------
SEA_STOPS = [
    (0.00, [91, 95, 116]),
    (0.10, [61, 76, 96]),
    (0.34, [34, 54, 75]),
    (0.70, [21, 39, 60]),
    (1.00, [12, 26, 45]),
]
poly_fill(
    "sea_base", [(0, 572), (W, 572), (W, H), (0, H)],
    color=SEA_STOPS[0][1], color2=SEA_STOPS[-1][1],
    size=92, opacity=0.82, preset="round_soft", spacing=24,
    jitter=0.50, hardness=0.30, edge=False, gradient_angle=90, seed=51,
)

for i in range(650):
    t = rng.random() ** 1.38
    y = 576 + t * 320
    c = grad(SEA_STOPS, t)
    c = mix(c, [145, 118, 115], max(0.0, 0.12 - t) * 2.6)
    c = mix(c, [120, 142, 154], rng.uniform(0.0, 0.22))
    length = 35 + 220 * t
    size = 3.5 + 18.0 * t
    dash("sea_paint", rng.uniform(-100, W + 100), y, length, size, c,
         opacity=rng.uniform(0.035, 0.19),
         preset=rng.choice(["flat", "flat", "round_soft"]),
         hardness=rng.uniform(0.22, 0.60), angle=rng.uniform(-2.2, 2.2))

# A few broader curved swells give the water weight.
for i in range(165):
    t = rng.random() ** 1.24
    y = 597 + 292 * t
    x = rng.uniform(-60, 760)
    L = 55 + 240 * t
    c = mix(grad(SEA_STOPS, t), [157, 144, 145], rng.uniform(0.0, 0.24))
    pts = [(x - L * 0.5, y + rng.gauss(0, 1.8)),
           (x, y + rng.uniform(-7, 7) * (0.3 + t)),
           (x + L * 0.5, y + rng.gauss(0, 2.2))]
    add_pts("sea_paint", pts, rng.uniform(6, 22), c,
            opacity=rng.uniform(0.035, 0.14), preset="round_soft",
            hardness=rng.uniform(0.22, 0.54))

# Soft rolling bands under the small flecks.
for i in range(92):
    t = rng.random() ** 1.18
    y = 612 + 278 * t
    x = rng.uniform(-50, 740)
    L = 90 + 300 * t
    c = mix(grad(SEA_STOPS, t), [91, 123, 139], rng.uniform(0.08, 0.30))
    add_pts("sea_paint",
            [(x - L * 0.5, y + rng.gauss(0, 2.2)),
             (x, y + rng.uniform(-9, 9) * (0.4 + t)),
             (x + L * 0.5, y + rng.gauss(0, 2.8))],
            rng.uniform(12, 38), c, opacity=rng.uniform(0.025, 0.085),
            preset="round_soft", hardness=rng.uniform(0.12, 0.38))

# Broken horizon seam, not a ruled black line.
for i in range(10):
    x0 = rng.uniform(-20, 640)
    add_pts("sea_paint", [(x0, 573 + rng.uniform(-0.6, 0.6)),
                          (x0 + rng.uniform(45, 150), 574 + rng.uniform(-0.8, 0.8))],
            rng.uniform(1.8, 3.6), [51, 57, 70], rng.uniform(0.10, 0.25),
            "round_soft", 0.58)
for i in range(150):
    y = rng.uniform(572, 597)
    dash("sea_glint", rng.uniform(-30, 700), y, rng.uniform(20, 150),
         rng.uniform(2, 9), [238, 174, 133],
         opacity=rng.uniform(0.012, 0.055), preset="round_soft",
         hardness=0.20, angle=rng.uniform(-1, 1))

# General wave crest flecks.
for i in range(300):
    t = rng.random() ** 1.5
    y = 585 + t * 300
    x = rng.uniform(-20, 760)
    c = mix([195, 178, 163], [80, 112, 132], t)
    dash("sea_glint", x, y, rng.uniform(8, 62 + 100 * t),
         rng.uniform(1.2, 3.8 + 4.8 * t), c,
         opacity=rng.uniform(0.045, 0.24), preset="round_soft",
         hardness=rng.uniform(0.38, 0.76), angle=rng.uniform(-1.2, 1.2))

# Broken sunset reflection, widening toward the viewer.
for i in range(260):
    t = rng.random()
    y = 582 + 230 * (t ** 0.92)
    half = 14 + 96 * t
    x = 345 + rng.gauss(0, half * 0.44)
    c = mix([255, 219, 156], [230, 126, 91], t * 0.8)
    dash("sea_glint", x, y, rng.uniform(4, 22 + 62 * t),
         rng.uniform(1.0, 2.1 + 4.0 * t), c,
         opacity=rng.uniform(0.035, 0.16), preset="round_soft",
         hardness=0.58, angle=rng.uniform(-0.8, 0.8))

# ---------------------------------------------------------------------------
# 6. Main searchlight cone, painted as soft luminous drags.
# ---------------------------------------------------------------------------
OX, OY = 824.0, 405.0
# Soft nested wedges: the outer haze, the beam body, and a warm inner core.
poly_fill("beam", [(OX, OY), (34, 245), (58, 472)],
          [255, 214, 156], [255, 181, 112], size=128, opacity=0.011,
          preset="round_soft", spacing=38, jitter=0.72, hardness=0.02,
          edge=False, gradient_angle=180, seed=301)
poly_fill("beam", [(OX, OY), (66, 292), (92, 438)],
          [255, 225, 174], [255, 191, 126], size=94, opacity=0.017,
          preset="round_soft", spacing=29, jitter=0.66, hardness=0.035,
          edge=False, gradient_angle=180, seed=302)
poly_fill("beam", [(OX, OY), (118, 330), (145, 384)],
          [255, 239, 197], [255, 208, 148], size=60, opacity=0.024,
          preset="round_soft", spacing=21, jitter=0.58, hardness=0.05,
          edge=False, gradient_angle=180, seed=303)

# Broad atmospheric body of the beam; largest marks are deliberately sparse.
for i in range(180):
    tt = math.sqrt(rng.random())
    dist = 45 + 790 * tt
    x = OX - dist
    cy = OY - 0.083 * dist
    half = 8 + 105 * tt
    s = clamp(rng.gauss(0.0, 0.54), -1.0, 1.0)
    y = cy + s * half
    c = mix([255, 232, 178], [251, 184, 116], abs(s) * 0.72)
    dash("beam", x, y, rng.uniform(75, 210 + 170 * tt),
         rng.uniform(42, 80 + 95 * tt), c,
         opacity=rng.uniform(0.007, 0.020),
         preset="round_soft", hardness=rng.uniform(0.015, 0.09),
         angle=rng.uniform(-2.3, -0.4))

for i in range(760):
    tt = math.sqrt(rng.random())
    dist = 42 + 790 * tt
    x = OX - dist
    # Beam centre rises gently to the left; width opens with distance.
    cy = OY - 0.083 * dist
    half = 7 + 104 * tt
    s = clamp(rng.gauss(0.0, 0.55), -1.0, 1.0)
    y = cy + s * half
    c = mix([255, 230, 171], [249, 180, 112], abs(s) * 0.75)
    length = rng.uniform(28, 58 + 92 * tt)
    size = rng.uniform(7, 20 + 48 * tt)
    dash("beam", x, y, length, size, c,
         opacity=rng.uniform(0.018, 0.058) * (1.25 - 0.35 * tt),
         preset="round_soft", hardness=rng.uniform(0.02, 0.18),
         angle=rng.uniform(-2.0, -0.2))

# A narrow brighter core inside the broad cone.
for i in range(155):
    tt = math.sqrt(rng.random())
    dist = 35 + 690 * tt
    x = OX - dist
    y = OY - 0.083 * dist + rng.gauss(0, 4 + 18 * tt)
    c = mix([255, 248, 218], [255, 201, 133], rng.random())
    dash("beam", x, y, rng.uniform(45, 165), rng.uniform(3, 14 + 12 * tt),
         c, opacity=rng.uniform(0.018, 0.056), preset="round_soft",
         hardness=0.10, angle=-4.8)

# ---------------------------------------------------------------------------
# 7. Headland silhouette and faceted, impasto rock masses.
# ---------------------------------------------------------------------------
far_cliff = [
    (430, 900), (430, 814), (470, 791), (501, 759), (539, 752),
    (561, 729), (600, 721), (640, 697), (690, 705), (730, 684),
    (760, 690), (790, 671), (825, 677), (851, 672), (891, 685),
    (931, 675), (971, 693), (1016, 677), (1051, 698), (1091, 682),
    (1121, 706), (1171, 691), (1200, 697), (1200, 900),
]
near_cliff = [
    (455, 900), (470, 840), (510, 823), (540, 792), (585, 784),
    (620, 756), (660, 750), (700, 737), (750, 744), (790, 730),
    (830, 732), (870, 724), (910, 738), (955, 722), (1000, 744),
    (1050, 724), (1090, 752), (1140, 737), (1200, 750), (1200, 900),
]
poly_fill("rock_far", far_cliff, [91, 74, 91], [49, 44, 64],
          size=36, opacity=0.94, preset="round_soft", spacing=13,
          jitter=0.55, hardness=0.30, edge=True, edge_inset=5,
          gradient_angle=35, seed=71)
poly_fill("rock_mid", [(470, 900), (490, 840), (550, 810), (610, 783),
                       (670, 761), (735, 748), (810, 742), (880, 742),
                       (950, 758), (1030, 760), (1110, 782), (1200, 770),
                       (1200, 900)],
          [65, 54, 70], [33, 31, 46], size=33, opacity=0.96,
          preset="round_soft", spacing=12, jitter=0.62, hardness=0.32,
          edge=True, edge_inset=4, gradient_angle=100, seed=72)
poly_fill("rock_near", near_cliff, [52, 45, 63], [21, 23, 36],
          size=35, opacity=0.97, preset="round_soft", spacing=11.5,
          jitter=0.66, hardness=0.36, edge=True, edge_inset=4,
          gradient_angle=110, seed=73)

# Front-facing planes: one violet plane and one near-black cleft.
poly_fill("rock_face", [(500, 900), (522, 844), (576, 817), (635, 799),
                        (690, 785), (745, 778), (805, 789), (840, 900)],
          [58, 49, 63], [27, 28, 41], size=26, opacity=0.90,
          preset="flat", spacing=9.5, jitter=0.62, hardness=0.48,
          edge=True, edge_inset=3, gradient_angle=90, seed=74)
poly_fill("rock_face", [(825, 900), (842, 800), (890, 763), (950, 758),
                        (1008, 778), (1060, 790), (1115, 785), (1200, 820),
                        (1200, 900)],
          [48, 41, 56], [20, 22, 34], size=28, opacity=0.91,
          preset="flat", spacing=10, jitter=0.67, hardness=0.48,
          edge=True, edge_inset=3, gradient_angle=100, seed=75)

# Angular facet veils, not outlined rocks.
facets = [
    ([(535, 884), (559, 827), (617, 803), (682, 792), (723, 828), (640, 900)],
     [57, 48, 60], [29, 28, 40], 14),
    ([(705, 900), (738, 786), (804, 774), (841, 804), (858, 900)],
     [35, 31, 43], [20, 21, 31], 16),
    ([(870, 900), (884, 798), (938, 771), (999, 788), (1034, 900)],
     [48, 41, 52], [24, 24, 34], 15),
    ([(1050, 900), (1064, 790), (1118, 776), (1175, 796), (1200, 823), (1200, 900)],
     [39, 35, 49], [19, 20, 31], 14),
]
for pi, (poly, c1, c2, sp) in enumerate(facets):
    poly_fill("rock_face", poly, c1, c2, size=26, opacity=0.46,
              preset="flat", spacing=sp, jitter=0.75, hardness=0.48,
              edge=False, gradient_angle=80, seed=81 + pi)

# A lit shelf catches the last sky and the lighthouse spill.
top_shelf = [(548, 807), (610, 774), (688, 750), (770, 738), (844, 731),
             (920, 740), (982, 733), (1044, 749), (1098, 762), (1052, 787),
             (974, 797), (894, 785), (816, 794), (740, 783), (671, 795),
             (606, 811)]
poly_fill("rock_face", top_shelf, [84, 67, 71], [49, 43, 56],
          size=30, opacity=0.22, preset="flat", spacing=12.5,
          jitter=0.72, hardness=0.36, edge=False, gradient_angle=10,
          seed=90)

# Faceted shelves and clefts give the headland a readable rock anatomy.
poly_fill("rock_face", [(560, 819), (650, 792), (730, 804), (810, 797),
                        (890, 809), (970, 798), (1060, 813), (1112, 831),
                        (1051, 848), (962, 836), (875, 851), (790, 839),
                        (700, 846), (620, 834)],
          [45, 39, 51], [23, 25, 36], size=25, opacity=0.44,
          preset="flat", spacing=9.8, jitter=0.68, hardness=0.46,
          edge=False, gradient_angle=90, seed=91)
poly_fill("rock_face", [(480, 900), (500, 846), (555, 818), (620, 806),
                        (690, 817), (760, 845), (802, 900)],
          [77, 60, 65], [36, 33, 45], size=24, opacity=0.38,
          preset="flat", spacing=10.2, jitter=0.66, hardness=0.44,
          edge=False, gradient_angle=80, seed=92)
poly_fill("rock_face", [(945, 900), (962, 821), (1015, 803),
                        (1070, 821), (1110, 850), (1125, 900)],
          [31, 29, 41], [17, 19, 30], size=25, opacity=0.46,
          preset="flat", spacing=10.0, jitter=0.70, hardness=0.46,
          edge=False, gradient_angle=90, seed=93)

# Broad dragged planes break the silhouette into readable rock faces.
ROCK_PLANE_COLORS = [
    [82, 65, 71], [67, 54, 64], [49, 42, 55],
    [37, 33, 47], [26, 27, 39], [101, 76, 75],
]
for i in range(360):
    x = rng.uniform(485, 1200)
    y = rng.uniform(746, 894)
    c = rng.choice(ROCK_PLANE_COLORS)
    dash("rock_face", x, y, rng.uniform(45, 190), rng.uniform(14, 54), c,
         opacity=rng.uniform(0.025, 0.115), preset=rng.choice(["flat", "round_soft"]),
         hardness=rng.uniform(0.22, 0.52), angle=rng.uniform(-58, -10))
# Warm bounce along the shelf and boulder tops.
for i in range(72):
    x = rng.uniform(650, 1010)
    y = rng.uniform(738, 782)
    c = mix([128, 92, 83], [178, 115, 94], rng.random())
    dash("rock_face", x, y, rng.uniform(15, 62), rng.uniform(3, 11), c,
         opacity=rng.uniform(0.018, 0.065), preset="round_soft",
         hardness=0.36, angle=rng.uniform(-24, -2))
# Dark crevices, kept short so they read as chisel marks, not hatching.
for i in range(130):
    x = rng.uniform(530, 1190)
    y = rng.uniform(770, 890)
    dash("rock_face", x, y, rng.uniform(12, 85), rng.uniform(2, 10),
         [20, 21, 32], opacity=rng.uniform(0.05, 0.20),
         preset="flat", hardness=0.48, angle=rng.uniform(-66, -24))

# Thin warm rim on the ridge, and a cooler secondary rim on right faces.
def rim_line(pts, color, size_range, count, opacity_range, layer="rock_rim", jitter_px=8):
    for i in range(count):
        idx = rng.randrange(len(pts) - 1)
        p, q = pts[idx], pts[idx + 1]
        u = rng.random()
        x = lerp(p[0], q[0], u) + rng.gauss(0, jitter_px)
        y = lerp(p[1], q[1], u) + rng.gauss(0, jitter_px * 0.55)
        dash(layer, x, y, rng.uniform(12, 58), rng.uniform(*size_range),
             color, opacity=rng.uniform(*opacity_range),
             preset="round_soft", hardness=rng.uniform(0.30, 0.66),
             angle=rng.uniform(-18, 8))

rim_line([(470, 791), (539, 752), (600, 721), (690, 705), (760, 690),
          (825, 677), (891, 685), (931, 675)],
         [31, 29, 41], (1.8, 4.8), 190, (0.055, 0.22),
         layer="rock_face", jitter_px=4)
rim_line([(470, 791), (539, 752), (600, 721), (690, 705), (760, 690),
          (825, 677), (891, 685), (931, 675)],
         [244, 164, 111], (2.3, 7.2), 330, (0.030, 0.16), jitter_px=5)
rim_line([(955, 722), (1000, 744), (1050, 724), (1090, 752), (1140, 737)],
         [175, 126, 107], (1.8, 5.5), 130, (0.018, 0.085), jitter_px=5)

# Boulder facets near the lighthouse platform.
boulders = [
    [(710, 744), (735, 728), (761, 735), (777, 752), (752, 766), (723, 763)],
    [(876, 737), (899, 724), (929, 735), (944, 754), (916, 765), (888, 759)],
    [(946, 744), (971, 729), (997, 744), (1007, 762), (979, 774), (953, 765)],
    [(650, 762), (675, 747), (701, 755), (714, 774), (688, 787), (661, 781)],
]
for bi, poly in enumerate(boulders):
    poly_fill("rock_face", poly, [73, 58, 64], [31, 29, 41],
              size=12, opacity=0.90, preset="flat", spacing=4.8,
              jitter=0.55, hardness=0.52, edge=True, edge_inset=2,
              gradient_angle=20 + bi * 25, seed=92 + bi)
    # Lit cap inset into each boulder, so the mass reads as a plane in light.
    bcx = sum(p[0] for p in poly) / len(poly)
    bcy = sum(p[1] for p in poly) / len(poly)
    cap = [(bcx + (p[0] - bcx) * 0.76, bcy + (p[1] - bcy) * 0.64 - 3.0) for p in poly]
    poly_fill("rock_face", cap, [92, 71, 74], [54, 45, 55],
              size=6.5, opacity=0.34, preset="flat", spacing=3.2,
              jitter=0.60, hardness=0.45, edge=False, gradient_angle=20,
              seed=96 + bi)
    # Warm upper edges make each boulder read as a solid volume.
    for j in range(18):
        x = rng.uniform(min(p[0] for p in poly) + 2, max(p[0] for p in poly) - 2)
        y = min(p[1] for p in poly) + rng.uniform(0, 8)
        dash("rock_rim", x, y, rng.uniform(6, 25), rng.uniform(1.3, 4.5),
             [240, 161, 110], opacity=rng.uniform(0.03, 0.16),
             preset="round_soft", hardness=0.58, angle=rng.uniform(-15, 5))

# ---------------------------------------------------------------------------
# 8. Sea foam at the left foot of the headland.
# ---------------------------------------------------------------------------
for i in range(280):
    a = rng.uniform(0, math.tau)
    rr = math.sqrt(rng.random())
    x = 548 + math.cos(a) * 105 * rr
    y = 825 + math.sin(a) * 48 * rr
    c = mix([208, 225, 226], [94, 143, 158], rr)
    dab("foam", x, y, rng.uniform(1.2, 7.0), c,
        opacity=rng.uniform(0.04, 0.26), preset=rng.choice(["round_soft", "sponge"]),
        hardness=rng.uniform(0.25, 0.75))
for i in range(80):
    x = rng.uniform(475, 650)
    y = 817 + rng.gauss(0, 13)
    dash("foam", x, y, rng.uniform(18, 90), rng.uniform(1.2, 4.5),
         [220, 235, 235], opacity=rng.uniform(0.05, 0.22),
         preset="round_soft", hardness=0.5, angle=rng.uniform(-5, 5))

# ---------------------------------------------------------------------------
# 9. Lighthouse: masonry plinth, tapered tower, ironwork, lantern.
# ---------------------------------------------------------------------------
CX = 824.0
TOP_Y = 452.0
BASE_Y = 704.0
TOP_HALF = 29.0
BASE_HALF = 55.0

def half_at(y):
    t = (y - TOP_Y) / (BASE_Y - TOP_Y)
    return lerp(TOP_HALF, BASE_HALF, clamp(t))

# Stone footing, slightly wider than the tower.
poly_fill("tower_body", [(CX - 69, 702), (CX + 69, 702), (CX + 59, 735),
                         (CX - 59, 735)],
          [101, 81, 75], [48, 43, 51], size=10.0, opacity=0.98,
          preset="flat", spacing=4.5, jitter=0.40, hardness=0.62,
          edge=True, gradient_angle=0, seed=101)

# Tower body: expanded polygon compensates for clip=True's half-brush inset.
poly_fill("tower_body", [(CX - 61, BASE_Y), (CX + 61, BASE_Y),
                         (CX + 35, TOP_Y), (CX - 35, TOP_Y)],
          [246, 221, 183], [153, 143, 158], size=9.5, opacity=0.98,
          preset="round_soft", spacing=4.5, jitter=0.30, hardness=0.52,
          edge=False, clip=True, gradient_angle=0, seed=102)

# Cylindrical light/shadow, built from vertical dragged strokes.
for i in range(310):
    y = rng.uniform(TOP_Y + 8, BASE_Y - 8)
    hw = half_at(y) - 8
    u = rng.uniform(-0.94, 0.94)
    x = CX + u * hw
    if u > 0.20:
        base = mix([199, 180, 171], [104, 100, 126], clamp((u - 0.2) / 0.8))
    else:
        base = mix([252, 228, 187], [210, 187, 172], clamp((u + 0.94) / 1.14))
    if rng.random() < 0.20:
        base = mix(base, [255, 235, 197], 0.44)
    length = rng.uniform(12, 38)
    add_pts("tower_shade",
            [(x, y), (x + rng.uniform(-2.2, 2.2), y + length)],
            rng.uniform(5.5, 14.0), base, opacity=rng.uniform(0.045, 0.145),
            preset=rng.choice(["round_soft", "flat"]),
            hardness=rng.uniform(0.28, 0.58),
            pressure=rng.uniform(0.72, 1.0))

# Two vermilion bands, each clipped to the taper.
for bi, (y1, y2, tint) in enumerate([
        (456, 480, 1.00), (588, 619, 0.94), (659, 679, 0.90)]):
    w1 = half_at(y1) + 1.5
    w2 = half_at(y2) + 1.5
    band = [(CX - w1, y1), (CX + w1, y1), (CX + w2, y2), (CX - w2, y2)]
    poly_fill("tower_band", band, [177 * tint, 69 * tint, 58 * tint],
              [110, 46, 53], size=7.5, opacity=0.97, preset="round_soft",
              spacing=3.2, jitter=0.30, hardness=0.55, edge=False,
              clip=True, gradient_angle=0, seed=111 + bi)
    for j in range(40):
        y = rng.uniform(y1 + 2, y2 - 2)
        hw = half_at(y) - 8
        u = rng.uniform(-0.9, 0.9)
        x = CX + u * hw
        c = mix([231, 119, 86], [88, 43, 54], (u + 1) * 0.5)
        dash("tower_band", x, y, rng.uniform(6, 20), rng.uniform(2.5, 7.0),
             c, opacity=rng.uniform(0.05, 0.20), preset="flat",
             hardness=0.50, angle=rng.uniform(-2, 2))
    # Darker top/bottom edges keep the band crisp against the white tower.
    for yy in (y1 + 0.9, y2 - 0.9):
        hw = half_at(yy) - 2
        add_pts("tower_band", [(CX - hw, yy), (CX + hw, yy)], 1.5,
                [91, 36, 44], 0.34, "flat", 0.75)

# Fine horizontal joints in the white sections.
for y in range(486, 700, 25):
    if 588 <= y <= 619 or 659 <= y <= 679:
        continue
    hw = half_at(y) - 9
    c = [169, 145, 141] if y % 50 == 0 else [247, 218, 174]
    add_pts("tower_detail", [(CX - hw, y), (CX + hw, y + rng.uniform(-1, 1))],
            1.7, c, opacity=0.065 if y % 50 else 0.055,
            preset="round_soft", hardness=0.42)

# Vertical edge light/shadow.
add_pts("tower_detail", [(CX - half_at(BASE_Y - 4) + 4, BASE_Y - 5),
                         (CX - half_at(TOP_Y + 6) + 4, TOP_Y + 5)],
        3.6, [255, 225, 178], 0.24, "round_soft", 0.50)
add_pts("tower_detail", [(CX + half_at(BASE_Y - 5) - 5, BASE_Y - 5),
                         (CX + half_at(TOP_Y + 6) - 5, TOP_Y + 6)],
        5.0, [76, 70, 89], 0.34, "round_soft", 0.58)

# Small windows and door.
for wi, wy in enumerate([503, 558, 664]):
    hw = half_at(wy)
    wx = CX + 5
    win = [(wx - 8, wy + 13), (wx + 8, wy + 13), (wx + 7, wy - 11), (wx - 7, wy - 11)]
    poly_fill("tower_detail", win, [38, 35, 48], [24, 24, 35],
              size=2.0, opacity=0.96, preset="flat", spacing=1.5,
              jitter=0.1, hardness=0.90, edge=False, clip=True,
              gradient_angle=90, seed=121 + wi)
    add_pts("tower_detail", [(wx - 10, wy + 15), (wx + 10, wy + 15)],
            3.0, [239, 204, 157], 0.48, "flat", 0.7)
    add_pts("tower_detail", [(wx - 8, wy - 10), (wx - 8, wy + 12)],
            1.4, [195, 151, 94], 0.40, "round_soft", 0.55)

# Arched door at the foot.
door_poly = [(CX - 11, BASE_Y - 2), (CX + 11, BASE_Y - 2),
             (CX + 10, 675), (CX + 5, 669), (CX, 667), (CX - 5, 669), (CX - 10, 675)]
poly_fill("tower_detail", door_poly, [34, 31, 39], [19, 20, 29],
          size=2.0, opacity=0.98, preset="flat", spacing=1.5,
          jitter=0.05, hardness=0.92, edge=False, clip=True,
          gradient_angle=90, seed=131)
add_pts("tower_detail", [(CX - 11, BASE_Y - 3), (CX + 11, BASE_Y - 3)],
        4.0, [221, 181, 136], 0.32, "flat", 0.72)
add_pts("tower_detail", [(CX - 8, 685), (CX + 8, 685)],
        1.5, [216, 157, 102], 0.55, "round_soft", 0.8)

# Gallery deck, railing, lantern room and roof.
poly_fill("iron", [(CX - 51, 447), (CX + 51, 447), (CX + 46, 458), (CX - 46, 458)],
          [70, 63, 72], [29, 29, 40], size=6.0, opacity=0.98,
          preset="flat", spacing=3.0, jitter=0.30, hardness=0.68,
          edge=True, gradient_angle=0, seed=141)

# Amber lantern panes.
poly_fill("tower_detail", [(CX - 23, 387), (CX + 23, 387),
                            (CX + 23, 419), (CX - 23, 419)],
          [247, 184, 91], [185, 111, 67], size=5.0, opacity=0.97,
          preset="round_soft", spacing=3.4, jitter=0.25, hardness=0.45,
          edge=False, clip=True, gradient_angle=90, seed=142)

for x in [CX - 39, CX - 30, CX - 20, CX - 10, CX, CX + 10, CX + 20, CX + 30, CX + 39]:
    h = 27 if abs(x - CX) > 29 else 30
    add_pts("iron", [(x, 418), (x, 448 - (0 if abs(x - CX) > 32 else 0))],
            2.0 if abs(x - CX) > 29 else 3.2,
            [20, 21, 30], 0.90, "flat", 0.88)
for y in [418, 428, 446]:
    add_pts("iron", [(CX - 43, y), (CX + 43, y)], 2.6 if y != 428 else 1.5,
            [24, 24, 34], 0.92, "flat", 0.9)

# Lantern posts and glazing bars.
for x in [CX - 26, CX - 9, CX + 9, CX + 26]:
    add_pts("iron", [(x, 385), (x, 421)], 3.0 if abs(x - CX) > 20 else 2.0,
            [24, 24, 34], 0.95, "flat", 0.9)
add_pts("iron", [(CX - 29, 385), (CX + 29, 385)], 4.0, [34, 31, 41], 0.95, "flat", 0.8)
add_pts("iron", [(CX - 27, 420), (CX + 27, 420)], 4.2, [29, 28, 38], 0.95, "flat", 0.8)

# Conical roof.
poly_fill("iron", [(CX - 42, 386), (CX + 42, 386), (CX + 4, 349), (CX - 4, 349)],
          [67, 60, 70], [31, 31, 42], size=7.0, opacity=0.98,
          preset="flat", spacing=3.2, jitter=0.28, hardness=0.66,
          edge=True, edge_inset=2, gradient_angle=0, seed=151)
# Warm left roof edge.
for i in range(35):
    u = rng.random()
    x = lerp(CX - 42, CX - 1, u) + rng.gauss(0, 1.2)
    y = lerp(386, 351, u) + rng.gauss(0, 1.5)
    dash("iron_rim", x, y, rng.uniform(4, 16), rng.uniform(1.2, 3.4),
         [241, 174, 107], opacity=rng.uniform(0.08, 0.26),
         preset="round_soft", hardness=0.6, angle=rng.uniform(-55, -20))

# Finial and lightning rod.
dab("iron", CX, 344, 7.5, [50, 45, 55], 0.95, "round_hard", 0.75)
dab("iron_rim", CX - 2, 342, 3.0, [245, 188, 119], 0.45, "round_soft", 0.6)
add_pts("iron", [(CX, 345), (CX, 327)], 2.0, [39, 36, 46], 0.95, "flat", 0.82)
dab("iron", CX, 326, 2.2, [210, 161, 105], 0.8, "round_soft", 0.7)

# Contact shadow in front of the footing.
for i in range(95):
    x = CX + rng.uniform(-92, 98)
    y = rng.uniform(728, 744)
    dash("contact_shadow", x, y, rng.uniform(8, 40), rng.uniform(2.5, 8.0),
         [13, 15, 24], opacity=rng.uniform(0.06, 0.30),
         preset="flat", hardness=0.48, angle=rng.uniform(-3, 3))
for i in range(34):
    x = CX + rng.uniform(-58, 62)
    y = rng.uniform(732, 741)
    dash("rock_rim", x, y, rng.uniform(5, 26), rng.uniform(1.2, 4.0),
         [224, 149, 103], opacity=rng.uniform(0.03, 0.12),
         preset="bristle", hardness=0.62, angle=rng.uniform(-6, 4))

# ---------------------------------------------------------------------------
# 10. Lantern: a compact white-hot core inside layered amber halos.
# ---------------------------------------------------------------------------
LX, LY = CX, 403.0
for i in range(520):
    a = rng.uniform(0, math.tau)
    rr = math.sqrt(rng.random())
    radius = 2.0 + rr * 145.0
    x = LX + math.cos(a) * radius
    y = LY + math.sin(a) * radius * 0.88
    c = mix([255, 251, 226], [247, 159, 84], rr ** 0.55)
    dab("lamp", x, y, lerp(4.5, 34.0, rr), c,
        opacity=lerp(0.13, 0.023, rr), preset="round_soft",
        hardness=lerp(0.26, 0.06, rr))
for i in range(90):
    radius = rng.uniform(0, 11)
    a = rng.uniform(0, math.tau)
    x = LX + math.cos(a) * radius
    y = LY + math.sin(a) * radius
    dab("lamp", x, y, rng.uniform(1.5, 4.5), [255, 252, 232],
        opacity=rng.uniform(0.22, 0.72), preset="round_soft", hardness=0.55)

# Warm reflection on the gallery underside.
for i in range(45):
    x = CX + rng.uniform(-48, 48)
    dash("lamp", x, 446 + rng.uniform(0, 11), rng.uniform(5, 28),
         rng.uniform(1.0, 3.0), [255, 190, 101],
         opacity=rng.uniform(0.03, 0.14), preset="round_soft", hardness=0.4)

# ---------------------------------------------------------------------------
# 11. Final softlight vignette, leaving the lantern open.
# ---------------------------------------------------------------------------
for i in range(280):
    edge = rng.randrange(4)
    if edge == 0:
        x, y = rng.uniform(-90, 290), rng.uniform(-100, H + 80)
    elif edge == 1:
        x, y = rng.uniform(W - 290, W + 90), rng.uniform(-100, H + 80)
    elif edge == 2:
        x, y = rng.uniform(-100, W + 100), rng.uniform(-90, 160)
    else:
        x, y = rng.uniform(-100, W + 100), rng.uniform(H - 170, H + 90)
    dab("vignette", x, y, rng.uniform(70, 170), [17, 17, 31],
        opacity=rng.uniform(0.012, 0.045), preset="round_soft",
        hardness=rng.uniform(0.02, 0.15))

# Guard against layer typos before writing.
used = {s["layer"] for s in doc["strokes"]}
missing = used - set(LAYER_ORDER)
if missing:
    raise RuntimeError(f"Undeclared layers: {sorted(missing)}")
for s in doc["strokes"]:
    if len(s["points"]) == 0:
        raise RuntimeError("Empty stroke")
    for p in s["points"]:
        if len(p) != 3:
            raise RuntimeError("Non-triple point")

out_plan = OUT / "lighthouse.plan.json"
written = plan_io.dump_plan(
    str(out_plan), W, H, doc["strokes"],
    layer_blends=LAYER_BLENDS, layer_order=LAYER_ORDER,
)
print(json.dumps({
    "plan": str(out_plan),
    "written": written,
    "strokes": len(doc["strokes"]),
    "layers": len(LAYER_ORDER),
    "bytes": out_plan.stat().st_size,
    "seed": SEED,
}, ensure_ascii=False))
