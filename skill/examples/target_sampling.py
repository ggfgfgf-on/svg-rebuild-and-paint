"""凭空画（第二种路线）：先造目标图，再按目标图采样落笔。

    python examples/target_sampling.py out.png [plan.json]

和 realistic_sky_sea.py 的差别是「谁决定形体」：
- 第一种：每个循环自己写形体（形体可改，但写实度靠人调）；
- 这一种：形体由目标图决定（更容易写实，但 plan 里改一笔只是改一笔，改不动整体形体）。
目标图这里是解析场；手上有照片时把第 1 步换成 cv2.imread(path) 即可。
"""
import json
import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import cv2
import numpy as np
import plan_io
import render_util as RU

W, H = 900, 620
YH = int(H * 0.62)
rng = np.random.default_rng(5)

# 1) 目标图：竖直渐变 + 太阳 + 三条云带 + 海面反光柱（换成 imread 就是照片采样）
yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
tn = np.clip((YH - yy) / YH, 0, 1)
stops = [0.0, 0.4, 0.75, 1.0]
cols = np.array([[252, 176, 108], [236, 128, 108], [140, 84, 120], [40, 44, 92]], np.float32)
target = np.stack([np.interp(tn, stops, cols[:, i]) for i in range(3)], axis=-1).astype(np.float32)
dist2 = (xx - W * 0.62) ** 2 + (yy - H * 0.33) ** 2
target += np.exp(-dist2 / (2 * 150.0 ** 2))[..., None] * np.array([70, 40, 14], np.float32)
target += np.exp(-dist2 / (2 * 30.0 ** 2))[..., None] * np.array([180, 150, 110], np.float32)
for cy, thick, dark in ((0.22, 26, 0.55), (0.34, 18, 0.42), (0.46, 22, 0.30)):       # 云带
    band = np.exp(-((yy - YH * cy) / thick) ** 2) * (1 + 0.6 * np.sin(xx * 0.012 + cy * 9))
    target *= (1 - dark * np.clip(band, 0, 1))[..., None]
sea = np.array([16, 28, 48], np.float32) * (1 - np.clip((yy - YH) / (H - YH), 0, 1))[..., None]
sea += np.exp(-((xx - W * 0.62) / (40 + 180 * np.clip((yy - YH) / (H - YH), 0, 1))) ** 2)[..., None] * \
       np.array([150, 110, 62], np.float32) * (yy >= YH)[..., None]
target = np.where((yy >= YH)[..., None], sea, target)
target = cv2.GaussianBlur(np.clip(target, 0, 255), (0, 0), 1.2)

# 2) 结构方向：梯度切线（等值线方向）；细节处笔短、平处笔长
lum = cv2.GaussianBlur(cv2.cvtColor(target.astype(np.uint8), cv2.COLOR_RGB2GRAY).astype(np.float32) / 255.0, (0, 0), 2.0)
gx = cv2.Sobel(lum, cv2.CV_32F, 1, 0, ksize=3)
gy = cv2.Sobel(lum, cv2.CV_32F, 0, 1, ksize=3)
gmag = np.hypot(gx, gy)
theta = np.arctan2(gy, gx) + np.pi / 2

# 3) 采样：网格步长按局部尺度走（细节密、平处稀），颜色直接取目标图
strokes = []


def add(layer, x, y, ang, ln, size, color, opacity, preset="flat", hardness=0.45):
    hx, hy = math.cos(ang) * ln / 2, math.sin(ang) * ln / 2
    strokes.append({"layer": layer, "preset": preset, "size": float(round(size, 2)),
                    "opacity": float(round(opacity, 3)),
                    "color": [round(float(c), 1) for c in color], "hardness": hardness,
                    "region_id": 0,
                    "points": [[round(float(x - hx), 1), round(float(y - hy), 1), 1.0],
                               [round(float(x + hx), 1), round(float(y + hy), 1), 1.0]]})


# 3a) 铺底：先让目标图的明暗关系完整出现（大笔、低细节）
for y in range(-6, H, 7):
    for x in range(-40, W, 22):
        yi, xi = int(np.clip(y, 0, H - 1)), int(np.clip(x, 0, W - 1))
        add("base", x + rng.uniform(0, 12), y + rng.uniform(-1, 1), rng.normal(0, 0.03),
            rng.uniform(44, 100), rng.uniform(16, 26), target[yi, xi], rng.uniform(0.8, 0.94))

# 3b) 采样：网格步长按局部尺度走（细节密、平处稀），颜色直接取目标图
for y in range(-6, H, 4):
    for x in range(-6, W, 4):
        yi, xi = int(np.clip(y, 0, H - 1)), int(np.clip(x, 0, W - 1))
        g = float(gmag[yi, xi])
        if g < 0.015 and rng.random() > 0.55:                 # 极平的区域稀疏落笔
            continue
        if y >= YH:
            ang = rng.normal(0, 0.04)                     # 水面只走横笔，避免竖直拉丝
        else:
            ang = float(theta[yi, xi])
            ang = max(-0.7, min(0.7, ang)) + rng.normal(0, 0.05)   # 天空笔不竖过 40°
        ln = float(np.clip(2.4 / (g + 0.05), 6, 28))
        size = float(np.clip(ln * 0.30, 3.0, 9.0))
        c = target[yi, xi] + rng.normal(0, 2.0, 3)
        lay = "base" if y < YH else "water"
        add(lay, x + rng.uniform(-2, 2), y + rng.uniform(-2, 2), ang, ln, size,
            np.clip(c, 0, 255), rng.uniform(0.30, 0.62))

out = Path(sys.argv[1] if len(sys.argv) > 1 else "sampled.png")
plan_path = Path(sys.argv[2] if len(sys.argv) > 2 else out.with_suffix(".plan.json"))
t0 = time.perf_counter()
plan_io.dump_plan(str(plan_path), W, H, strokes,
                  layer_blends={"base": "normal", "water": "normal"},
                  layer_order=["base", "water"])
arr = RU.render_arr(plan_io.load_plan(str(plan_path))[0], gpu=False, nthreads=8)
a = arr[..., 3:4].astype(np.float32) / 255.0
rgb = np.clip(arr[..., :3].astype(np.float32) * a + np.array([250, 247, 240], np.float32) * (1 - a), 0, 255)
cv2.imwrite(str(out), np.ascontiguousarray(rgb.astype(np.uint8)[..., ::-1]))
print(json.dumps({"route": "target_sampling", "strokes": len(strokes), "canvas": [W, H],
                  "png": str(out), "plan_bytes": plan_path.stat().st_size,
                  "render_s": round(time.perf_counter() - t0, 3)}, ensure_ascii=False))
