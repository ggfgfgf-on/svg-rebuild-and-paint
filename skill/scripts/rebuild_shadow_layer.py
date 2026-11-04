"""把 shadow 层的"38px 格填"重采样成连续软笔触（消除格子接缝）。

做法：单独渲染 shadow 层拿到 RGBA（alpha=覆盖），按 cell 像素分块求
alpha 加权颜色与平均覆盖，然后在每块中心放一支 round_soft 软笔（size≈2.2*cell、可重叠）。
结果：同样的明暗分布，但没有格子边界。

用法：
    python scripts/rebuild_shadow_layer.py plan.json out.json [--cell 16] [--layer shadow]
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np

import plan_io
import render_util as RU


def render_layer(doc, layer):
    """单层 RGBA —— 委托给 render_util（同一套渲染/亮度辅助，避免各脚本各写一份）。"""
    return RU.render_layer_rgba(doc, layer)


def resample_strokes(arr, w, h, cell=12, layer="shadow", size_k=5.0, op_max=0.85, op_scale=0.12,
                     seed=0, hardness=0.6, jitter=0.5, size_spread=0.15):
    """每格一支软笔；位置/大小/不透明度都加确定性抖动，避免软笔落在规则网格上再出摩尔纹。"""
    a = arr.astype(np.float64) / 255.0
    alpha = a[..., 3]
    rng = np.random.default_rng(int(seed))
    out = []
    for y0 in range(0, h, cell):
        for x0 in range(0, w, cell):
            blk = alpha[y0:y0 + cell, x0:x0 + cell]
            cov = float(blk.mean())
            if cov < 0.04:
                continue
            wgt = blk.sum()
            rgb = (a[y0:y0 + cell, x0:x0 + cell, :3] * blk[..., None]).sum(axis=(0, 1)) / max(wgt, 1e-6)
            jx = rng.uniform(-jitter, jitter) * cell
            jy = rng.uniform(-jitter, jitter) * cell
            out.append({"layer": layer, "preset": "round_soft",
                        "size": round(cell * size_k * rng.uniform(1.0 - size_spread, 1.0 + size_spread), 2),
                        "opacity": round(min(op_max, max(0.002, cov * op_scale * rng.uniform(0.85, 1.15))), 4),
                        "color": [round(float(v) * 255.0, 2) for v in rgb],
                        "hardness": hardness,
                        "points": [[x0 + cell / 2.0 + jx, y0 + cell / 2.0 + jy, 1.0]]})
    return out


def resample_random(arr, w, h, layer="shadow", n=9000, size=46.0, opacity=0.16, seed=0):
    """随机抛洒软笔（不接受网格），密度按 alpha 接受率，叠出连续阴影。"""
    a = arr.astype(np.float64) / 255.0
    alpha = a[..., 3]
    rng = np.random.default_rng(int(seed))
    xs = rng.uniform(0, w, n)
    ys = rng.uniform(0, h, n)
    xi = np.clip(xs.astype(int), 0, w - 1)
    yi = np.clip(ys.astype(int), 0, h - 1)
    keep = rng.uniform(0, 1, n) < alpha[yi, xi]
    out = []
    for x, y, i, j in zip(xs[keep], ys[keep], xi[keep], yi[keep]):
        rgb = a[j, i, :3]
        out.append({"layer": layer, "preset": "round_soft",
                    "size": round(size * rng.uniform(0.8, 1.25), 2),
                    "opacity": round(min(0.5, opacity * rng.uniform(0.8, 1.2)), 4),
                    "color": [round(float(v) * 255.0, 2) for v in rgb],
                    "hardness": 0.6,
                    "points": [[round(float(x), 2), round(float(y), 2), 1.0]]})
    return out


strokes_to_rgba = RU.strokes_to_rgba


def smooth_layer_strokes(strokes, w, h, layer="shadow", seed=0, cell=12, size_k=5.0, op_scale=0.12):
    """把任意一层的格填笔触重采样成连续软笔（planner 默认调用，见 --shadow-smooth）。

    cell=12 网格 + size=5*cell 的重叠软笔 + op_scale=0.12：实测格纹与"气泡"边界都消失，
    整图亮度 45.6 -> 45.1（tone 参数不变）。
    """
    if not strokes:
        return []
    arr = strokes_to_rgba(strokes, w, h)
    return resample_strokes(arr, w, h, cell, layer, size_k=size_k, op_scale=op_scale, seed=seed)


def apply_shadow_mode(strokes, w, h, mode="drop", seed=0, **kw):
    """planner 用的三档：drop=删掉格填 shadow（默认，最锐利）、smooth=重采样软笔、keep=原样。"""
    if mode == "keep" or not strokes:
        return strokes
    if mode == "drop":
        return []
    if mode == "smooth":
        return smooth_layer_strokes(strokes, w, h, seed=seed, **kw)
    raise ValueError("unknown shadow mode: %r" % (mode,))


def rebuild(plan_path, out_path, cell=12, layer="shadow", op_scale=0.12, seed=0, mode="grid",
            size_k=5.0, hardness=0.6, jitter=0.5, size_spread=0.15):
    doc, w, h = plan_io.load_plan(plan_path)
    arr = render_layer(doc, layer)
    if mode == "random":
        fresh = resample_random(arr, w, h, layer, seed=seed)
    else:
        fresh = resample_strokes(arr, w, h, cell, layer, size_k=size_k, op_scale=op_scale, seed=seed,
                                 hardness=hardness, jitter=jitter, size_spread=size_spread)
    strokes = [s for s in doc["strokes"] if s["layer"] != layer] + fresh
    blends = {l["name"]: l.get("blend", "normal") for l in (doc.get("layers") or [])}
    plan_io.dump_plan(str(out_path), w, h, strokes, layer_blends=blends,
                      regions=doc.get("regions"), subject_layers=doc.get("subject_layers"))
    return len(fresh)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("plan")
    ap.add_argument("out")
    ap.add_argument("--cell", type=int, default=12)
    ap.add_argument("--layer", default="shadow")
    ap.add_argument("--op-scale", type=float, default=0.12)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--mode", choices=("grid", "random"), default="grid")
    ap.add_argument("--size-k", type=float, default=5.0)
    a = ap.parse_args()
    n = rebuild(a.plan, a.out, a.cell, a.layer, a.op_scale, a.seed, a.mode, a.size_k)
    print("reshaped %r: %d soft strokes (cell=%d) -> %s" % (a.layer, n, a.cell, a.out))


if __name__ == "__main__":
    main()
