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
import ops_incremental_demo as D
import tile_inc


def render_layer(doc, layer):
    d = {"canvas": doc["canvas"],
         "layers": [{"name": "bg", "blend": "normal"}, {"name": "top", "blend": "normal"}],
         "strokes": [dict(s, layer="top") for s in doc["strokes"] if s["layer"] == layer]}
    p, cat, w, h = D.new_painter(d)
    plan, spec = D.doc_to_plan(d, cat)
    region = np.zeros((h, w), np.int32)
    tile_inc.full(p, plan, spec, lambda nm: region, gpu=False, nthreads=8)
    arr = np.ascontiguousarray(p.composite()).copy()
    p.close()
    return arr


def resample_strokes(arr, w, h, cell=16, layer="shadow", size_k=2.2, op_max=0.85, op_scale=0.6,
                     seed=0):
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
            jx = rng.uniform(-0.5, 0.5) * cell
            jy = rng.uniform(-0.5, 0.5) * cell
            out.append({"layer": layer, "preset": "round_soft",
                        "size": round(cell * size_k * rng.uniform(0.85, 1.15), 2),
                        "opacity": round(min(op_max, max(0.02, cov * op_scale * rng.uniform(0.85, 1.15))), 4),
                        "color": [round(float(v) * 255.0, 2) for v in rgb],
                        "hardness": 0.7,
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


def rebuild(plan_path, out_path, cell=16, layer="shadow", op_scale=0.6, seed=0, mode="grid"):
    doc, w, h = plan_io.load_plan(plan_path)
    arr = render_layer(doc, layer)
    if mode == "random":
        fresh = resample_random(arr, w, h, layer, seed=seed)
    else:
        fresh = resample_strokes(arr, w, h, cell, layer, op_scale=op_scale, seed=seed)
    strokes = [s for s in doc["strokes"] if s["layer"] != layer] + fresh
    blends = {l["name"]: l.get("blend", "normal") for l in (doc.get("layers") or [])}
    plan_io.dump_plan(str(out_path), w, h, strokes, layer_blends=blends,
                      regions=doc.get("regions"), subject_layers=doc.get("subject_layers"))
    return len(fresh)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("plan")
    ap.add_argument("out")
    ap.add_argument("--cell", type=int, default=16)
    ap.add_argument("--layer", default="shadow")
    ap.add_argument("--op-scale", type=float, default=0.6)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--mode", choices=("grid", "random"), default="grid")
    a = ap.parse_args()
    n = rebuild(a.plan, a.out, a.cell, a.layer, a.op_scale, a.seed, a.mode)
    print("reshaped %r: %d soft strokes (cell=%d) -> %s" % (a.layer, n, a.cell, a.out))


if __name__ == "__main__":
    main()
