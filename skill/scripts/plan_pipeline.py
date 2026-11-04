"""一条命令跑完：planner -> 报告(kra/psd/png + 统计 + luma) -> 按参考图标定 tone -> 出最终图。

planner 需要参考图与 regions npz，所以这里只负责"编排"：
把路径算好、命令行拼好、跑完读报告、再用标定出的 ops 渲染最终图并回写摘要。

用法:
    python scripts/plan_pipeline.py rose5-full.png regions2.npz --out demo \
        --target-image rose5-full.png --gpu
    python scripts/plan_pipeline.py ... --dry-run     # 只打印将执行的命令
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import cli_util as CU
import plan_io
import render_plan as RP
import render_util as RU


def paths_for(out_prefix, target_image):
    """派生所有产物路径（plan/report/tone ops/final png），集中一处，避免调用方各算一遍。"""
    base = str(out_prefix)
    return {
        "prefix": base,
        "plan": base + ".plan.json",
        "regions": base + ".regions.npz",
        "report": base + ".report.json",
        "tone": base + ".tone.json",
        "final": base + ".final.png",
        "target": target_image,
    }


def build_planner_cmd(p=(), python=None):
    """planner 命令行：位置参数(参考图/regions/输出前缀) + --plan/--report/--calibrate。"""
    python = python or sys.executable
    cmd = [python, "scripts/planner_8layer.py", p["reference"], p["regions"], p["prefix"],
           "--plan", p["plan"], "--report", p["report"]]
    if p.get("target"):
        cmd += ["--calibrate", p["target"]]
    return cmd


def merge_report(report, extra):
    """把最终出图的统计并回 planner 报告（不覆盖 planner 自己的字段）。"""
    out = dict(report)
    for k, v in (extra or {}).items():
        out.setdefault(k, v) if k in out else out.__setitem__(k, v)
    return out


def run(reference, regions, out_prefix, target_image=None, python=None, extra=(), dry_run=False,
        gpu=False, threads=8):
    p = paths_for(out_prefix, target_image)
    p["reference"], p["regions"] = str(reference), str(regions)
    cmd = build_planner_cmd(p, python)
    summary = {"cmd": cmd, "paths": p, "dry_run": bool(dry_run)}
    if dry_run:
        return summary
    subprocess.run(cmd, check=True, cwd=str(Path(__file__).resolve().parents[1]))
    rep = CU.load_json(p["report"])
    specs = CU.load_json(p["tone"]) if Path(p["tone"]).exists() else []
    doc, w, h = plan_io.load_plan(p["plan"])
    arr, _ = RP.render_doc_with_ops(doc, specs, gpu=gpu, nthreads=threads)
    from PIL import Image
    import numpy as np
    Image.fromarray(np.ascontiguousarray(arr[..., :3])).save(p["final"])
    merged = merge_report(rep, {"final_png": p["final"], "final_luma": round(RU.luma_of(arr), 2),
                                "tone_ops": p["tone"] if specs else None})
    CU.write_json(p["report"], merged)
    summary.update({"report": merged})
    return summary


def main():
    ap = argparse.ArgumentParser(description="planner -> 报告 -> 标定 -> 最终图")
    ap.add_argument("reference", help="参考图（planner 输入）")
    ap.add_argument("regions", help="regions npz")
    ap.add_argument("--out", required=True, help="输出前缀（不带扩展名）")
    ap.add_argument("--target-image", help="标定用的参考图（缺省不标定）")
    ap.add_argument("--dry-run", action="store_true", help="只打印将执行的命令")
    CU.add_common_args(ap)
    a = ap.parse_args()
    s = run(a.reference, a.regions, a.out, a.target_image, extra=(), dry_run=a.dry_run,
            gpu=a.gpu, threads=a.threads)
    print(json.dumps(s, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
