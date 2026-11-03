"""改画快路径演示：打包计划（plan_store）+ 只重算脏矩形（tile_inc）。

JSON 路径适合调试和交换，但逐笔装配 5.7 万笔很慢；生产路径把计划 packing 成
points/offsets/params 数组，渲染走原生批量接口，改画只重放脏矩形。

    python ops_incremental_demo.py <plan.json> [--layer highlight] [--hue 260]
        [--brightness 1.25] [--densify-layer highlight] [--k 1.2] [--rect "x,y,w,h"]
        [--feather 40] [--gpu] [--out <png>]

判定：重建结果与「整份重画」逐位相同；脏矩形之外与改画前逐位相同。
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np

import compile_ops
import masks
import ops
import paint_native
import plan_io
import plan_store
import tile_inc


def new_painter(doc):
    w, h = [int(v) for v in doc["canvas"]]
    p = paint_native.Painter(w, h)
    p.add_layer("normal")
    p.clear_layer(0, (255, 255, 255), 0.0)
    for spec in doc.get("layers") or []:
        p.add_layer(spec.get("blend", "normal"))
    cat = paint_native.load_catalog(p)
    return p, cat, w, h


def doc_to_plan(doc, cat):
    """plan_io 的 JSON 契约 -> plan_store 的数组快表（全部 rids=0，单区域）。"""
    spec = [(l["name"], l.get("blend", "normal")) for l in (doc.get("layers") or [])]
    by_layer = {}
    for s in doc["strokes"]:
        by_layer.setdefault(s["layer"], []).append(s)
    plan = {}
    for name, _ in spec:
        items = []
        for s in by_layer.get(name, []):
            pid = paint_native.PRESETS.get(s["preset"], cat.get(s["preset"]))
            if pid is None:
                raise KeyError("unknown preset %r" % (s["preset"],))
            items.append({"points": s["points"], "size": s["size"], "opacity": s["opacity"],
                          "color": s["color"], "hardness": s.get("hardness", 0.7),
                          "preset_id": pid, "region_id": 0})
        pts, offs, params, rids, sid = plan_store.pack(items)
        plan[name] = {"pts": pts, "offs": offs, "params": params, "rids": rids, "sid": sid}
    return plan, spec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("plan")
    ap.add_argument("--layer", default="highlight")
    ap.add_argument("--hue", type=float, default=None)
    ap.add_argument("--brightness", type=float, default=None)
    ap.add_argument("--densify-layer", default=None)
    ap.add_argument("--k", type=float, default=1.2)
    ap.add_argument("--rect", default=None)
    ap.add_argument("--feather", type=float, default=32.0)
    ap.add_argument("--gpu", action="store_true")
    ap.add_argument("--out", default=str(Path.home() / ".cache" / "ops_demo" / "inc.png"))
    a = ap.parse_args()

    t0 = time.perf_counter()
    doc, w, h = plan_io.load_plan(a.plan)
    load_s = time.perf_counter() - t0

    t0 = time.perf_counter()
    p, cat, w, h = new_painter(doc)
    plan1, spec = doc_to_plan(doc, cat)
    region = np.zeros((h, w), np.int32)
    tile_inc.full(p, plan1, spec, lambda nm: region, gpu=a.gpu, nthreads=8)
    arr1 = np.ascontiguousarray(p.composite())
    pack_s = time.perf_counter() - t0

    if a.rect:
        x, y, rw, rh = [float(v) for v in a.rect.split(",")]
        mask = masks.rect(x, y, rw, rh)
    else:
        mask = masks.rect(0, 0, w, h)
    t0 = time.perf_counter()
    sel, wt = masks.weights(doc, mask, a.feather, layer=a.layer)
    sel_s = time.perf_counter() - t0

    specs = []
    if a.hue is not None:
        specs.append({"op": "set_layer_hue", "layer": a.layer, "hue": a.hue, "guidance": 0.85})
    if a.brightness is not None:
        specs.append({"op": "set_layer_brightness", "layer": a.layer, "factor": a.brightness, "guidance": 0.9})
    if a.densify_layer:
        specs.append({"op": "densify", "layer": a.densify_layer, "k": a.k})
    compile_ops.validate_ops(doc, specs)
    t0 = time.perf_counter()
    out = ops.apply(doc, specs, selection=sel, weights=wt)
    edit_s = time.perf_counter() - t0

    box = masks.bbox(doc, sel)
    x0, y0, x1, y1 = [int(round(v)) for v in box]
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(w - 1, x1), min(h - 1, y1)
    dirty = {a.layer: [(x0, y0, x1, y1)]}

    t0 = time.perf_counter()
    plan2, spec2 = doc_to_plan(out, cat)
    tile_inc.rebuild(p, plan2, spec2, dirty, lambda nm: region, gpu=a.gpu, nthreads=8)
    arr_inc = np.ascontiguousarray(p.composite())
    rebuild_s = time.perf_counter() - t0

    q, _, _, _ = new_painter(doc)
    t0 = time.perf_counter()
    tile_inc.full(q, plan2, spec2, lambda nm: region, gpu=a.gpu, nthreads=8)
    arr_ref = np.ascontiguousarray(q.composite())
    full2_s = time.perf_counter() - t0
    q.close()

    inc_vs_ref = int(np.any(arr_inc != arr_ref, axis=2).sum())
    before_vs_inc = np.any(arr1 != arr_inc, axis=2)
    ys, xs = np.nonzero(before_vs_inc)
    outside = int(((xs < x0) | (xs > x1) | (ys < y0) | (ys > y1)).sum()) if len(xs) else 0

    out_path = Path(a.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    from PIL import Image
    Image.fromarray(arr_inc, "RGBA").save(out_path)

    print(json.dumps({
        "load_s": round(load_s, 2), "pack_and_full_render_s": round(pack_s, 2),
        "selected": len(sel), "select_s": round(sel_s, 3), "edit_s": round(edit_s, 3),
        "dirty_rect": [x0, y0, x1, y1],
        "rebuild_incremental_s": round(rebuild_s, 3), "full_render_after_s": round(full2_s, 2),
        "speedup_vs_full": round(full2_s / max(rebuild_s, 1e-6), 1),
        "inc_vs_full_diff_px": inc_vs_ref,
        "changed_px": int(before_vs_inc.sum()), "outside_dirty_rect_px": outside,
        "png": str(out_path),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
