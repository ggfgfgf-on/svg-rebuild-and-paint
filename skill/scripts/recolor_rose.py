"""按颜色选笔改色：把红玫瑰改成蓝玫瑰。

为什么不用几何掩膜：这张画的大笔触同时盖着花头和背景，按区域选笔要么漏、要么把背景一起染；
而"红色笔触"本身几乎只出现在花上（绿背景、绿叶子天然被排除）。所以选笔用颜色，不用区域。

    python scripts/recolor_rose.py plan.json --hue 218 --out-plan plan_blue.json --out-png rose_blue.png

参数：
    --hue       目标色相（0-360；蓝≈220，紫≈275，洋红≈300）
    --hue-tol   原色的"红"判定半宽（默认 40°，即 hue<40 或 >320）
    --sat-min   只改饱和度不低于该值的笔（默认 0.45，避免把棕褐色的茎一起改）
    --val-min   只改明度不低于该值的笔（默认 0.12）
    --boost-sat 改色后把饱和度拉到该值（可选，默认不改）
"""
import argparse
import colorsys
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ops
import plan_io


def hsv_of(rgb):
    r, g, b = [float(v) / 255.0 for v in np.asarray(rgb)[:3]]
    return colorsys.rgb_to_hsv(r, g, b)


def is_red(h, sat, v, tol, sat_min, val_min):
    hd = (h * 360.0) % 360.0
    return sat >= sat_min and v >= val_min and (hd <= tol or hd >= 360.0 - tol)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("plan")
    ap.add_argument("--hue", type=float, default=218.0)
    ap.add_argument("--hue-tol", type=float, default=40.0)
    ap.add_argument("--sat-min", type=float, default=0.45)
    ap.add_argument("--val-min", type=float, default=0.12)
    ap.add_argument("--boost-sat", type=float, default=None)
    ap.add_argument("--out-plan", required=True)
    ap.add_argument("--out-png", required=True)
    a = ap.parse_args()

    doc, w, h = plan_io.load_plan(a.plan)
    by_layer = {}
    for i, s in enumerate(doc["strokes"]):
        hh, sat, v = hsv_of(s["color"])
        if is_red(hh, sat, v, a.hue_tol, a.sat_min, a.val_min):
            by_layer.setdefault(s["layer"], []).append(i)

    specs = []
    for name in sorted(by_layer):
        specs.append({"op": "set_layer_hue", "layer": name, "hue": a.hue, "guidance": 1.0})
        if a.boost_sat is not None:
            specs.append({"op": "set_layer_saturation", "layer": name, "sat": a.boost_sat, "guidance": 1.0})

    out = doc
    for name in sorted(by_layer):
        sel = by_layer[name]
        out = ops.apply(out, [{"op": "set_layer_hue", "layer": name, "hue": a.hue, "guidance": 1.0}],
                        selection=sel, weights=[1.0] * len(sel))
        if a.boost_sat is not None:
            out = ops.apply(out, [{"op": "set_layer_saturation", "layer": name, "sat": a.boost_sat,
                                   "guidance": 1.0}], selection=sel, weights=[1.0] * len(sel))

    Path(a.out_plan).write_text(json.dumps(out), encoding="utf-8")
    stats = plan_io.render_plan(a.out_plan, a.out_png)
    print(json.dumps({"changed_strokes": sum(len(v) for v in by_layer.values()),
                      "per_layer": {k: len(v) for k, v in sorted(by_layer.items())},
                      "hue": a.hue, "boost_sat": a.boost_sat,
                      "plan": a.out_plan, "png": a.out_png, "render": stats}, ensure_ascii=False))


if __name__ == "__main__":
    main()
