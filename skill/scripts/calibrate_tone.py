"""按参考亮度标定 tone_map 的 gain —— 不硬编码任何亮度常数。

标定输入只有两样：计划文件 + 参考图（或显式目标 luma）。脚本在渲染器上实测 luma，
二分求 gain，最后把解出的 gain 写回 ops JSON；同一个参考图重复跑得到同一个 gain。

用法:
    python scripts/calibrate_tone.py plan.json --target-image ref.png \
        --base-ops ops_erase_highlight.json --out tone_calibrated.json
    python scripts/calibrate_tone.py plan.json --target-luma 22.3 --out tone.json

参数:
    --base-ops   其它算子的 JSON（如 erase highlight），tone_map 会被替换成标定后的一个
    --target     目标亮度来源：--target-image 或 --target-luma，二选一
    --lo/--hi    gain 搜索区间（默认 0.2..3.0）
    --tol        亮度收敛阈值（默认 0.1）
    --iters      最大渲染次数（默认 12）
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
from PIL import Image

import cli_util as CU
import ops
import plan_io
import render_util as RU

# 渲染与亮度口径统一走 render_util（calibrate / rebuild_shadow_layer / 测试共用）
render_luma = RU.render_luma
target_luma_from_image = RU.target_luma_from_image


def _with_gain(specs, gain):
    out = [dict(s) for s in specs if s.get("op") != "tone_map"]
    out.append({"op": "tone_map", "gain": round(float(gain), 6)})
    return out


def calibrate(doc, target, specs=(), lo=0.2, hi=3.0, tol=0.1, iters=12, gpu=False, nthreads=8,
              log=None):
    """二分求 gain，使实测 luma 命中 target；返回 (gain, achieved_luma, renders)。"""
    base = ops.apply(doc, [dict(s) for s in specs], selection=None, weights=None) if specs else doc
    renders = 0

    def luma_at(g):
        nonlocal renders
        renders += 1
        cur = ops.apply(base, _with_gain([], g), selection=None, weights=None)
        v = render_luma(cur, gpu=gpu, nthreads=nthreads)
        if log:
            log("gain %.4f -> luma %.2f" % (g, v))
        return v

    l_lo, l_hi = luma_at(lo), luma_at(hi)
    if not (lo < hi) or l_lo == l_hi:
        raise ValueError("luma does not respond to gain in [%s, %s]" % (lo, hi))
    rising = l_hi > l_lo
    if (rising and target < l_lo) or (not rising and target > l_lo):
        raise ValueError("target %.2f outside reachable range [%.2f, %.2f]" % (target, l_lo, l_hi))
    a, b, va, vb = lo, hi, l_lo, l_hi
    best, best_err = (lo, l_lo), abs(l_lo - target)
    for _ in range(max(0, iters - 2)):
        m = (a * b) ** 0.5                      # 亮度对 gain 近似幂律，几何中点收敛更快
        vm = luma_at(m)
        if abs(vm - target) < best_err:
            best, best_err = (m, vm), abs(vm - target)
        if best_err <= tol:
            break
        if (vm < target) == rising:
            a, va = m, vm
        else:
            b, vb = m, vm
    return round(best[0], 4), round(best[1], 3), renders


def main():
    ap = argparse.ArgumentParser(description="按参考亮度标定 tone_map 的 gain（解写回 ops JSON）")
    ap.add_argument("plan")
    CU.add_target_args(ap)
    ap.add_argument("--base-ops", help="其它算子的 JSON（如 erase highlight），会被保留")
    ap.add_argument("--out", default="tone_calibrated.json")
    ap.add_argument("--lo", type=float, default=0.2, help="gain 搜索下界")
    ap.add_argument("--hi", type=float, default=3.0, help="gain 搜索上界")
    ap.add_argument("--tol", type=float, default=0.1, help="亮度收敛阈值")
    ap.add_argument("--iters", type=int, default=12, help="最多渲染次数")
    CU.add_common_args(ap)
    a = ap.parse_args()

    doc, w, h = plan_io.load_plan(a.plan)
    target = CU.resolve_target(a, RU.target_luma_from_image)
    specs = CU.load_json(a.base_ops) if a.base_ops else []
    gain, luma, renders = calibrate(doc, target, specs, a.lo, a.hi, a.tol, a.iters, a.gpu,
                                    a.threads, log=lambda s: CU.log_unless_quiet(a, s))
    out = [dict(s) for s in specs if s.get("op") != "tone_map"]
    out.append({"op": "tone_map", "gain": gain})
    CU.write_json(a.out, out)
    print(json.dumps({"plan": Path(a.plan).name, "target_luma": round(target, 2),
                      "solved_gain": gain, "achieved_luma": luma,
                      "abs_error": round(abs(luma - target), 3), "renders": renders,
                      "out": str(a.out)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
