"""改画演示：真实计划上跑「指令 -> 算子 -> 重渲染」，量脏矩形外的像素差。

    python ops_demo.py <plan.json> [--layer highlight] [--hue 260] [--brightness 1.25]
                       [--densify-layer highlight] [--k 1.2] [--rect "x,y,w,h"]
                       [--feather 32] [--out-dir <dir>]

判定：changed_px > 0 且 outside_px == 0（脏矩形之外逐位相同）。
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np

import cli_util as CU
import compile_ops
import masks
import ops
import paint_native
import plan_io


def render(doc, out_png, threads=8):
    w, h = [int(v) for v in doc["canvas"]]
    p = paint_native.Painter(w, h)
    p.add_layer("normal")
    p.clear_layer(0, (255, 255, 255), 0.0)
    layers = doc.get("layers") or []
    for spec in layers:
        p.add_layer(spec.get("blend", "normal"))
    rid = fg = None
    regions = doc.get("regions")
    if regions:
        z = np.load(regions)
        rid, fg = z["rid"], z["fg"]
        p.set_region(rid)
    subject = set(doc.get("subject_layers") or [])
    catalog = paint_native.load_catalog(p)   # 只扫一次，别每笔都扫
    by_layer = {}
    for s in doc["strokes"]:
        by_layer.setdefault(s["layer"], []).append(s)
    for i, spec in enumerate(layers):
        name = spec["name"]
        if regions:
            p.set_region(fg if name in subject else rid)
        batch = []
        for s in by_layer.get(name, []):
            pid = plan_io.resolve_preset(s["preset"], p, allow_tips=True, catalog=catalog)
            batch.append({"points": np.asarray(s["points"], np.float32), "size": float(s["size"]),
                          "opacity": float(s["opacity"]),
                          "color": np.asarray(plan_io._color(s["color"]), np.float32),
                          "preset": pid, "hardness": float(s.get("hardness", 0.7)),
                          "region_id": int(s.get("region_id", 0))})
        if batch:
            p.stroke_batch(i + 1, batch, nthreads=threads)
    comp = p.composite()
    p.close()
    from PIL import Image
    Image.fromarray(comp, "RGBA").save(out_png)
    return comp


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
    ap.add_argument("--out-dir", default=str(CU.cache_dir()))
    a = ap.parse_args()

    t0 = time.perf_counter()
    doc, w, h = plan_io.load_plan(a.plan)
    load_s = time.perf_counter() - t0
    summary = compile_ops.summarize(doc)

    if a.rect:
        x, y, rw, rh = [float(v) for v in a.rect.split(",")]
        mask = masks.rect(x, y, rw, rh)
    else:
        mask = masks.rect(0, 0, w, h)
    sel, wt = masks.weights(doc, mask, a.feather, layer=a.layer)
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

    outdir = Path(a.out_dir)
    outdir.mkdir(parents=True, exist_ok=True)
    p1, p2 = outdir / "before.png", outdir / "after.png"
    t0 = time.perf_counter()
    render(doc, p1)
    render_a = time.perf_counter() - t0
    print("render before: %.2fs" % render_a, flush=True)
    t0 = time.perf_counter()
    render(out, p2)
    render_b = time.perf_counter() - t0
    print("render after: %.2fs" % render_b, flush=True)

    from PIL import Image
    a1 = np.asarray(Image.open(p1))
    a2 = np.asarray(Image.open(p2))
    diff = np.any(a1 != a2, axis=2)
    ys, xs = np.nonzero(diff)
    box = masks.bbox(doc, sel)
    outside = 0
    if box and len(xs):
        x0, y0, x1, y1 = box
        outside = int(((xs < x0 - 1) | (xs > x1 + 1) | (ys < y0 - 1) | (ys > y1 + 1)).sum())
    print(json.dumps({
        "plan": Path(a.plan).name, "canvas": [w, h], "load_s": round(load_s, 2),
        "total_strokes": len(doc["strokes"]), "layers": [(l["name"], l["strokes"]) for l in summary["layers"]],
        "mask": mask, "selected": len(sel), "ops": specs,
        "strokes_after": len(out["strokes"]), "edit_s": round(edit_s, 3),
        "render_before_s": round(render_a, 2), "render_after_s": round(render_b, 2),
        "changed_px": int(diff.sum()), "dirty_rect": box, "outside_dirty_rect_px": outside,
        "fingerprint_before": ops.fingerprint(doc), "fingerprint_after": ops.fingerprint(out),
        "pngs": [str(p1), str(p2)],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
