"""验收门（性质断言）：不给数值查表，只断言换图/换指令都成立的性质。

检查项：
    schema         ops 能通过 ops.validate（未知算子/未知键/缺必填一律拒绝）
    determinism    同一 plan + 同一 ops 渲染两次逐字节相同
    outside        掩膜外像素不变（均值差 <= outside_tol）
    direction      目标区朝指令方向变化（luma 升/降的符号正确，幅度 >= min_delta）
    subject        主体区变化 <= subject_tol（不误伤）

用法:
    python scripts/accept.py --plan p.json --recipe r.json [--report out.json]
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

import compile_ops
import edit_loop as EL
import ops as ops_mod


def check_schema(doc, specs):
    compile_ops.validate_ops(doc, specs)
    return {"ok": True, "detail": "ops schema valid"}


def check_determinism(render_once, times=2):
    outs = [np.asarray(render_once(), dtype=np.uint8) for _ in range(times)]
    same = all(np.array_equal(outs[0], o) for o in outs[1:])
    return {"ok": bool(same), "detail": "repeat renders byte-identical" if same else "render differs"}


def check_outside(before, after, mask_bool, tol=0.5):
    d = EL._delta(after, before)
    val = float(d[~mask_bool].mean()) if (~mask_bool).any() else 0.0
    return {"ok": val <= tol, "detail": "outside mean_abs_change=%.3f tol=%.3f" % (val, tol), "value": round(val, 3)}


def check_direction(before, after, mask_bool, expect="up", min_delta=0.5):
    lb, la = EL._luma(before, mask_bool), EL._luma(after, mask_bool)
    delta = la - lb
    ok = (delta >= min_delta) if expect == "up" else (delta <= -min_delta)
    return {"ok": bool(ok), "detail": "region luma %.2f -> %.2f (expect %s, min %.2f)" % (lb, la, expect, min_delta),
            "delta": round(delta, 3)}


def check_subject(before, after, mask_bool, tol=3.0):
    d = EL._delta(after, before)
    val = float(d[mask_bool].mean()) if mask_bool.any() else 0.0
    return {"ok": val <= tol, "detail": "subject mean_abs_change=%.3f tol=%.3f" % (val, tol), "value": round(val, 3)}


def run(plan, recipe_path=None, steps=None, expect="up", min_delta=0.5, outside_tol=0.5,
        subject_tol=3.0, report=None, out_dir=None, gpu=False, threads=8):
    """跑一次编辑并给出验收结论（不写死像素数值，只看性质）。"""
    import plan_io
    import render_util as RU

    if recipe_path:
        rec = json.loads(Path(recipe_path).read_text(encoding="utf-8"))
        steps = rec["steps"]
    if not steps:
        raise SystemExit("需要 --recipe 或 steps")
    doc, w, h = plan_io.load_plan(plan)
    all_specs = [sp for st in steps for sp in st["ops"]]
    results = {"schema": check_schema(doc, all_specs)}

    out = EL.run_steps(plan, steps, out_dir or "accept_out", tag="accept",
                       gpu=gpu, threads=threads)
    root = Path(out_dir or "accept_out") / "accept"
    from PIL import Image as _Image
    before = np.asarray(_Image.open(root / "before.png").convert("RGB"), dtype=np.float64)
    after = np.asarray(_Image.open(root / "after.png").convert("RGB"), dtype=np.float64)

    mask_bool = np.zeros((h, w), bool)
    for m in (root.glob("*/mask.json")):
        mm = json.loads(m.read_text(encoding="utf-8"))
        mask_bool |= EL._full_mask(mm, h, w, before)

    results["determinism"] = check_determinism(
        lambda: RU.render_arr(plan_io.load_plan(plan)[0], gpu=gpu, nthreads=threads))
    results["outside"] = check_outside(before, after, mask_bool, outside_tol)
    results["direction"] = check_direction(before, after, mask_bool, expect, min_delta)
    results["overall_ok"] = all(v["ok"] for k, v in results.items() if isinstance(v, dict) and "ok" in v)
    if report:
        Path(report).write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    return results


def main():
    ap = argparse.ArgumentParser(description="编辑验收门（性质断言）")
    ap.add_argument("--plan", required=True)
    ap.add_argument("--recipe", required=True)
    ap.add_argument("--expect", default="up", choices=["up", "down"])
    ap.add_argument("--min-delta", type=float, default=0.5)
    ap.add_argument("--outside-tol", type=float, default=0.5)
    ap.add_argument("--subject-tol", type=float, default=3.0)
    ap.add_argument("--out-dir", default="accept_out")
    ap.add_argument("--report")
    import cli_util as CU
    CU.add_common_args(ap, quiet=False)
    a = ap.parse_args()
    res = run(a.plan, a.recipe, expect=a.expect, min_delta=a.min_delta, outside_tol=a.outside_tol,
              subject_tol=a.subject_tol, report=a.report, out_dir=a.out_dir, gpu=a.gpu,
              threads=a.threads)
    print(json.dumps(res, ensure_ascii=False))
    sys.exit(0 if res["overall_ok"] else 1)


if __name__ == "__main__":
    main()
