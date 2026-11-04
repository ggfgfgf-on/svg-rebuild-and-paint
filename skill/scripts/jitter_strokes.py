"""给指定层的笔触位置加确定性抖动，打散像素扫描线对齐（消横条纹/摩尔纹）。

band 层的背景笔触是沿像素扫描线摆的（x/y 落在整像素上、行距 1px），提亮后会出现
规则条纹。这里给每笔一个稳定哈希偏移 + 每点小抖动：形状、笔数、颜色都不变，
只把"整整齐齐的格子"变成手绘式的错位。

用法：
    python scripts/jitter_strokes.py plan.json out.json [--layer band] [--shift 1.2] [--wiggle 0.25] [--seed 0]
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np

import plan_io


def _rng(seed, index):
    return np.random.default_rng((int(seed) * 1000003 + int(index) * 7919) & 0x7FFFFFFF)


def jitter_layer(doc, layer, shift=1.2, wiggle=0.25, seed=0, expand=1.0):
    """就地改 doc 的 strokes（points 只动 x/y），返回被动过的笔数。

    expand>1：每笔围绕自己的 bbox 中心放大，用来盖住"格子式填充"留下的接缝
    （shadow 层是 38px 一格的长路径填充，边界会露成网格线）。"""
    n = 0
    for idx, s in enumerate(doc["strokes"]):
        if s["layer"] != layer:
            continue
        pts = np.asarray(s["points"], np.float64).copy()
        if pts.size == 0:
            continue
        if expand and abs(float(expand) - 1.0) > 1e-9:
            cx = (pts[:, 0].min() + pts[:, 0].max()) * 0.5
            cy = (pts[:, 1].min() + pts[:, 1].max()) * 0.5
            k = float(expand)
            pts[:, 0] = cx + (pts[:, 0] - cx) * k
            pts[:, 1] = cy + (pts[:, 1] - cy) * k
        rng = _rng(seed, idx)
        off = rng.normal(0.0, float(shift), size=2)
        pts[:, 0] += off[0] + rng.normal(0.0, float(wiggle), size=pts.shape[0])
        pts[:, 1] += off[1] + rng.normal(0.0, float(wiggle), size=pts.shape[0])
        s["points"] = [[round(float(v), 3) for v in p] for p in pts.tolist()]
        n += 1
    return n


def jitter_plan(plan_path, out_path, layer="band", shift=1.2, wiggle=0.25, seed=0, expand=1.0):
    doc, w, h = plan_io.load_plan(plan_path)
    n = jitter_layer(doc, layer, shift, wiggle, seed, expand)
    blends = {l["name"]: l.get("blend", "normal") for l in (doc.get("layers") or [])}
    plan_io.dump_plan(str(out_path), w, h, doc["strokes"], layer_blends=blends,
                      regions=doc.get("regions"), subject_layers=doc.get("subject_layers"))
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("plan")
    ap.add_argument("out")
    ap.add_argument("--layer", default="band")
    ap.add_argument("--shift", type=float, default=1.2)
    ap.add_argument("--wiggle", type=float, default=0.25)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--expand", type=float, default=1.0,
                    help=">1 时每笔围绕自身 bbox 中心放大，用于盖住格子填充的接缝")
    a = ap.parse_args()
    n = jitter_plan(a.plan, a.out, a.layer, a.shift, a.wiggle, a.seed, a.expand)
    print("jittered %d strokes in %r (shift=%.2f wiggle=%.2f expand=%.3f) -> %s" % (
        n, a.layer, a.shift, a.wiggle, a.expand, a.out))


if __name__ == "__main__":
    main()
