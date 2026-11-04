"""区域重规划：只在一块掩膜/框里按参考图重新生成笔触，再按图层语义合并回计划。

这是"文本→笔触"的工程缺口：扩散模型/参考图给出像素，planner 给出笔触，
但此前 planner 只能整幅跑。现在可以只重画局部，其余笔触逐字节不动。

用法:
    python scripts/planner_region.py --plan base.plan.json --ref photo.png --regions r.npz \
        --mask mask_region.json --out merged.plan.json --layers band,highlight [--report r.json]
    # 不给 --ref 时用 --strokes 直接提供一份"新笔触计划"（例如人类/模型手工生成的）
"""
import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import cli_util as CU
import masks
import plan_io


def _load_mask(path):
    return masks.from_json(json.loads(Path(path).read_text(encoding="utf-8")))


def stroke_in_mask(mask, stroke, mode="center", thr=0.5):
    """笔触是否算"在区域内"：center=采样点均值，any=任一点，all=全部点。"""
    from masks import stroke_weight, _pts
    if mode == "any":
        return stroke_weight(mask, stroke, 0.0, mode="any") >= thr
    if mode == "all":
        return stroke_weight(mask, stroke, 0.0, mode="all") >= thr
    pts = _pts(stroke)
    if len(pts) == 0:
        return False
    w = masks.raster_weight(mask, pts) if mask.get("type") == "raster" else \
        [stroke_weight(mask, {**stroke, "points": [[p[0], p[1], 1.0]]}, 0.0) for p in pts]
    return float(sum(w) / max(1, len(w))) >= thr


def merge(base, new, mask, layers, mode="center", thr=0.5):
    """保留区域外原笔触，用区域内的新笔触替换同层旧笔触。"""
    layers = set(layers)
    kept, dropped = [], 0
    for st in base["strokes"]:
        if st["layer"] in layers and stroke_in_mask(mask, st, mode, thr):
            dropped += 1
            continue
        kept.append(st)
    added, skipped = [], 0
    for st in new["strokes"]:
        if st["layer"] not in layers:
            skipped += 1
            continue
        if stroke_in_mask(mask, st, mode, thr):
            added.append(st)
    out = dict(base)
    out["strokes"] = kept + added
    out["count"] = len(out["strokes"])
    out["subject_layers"] = base.get("subject_layers") or sorted({s["layer"] for s in out["strokes"]})
    return out, {"dropped": dropped, "added": len(added), "skipped_other_layers": skipped}


def run(plan_path, out_path, mask_path, layers=("band", "highlight"), ref=None, regions=None,
        strokes_plan=None, style_file=None, highlight_mode="keep", shadow_mode="drop",
        mode="center", thr=0.5, gpu=False, threads=8, report=None):
    base_doc, w, h = plan_io.load_plan(plan_path)
    mask = _load_mask(mask_path)

    if strokes_plan:
        new_doc, _, _ = plan_io.load_plan(strokes_plan)
    elif ref:
        tmp = Path(tempfile.mkdtemp(prefix="region_")) / "region.plan.json"
        cmd = [sys.executable, str(Path(__file__).with_name("planner_8layer.py")),
               str(ref), str(regions), str(tmp.with_suffix("")), "--plan", str(tmp),
               "--highlight-mode", highlight_mode, "--shadow-mode", shadow_mode,
               "--threads", str(threads)]
        if style_file:
            cmd += ["--style-file", str(style_file)]
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        new_doc, _, _ = plan_io.load_plan(tmp)
    else:
        raise SystemExit("需要 --ref（用参考图重画）或 --strokes（已有新笔触计划）")

    merged, stats = merge(base_doc, new_doc, mask, layers, mode, thr)
    blends = {l["name"]: l.get("blend", "normal") for l in base_doc.get("layers") or []}
    plan_io.dump_plan(out_path, w, h, merged["strokes"], layer_blends=blends,
                      regions=merged.get("regions"), subject_layers=merged.get("subject_layers"))
    res = {"plan": str(plan_path), "out": str(out_path), "layers": list(layers),
           "mask": mask_path, "mode": mode, "thr": thr, **stats,
           "strokes_before": len(base_doc["strokes"]), "strokes_after": len(merged["strokes"])}
    if report:
        Path(report).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return res


def main():
    ap = argparse.ArgumentParser(description="区域重规划并合并回计划")
    ap.add_argument("--plan", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--mask", required=True, help="区域掩膜 JSON")
    ap.add_argument("--ref", help="参考图（交给 planner_8layer 重画整幅，再按区域裁）")
    ap.add_argument("--regions", help="planner 需要的 regions.npz")
    ap.add_argument("--strokes", help="直接给一份新笔触计划（跳过 planner）")
    ap.add_argument("--layers", default="band,highlight")
    ap.add_argument("--style-file")
    ap.add_argument("--highlight-mode", default="keep")
    ap.add_argument("--shadow-mode", default="drop")
    ap.add_argument("--mode", default="center", choices=["center", "any", "all"])
    ap.add_argument("--thr", type=float, default=0.5)
    ap.add_argument("--report")
    CU.add_common_args(ap, quiet=False)
    a = ap.parse_args()
    print(json.dumps(run(a.plan, a.out, a.mask, [x for x in a.layers.split(",") if x], a.ref,
                         a.regions, a.strokes, a.style_file, a.highlight_mode, a.shadow_mode,
                         a.mode, a.thr, a.gpu, a.threads, a.report), ensure_ascii=False))


if __name__ == "__main__":
    main()
