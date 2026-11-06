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
import cli_util as CU
import plan_io
import render_util as RU
import tile_inc



def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("plan")
    ap.add_argument("--ops", required=True, help="编译产物 JSON（算子数组）")
    ap.add_argument("--rect", default=None, help="改画区域 x,y,w,h（缺省全图）")
    ap.add_argument("--mask", default=None, help="完整掩膜 JSON，优先于 --rect")
    ap.add_argument("--mask-file", default=None, help="掩膜 JSON 文件（多模态框选的产物）")
    ap.add_argument("--feather", type=float, default=32.0)
    ap.add_argument("--mask-mode", choices=("any", "all"), default="any",
                    help="any=笔触碰到掩膜即选中（默认）；all=整笔在掩膜内才选中")
    ap.add_argument("--no-check", action="store_true", help="跳过与整份重画的逐位对比")
    CU.add_common_args(ap, quiet=False)       # --gpu / --threads 与其他脚本同形（本脚本只输出报告，无需 --quiet）
    ap.add_argument("--out", default=str(CU.cache_dir() / "instruction.png"))
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
    p, cat, w, h = D.new_painter(doc, gpu=a.gpu)
    plan1, spec, region = RU.draw_full(p, cat, doc, w, h, gpu=a.gpu, nthreads=a.threads)
    p.composite()                      # 基线画布（真实场景里是上一次的合成结果）
    base_s = time.perf_counter() - t0

    touched = set()
    for sp in specs:
        if sp.get("layer") is None:
            touched.update(l["name"] for l in doc["layers"])
        else:
            touched.add(sp["layer"])

    t0 = time.perf_counter()
    sel, wt, sel_by_layer = [], [], {}
    for name in sorted(touched):                    # 按层取掩膜：只扫受影响的层
        s1, w1 = masks.weights(doc, mask, a.feather, layer=name, mode=a.mask_mode)
        if getattr(a, "hard_mask", False):
            w1 = [1.0] * len(s1)
        sel += s1
        wt += w1
        sel_by_layer[name] = s1
    out = ops.apply(doc, specs, selection=sel, weights=wt)
    plan2, spec2 = D.doc_to_plan(out, cat, base=plan1, only=touched)
    edit_s = time.perf_counter() - t0

    dirty = tile_inc.dirty_rects(doc, plan1, sel_by_layer, w, h)

    # draw_path / densify 会追加新笔触：它们不在掩膜选择里，脏矩形必须单独补上
    extra_by_layer = {}
    for j in range(len(doc["strokes"]), len(out["strokes"])):
        extra_by_layer.setdefault(out["strokes"][j]["layer"], []).append(j)
        touched.add(out["strokes"][j]["layer"])
    for name, idxs in extra_by_layer.items():
        ax0 = ay0 = 1e18
        ax1 = ay1 = -1e18
        for j in idxs:
            st = out["strokes"][j]
            pad = float(st["size"]) * 0.7 + 2.0      # 引擎真实外扩（见 tile_inc 注释）
            for pt in st["points"]:
                ax0 = min(ax0, pt[0] - pad); ax1 = max(ax1, pt[0] + pad)
                ay0 = min(ay0, pt[1] - pad); ay1 = max(ay1, pt[1] + pad)
        rect = (max(0, int(np.floor(ax0))), max(0, int(np.floor(ay0))),
                min(w - 1, int(np.ceil(ax1))), min(h - 1, int(np.ceil(ay1))))
        dirty.setdefault(name, []).append(rect)
    x0 = min(r[0] for rs in dirty.values() for r in rs) if dirty else 0
    y0 = min(r[1] for rs in dirty.values() for r in rs) if dirty else 0
    x1 = max(r[2] for rs in dirty.values() for r in rs) if dirty else w - 1
    y1 = max(r[3] for rs in dirty.values() for r in rs) if dirty else h - 1

    t0 = time.perf_counter()
    tile_inc.rebuild(p, plan2, spec2, dirty, lambda nm: region, gpu=a.gpu, nthreads=a.threads)
    arr = np.ascontiguousarray(p.composite())   # composite 结果就是 render_util.render_arr 的同一口径
    rebuild_s = time.perf_counter() - t0

    report = {"plan": Path(a.plan).name, "ops": specs, "selected": len(sel),
              "dirty_rect": [x0, y0, x1, y1], "touched_layers": sorted(touched),
              "load_validate_s": round(load_s, 3), "baseline_render_s": round(base_s, 2),
              "edit_s": round(edit_s, 3), "rebuild_s": round(rebuild_s, 3),
              "edit_to_pixels_s": round(load_s + edit_s + rebuild_s, 3)}

    if not a.no_check:
        q, _, _, _ = D.new_painter(doc, gpu=a.gpu)
        t0 = time.perf_counter()
        tile_inc.full(q, plan2, spec2, lambda nm: region, gpu=a.gpu, nthreads=a.threads)
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
