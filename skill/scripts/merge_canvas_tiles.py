"""把 texture 层的 canvas tile 小笔合并成一整幅笔触（消除方形接缝，纹理不断）。

原来 texture 层是 96px 的 02_rough-canvas tile 铺满画布，亮部会看到方块边；
合并成一张覆盖全画布的笔触后，纹理连续、平均亮度不变。

用法：
    python scripts/merge_canvas_tiles.py plan.json out.json [--layer texture]
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np

import plan_io


def merge_tiles(plan_path, out_path, layer="texture"):
    """返回 (tile 数, w, h)；out_path 写与输入同格式的计划。"""
    doc, w, h = plan_io.load_plan(plan_path)
    tiles = [s for s in doc["strokes"] if s["layer"] == layer]
    if not tiles:
        raise ValueError("layer %r has no strokes" % layer)
    base = dict(tiles[0])
    colors = np.asarray([s["color"] for s in tiles], np.float64)
    base["color"] = [round(float(v), 2) for v in colors.mean(axis=0)]
    base["opacity"] = round(float(np.median([s.get("opacity", 1.0) for s in tiles])), 4)
    base["size"] = round(float(np.hypot(w, h)) * 1.05, 2)
    base["points"] = [[round(w / 2.0, 2), round(h / 2.0, 2), 1.0]]
    strokes = [s for s in doc["strokes"] if s["layer"] != layer] + [base]
    blends = {l["name"]: l.get("blend", "normal") for l in (doc.get("layers") or [])}
    plan_io.dump_plan(str(out_path), w, h, strokes, layer_blends=blends,
                      regions=doc.get("regions"), subject_layers=doc.get("subject_layers"))
    return len(tiles), w, h


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("plan")
    ap.add_argument("out")
    ap.add_argument("--layer", default="texture")
    a = ap.parse_args()
    n, w, h = merge_tiles(a.plan, a.out, a.layer)
    print("merged %d tile strokes into 1 full-canvas stroke (%dx%d) -> %s" % (n, w, h, a.out))


if __name__ == "__main__":
    main()
