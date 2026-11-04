"""一次算完：把若干 ops JSON 顺序作用到计划上并渲染出图（不需要掩膜/增量重建）。

用途：标定完 tone 之后直接出最终图，或把多个 ops 文件串起来预览，
不必再为了拍一张全图去跑 instruction_demo 的脏矩形流程。

用法:
    python scripts/render_plan.py plan.json --ops a.json --ops b.json --out final.png --gpu
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
from PIL import Image

import cli_util as CU
import ops
import plan_io
import render_util as RU


def load_specs(paths):
    """顺序拼接多个 ops JSON；后一个作用于前一个的结果。"""
    out = []
    for p in paths or ():
        out.extend(CU.load_json(p))
    return out


def render_doc_with_ops(doc, specs, gpu=False, nthreads=8):
    """作用算子后整份渲染，返回 (RGBA 数组, 作用后的 doc)。"""
    out = ops.apply(doc, specs, selection=None, weights=None) if specs else doc
    return RU.render_arr(out, gpu=gpu, nthreads=nthreads), out


def render_plan_with_ops(plan_path, specs, out_path, gpu=False, nthreads=8):
    doc, w, h = plan_io.load_plan(plan_path)
    arr, _ = render_doc_with_ops(doc, specs, gpu=gpu, nthreads=nthreads)
    Image.fromarray(np.ascontiguousarray(arr[..., :3])).save(out_path)
    return {"plan": Path(plan_path).name, "ops": len(specs), "out": str(out_path),
            "size": [int(w), int(h)], "luma": round(RU.luma_of(arr), 2)}


def main():
    ap = argparse.ArgumentParser(description="把 ops 顺序作用到计划上并渲染成图")
    ap.add_argument("plan")
    ap.add_argument("--ops", action="append", default=[], help="ops JSON，可重复给（按顺序作用）")
    ap.add_argument("--out", required=True)
    CU.add_common_args(ap, quiet=False)
    a = ap.parse_args()
    print(render_plan_with_ops(a.plan, load_specs(a.ops), a.out, gpu=a.gpu, nthreads=a.threads))


if __name__ == "__main__":
    main()
