"""改画端到端：指令编译产物（ops.json）+ 掩膜 -> 编辑 -> 只重算脏矩形。

    python instruction_demo.py <plan.json> --ops ops.json --rect "x,y,w,h" [--feather 40]
                                [--out <png>] [--no-check]

流程与真实运行时一致：
    1. 读计划、读编译好的 ops.json（模型产物，已落盘）
    2. schema 校验（未知算子/未知层/未知笔尖一律拒绝）
    3. 掩膜选笔触（羽化权重）
    4. 算子执行 -> 新计划
    5. 只重算脏矩形（补齐改过的层），与「整份重画」逐位对比
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
import ops_incremental_demo as D
import plan_io
import tile_inc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("plan")
    ap.add_argument("--ops", required=True, help="编译产物 JSON（算子数组）")
    ap.add_argument("--rect", default=None, help="改画区域 x,y,w,h（缺省全图）")
    ap.add_argument("--mask", default=None, help="完整掩膜 JSON，优先于 --rect")
    ap.add_argument("--mask-file", default=None, help="掩膜 JSON 文件（多模态框选的产物）")
    ap.add_argument("--feather", type=float, default=32.0)
    ap.add_argument("--gpu", action="store_true")
    ap.add_argument("--no-check", action="store_true", help="跳过与整份重画的逐位对比")
    ap.add_argument("--out", default=str(Path.home() / ".cache" / "ops_demo" / "instruction.png"))
    a = ap.parse_args()

    t0 = time.perf_counter()
    doc, w, h = plan_io.load_plan(a.plan)
    specs = json.loads(Path(a.ops).read_text(encoding="utf-8"))
    compile_ops.validate_ops(doc, specs)
    load_s = time.perf_counter() - t0

    if a.mask_file:
        mask = masks.from_json(json.loads(Path(a.mask_file).read_text(encoding="utf-8")))
    elif a.mask:
        mask = masks.from_json(json.loads(a.mask))
    elif a.rect:
        x, y, rw, rh = [float(v) for v in a.rect.split(",")]
        mask = masks.rect(x, y, rw, rh)
    else:
        mask = masks.rect(0, 0, w, h)

    t0 = time.perf_counter()
    p, cat, w, h = D.new_painter(doc)
    plan1, spec = D.doc_to_plan(doc, cat)
    region = np.zeros((h, w), np.int32)
    tile_inc.full(p, plan1, spec, lambda nm: region, gpu=a.gpu, nthreads=8)
    p.composite()                      # 基线画布（真实场景里是上一次的合成结果）
    base_s = time.perf_counter() - t0

    touched = set()
    for sp in specs:
        if sp.get("layer") is None:
            touched.update(l["name"] for l in doc["layers"])
        else:
            touched.add(sp["layer"])

    t0 = time.perf_counter()
    sel, wt = [], []
    for name in sorted(touched):                    # 按层取掩膜：只扫受影响的层
        s1, w1 = masks.weights(doc, mask, a.feather, layer=name)
        sel += s1
        wt += w1
    out = ops.apply(doc, specs, selection=sel, weights=wt)
    plan2, spec2 = D.doc_to_plan(out, cat, base=plan1, only=touched)
    edit_s = time.perf_counter() - t0

    box = masks.bbox(doc, sel) or (0.0, 0.0, float(w - 1), float(h - 1))
    x0, y0, x1, y1 = [int(round(v)) for v in box]
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(w - 1, x1), min(h - 1, y1)
    dirty = {name: [(x0, y0, x1, y1)] for name in touched}

    t0 = time.perf_counter()
    tile_inc.rebuild(p, plan2, spec2, dirty, lambda nm: region, gpu=a.gpu, nthreads=8)
    arr = np.ascontiguousarray(p.composite())
    rebuild_s = time.perf_counter() - t0

    report = {"plan": Path(a.plan).name, "ops": specs, "selected": len(sel),
              "dirty_rect": [x0, y0, x1, y1], "touched_layers": sorted(touched),
              "load_validate_s": round(load_s, 3), "baseline_render_s": round(base_s, 2),
              "edit_s": round(edit_s, 3), "rebuild_s": round(rebuild_s, 3),
              "edit_to_pixels_s": round(load_s + edit_s + rebuild_s, 3)}

    if not a.no_check:
        q, _, _, _ = D.new_painter(doc)
        t0 = time.perf_counter()
        tile_inc.full(q, plan2, spec2, lambda nm: region, gpu=a.gpu, nthreads=8)
        ref = np.ascontiguousarray(q.composite())
        q.close()
        report["full_render_after_s"] = round(time.perf_counter() - t0, 2)
        report["incremental_vs_full_diff_px"] = int(np.any(arr != ref, axis=2).sum())

    out_path = Path(a.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    from PIL import Image
    Image.fromarray(arr, "RGBA").save(out_path)
    report["png"] = str(out_path)
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
