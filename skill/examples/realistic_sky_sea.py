"""凭空画（第一种路线）：解析场直接给颜色——不建参考图，逐段写循环。

    python examples/realistic_sky_sea.py out.png [plan.json]

做法：天空=大气渐变+太阳散射；云=分形密度场，按密度梯度算「法线」给受光/背光两级；
海=按 y 压缩相位的波列 + 太阳正下方按距离扩散的碎光。全部是解析函数 f(x,y)，
所以每段循环改哪一段只影响那一段，plan 里每笔仍可单独改。

输出：PNG（白纸合成）、plan.json、以及实测的笔数与耗时。
"""
import json
import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import cv2
import numpy as np
import render_util as RU
import plan_io

W, H = 960, 640
YH = int(H * 0.62)                      # 地平线
SUNX, SUNY, SUNR = 600, 236, 22
rng = np.random.default_rng(7)


def fbm(shape, octaves=6, fx=1.0, fy=1.0, base=6, gain=0.5):
    """分形值噪声。fx/fy 乘在频率上：越大越碎；云要横着拉长就 fx<fy。"""
    h, w = shape
    out = np.zeros((h, w), np.float32)
    amp, tot = 1.0, 0.0
    for o in range(octaves):
        gx = max(2, int(base * (2 ** o) * fx))
        gy = max(2, int(base * (2 ** o) * fy))
        out += cv2.resize(rng.random((gy, gx)).astype(np.float32), (w, h),
                          interpolation=cv2.INTER_CUBIC) * amp
        tot += amp
        amp *= gain
    out /= tot
    return (out - out.min()) / (np.ptp(out) + 1e-6)


def ss(e0, e1, x):
    t = np.clip((x - e0) / (e1 - e0 + 1e-6), 0.0, 1.0)
    return t * t * (3 - 2 * t)


yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
strokes = []


def add(layer, pts, size, color, opacity, preset="round_soft", hardness=0.3):
    strokes.append({"layer": layer, "preset": preset, "size": float(round(size, 2)),
                    "opacity": float(round(opacity, 3)),
                    "color": [round(float(c), 1) for c in color], "hardness": hardness,
                    "region_id": 0,
                    "points": [[round(float(p[0]), 1), round(float(p[1]), 1), 1.0] for p in pts]})


# ---------- 场：天空 / 云 / 海 ----------
tn = np.clip((YH - yy) / YH, 0.0, 1.0)
ST = [0.0, 0.22, 0.46, 0.72, 1.0]
CO = [(252, 172, 106), (246, 148, 108), (200, 108, 116), (112, 70, 124), (34, 38, 84)]
sky = np.stack([np.interp(tn, ST, [c[i] for c in CO]) for i in range(3)], axis=-1).astype(np.float32)

dist = np.sqrt((xx - SUNX) ** 2 + (yy - SUNY) ** 2)
sky += np.exp(-(dist / 400.0) ** 2)[..., None] * np.array([40, 22, 8], np.float32)
sky += np.exp(-(dist / 120.0) ** 2)[..., None] * np.array([60, 32, 8], np.float32)

dens = fbm((H, W), octaves=6, fx=0.5, fy=2.0, base=5)
dens *= ss(-0.1, 0.3, np.clip((YH - yy) / YH, 0, 1))
dens_s = cv2.GaussianBlur(dens, (13, 13), 0)
gy, gx = np.gradient(dens_s)
nrm = np.sqrt(gx * gx + gy * gy) + 1e-4
L = np.array([SUNX - W * 0.5, SUNY - H * 0.5], np.float32)
L /= np.linalg.norm(L)
shade = np.clip(0.45 + 0.55 * ((-gx * L[0] - gy * L[1]) / nrm), 0.0, 1.0)   # 伪法线·光
lit = np.clip(shade * 0.9 + 0.1, 0, 1)
cloud = np.array([40, 32, 68], np.float32) * (1 - lit[..., None]) + \
        np.array([255, 206, 168], np.float32) * lit[..., None]
alpha = (ss(0.52, 0.63, dens) * 0.95)[..., None]
sky = sky * (1 - alpha) + cloud * alpha
sky += np.clip(1.0 - dist / SUNR, 0, 1)[..., None] ** 0.5 * np.array([255, 246, 214], np.float32)

wy = np.clip(yy, YH, None)
t = np.clip((wy - YH) / float(H - YH), 0.0, 1.0)
u = np.log1p(9.0 * t) / np.log(10.0)                     # 靠近地平线压缩，产生透视
phase = u * 16.0 + fbm((H, W), octaves=4, fx=0.06, fy=0.18, base=6) * 2.0
water = np.array([10, 22, 44], np.float32) * (1 - t[..., None]) + \
        np.array([7, 14, 30], np.float32) * t[..., None]
water += (np.sin(phase * 2 * math.pi) * 10.0 * (1 - 0.55 * t))[..., None] * np.array([0.55, 0.75, 1.0], np.float32)
path = np.exp(-((xx - SUNX) / (34 + 190 * t)) ** 2)
glint = np.clip(path * (0.55 + 0.45 * np.sin((u * 44.0 + phase) * 2 * math.pi)), 0, None) * (0.45 + 0.55 * (1 - t))
water += glint[..., None] * np.array([150, 96, 52], np.float32)
water = cv2.GaussianBlur(water, (5, 5), 0)
sky_sea = np.where((yy >= YH)[..., None], water, sky)

# ---------- 落笔：底色 / 云细节 / 水面 / 反光 ----------
for y in range(-6, H, 6):
    for x in range(-50, W, 24):
        jx, jy = x + rng.uniform(0, 14), y + rng.uniform(-1.5, 1.5)
        c = sky_sea[int(np.clip(jy, 0, H - 1)), int(np.clip(jx, 0, W - 1))]
        add("base", [(jx, jy), (jx + rng.uniform(50, 110), jy + rng.uniform(-2, 2))],
            rng.uniform(18, 30), c, rng.uniform(0.8, 0.95), "flat", 0.6)

gmag = np.hypot(gx, gy)
theta = np.arctan2(gy, gx) + math.pi / 2
ys, xs = np.nonzero(gmag > 0.03)
pick = rng.choice(len(ys), size=min(14000, len(ys)), replace=False)
for i in pick:
    y, x = float(ys[i]), float(xs[i])
    ln = float(np.clip(2.6 / (gmag[int(y), int(x)] + 0.03), 5, 24))
    ang = float(theta[int(y), int(x)])
    c = sky_sea[int(y), int(x)]
    add("detail", [(x - math.cos(ang) * ln / 2, y - math.sin(ang) * ln / 2),
                   (x + math.cos(ang) * ln / 2, y + math.sin(ang) * ln / 2)],
        rng.uniform(3.5, 8), c, rng.uniform(0.35, 0.7), "flat", 0.4)

for y in range(YH + 2, H, 4):
    for x in range(-30, W, 18):
        jx = x + rng.uniform(0, 14)
        c = sky_sea[int(y), int(np.clip(jx, 0, W - 1))]
        add("water", [(jx, y), (jx + rng.uniform(16, 44), y + rng.uniform(-1, 1))],
            rng.uniform(6, 12), c, rng.uniform(0.5, 0.8), "flat", 0.5)

bright = sky_sea.mean(axis=2)
ys, xs = np.nonzero(bright > 215)
pick = rng.choice(len(ys), size=min(1200, len(ys)), replace=False)
for i in pick:
    y, x = float(ys[i]), float(xs[i])
    add("glow", [(x, y), (x + rng.uniform(2, 9), y)], rng.uniform(1.2, 3), (255, 236, 196),
        rng.uniform(0.2, 0.5), "flat", 0.3)

out = Path(sys.argv[1] if len(sys.argv) > 1 else "sky_sea.png")
plan_path = Path(sys.argv[2] if len(sys.argv) > 2 else out.with_suffix(".plan.json"))
blends = {"base": "normal", "detail": "normal", "water": "normal", "glow": "screen"}
t0 = time.perf_counter()
plan_io.dump_plan(str(plan_path), W, H, strokes, layer_blends=blends,
                  layer_order=["base", "detail", "water", "glow"])
arr = RU.render_arr(plan_io.load_plan(str(plan_path))[0], gpu=False, nthreads=8)
a = arr[..., 3:4].astype(np.float32) / 255.0
paper = np.array([250, 247, 240], np.float32)
rgb = np.clip(arr[..., :3].astype(np.float32) * a + paper * (1 - a), 0, 255).astype(np.uint8)
cv2.imwrite(str(out), np.ascontiguousarray(rgb[..., ::-1]))
print(json.dumps({"strokes": len(strokes), "layers": len(blends), "canvas": [W, H],
                  "png": str(out), "plan": str(plan_path), "plan_bytes": plan_path.stat().st_size,
                  "render_s": round(time.perf_counter() - t0, 3)}, ensure_ascii=False))
