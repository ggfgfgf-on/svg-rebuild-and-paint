"""参数候选实验室：把一组 ops 候选项各渲染一张，拼成带标签的对照图 + 指标。

用途：让"看图的一方"（当前会话模型或人）在候选之间做选择，而不是靠猜默认值。
产出 sheet.png（带序号+名字）+ metrics.json（亮度、可选参考图平均差），选择结果另行记录。

用法:
    python scripts/variant_lab.py plan.json --variants variants.json --out lab/ [--base-ops a.json] [--ref ref.png]
variants.json: [{"name": "drop", "ops": []}, {"name": "green", "ops": [...]}]
"""
import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

import cli_util as CU
import plan_io
import render_plan as RP
import render_util as RU


def sheet(images, labels, cols=3, cell=300, pad=8):
    """拼带标签的对照图（每格上方写序号+名字）。"""
    if not images:
        raise ValueError("no images")
    rows = (len(images) + cols - 1) // cols
    W = cols * cell + (cols + 1) * pad
    H = rows * (cell + 22) + (rows + 1) * pad
    out = Image.new("RGB", (W, H), (18, 18, 18))
    d = ImageDraw.Draw(out)
    for i, (im, name) in enumerate(zip(images, labels)):
        r, c = divmod(i, cols)
        x = pad + c * (cell + pad)
        y = pad + r * (cell + 22 + pad)
        thumb = im.copy()
        thumb.thumbnail((cell, cell), Image.LANCZOS)
        out.paste(thumb, (x + (cell - thumb.width) // 2, y + 22))
        d.text((x + 2, y + 4), "%d %s" % (i + 1, name), fill=(235, 235, 235))
    return out


def run(plan_path, variants, out_dir, base_ops=(), ref=None, gpu=False, threads=8, cols=3,
        cell=300):
    doc, w, h = plan_io.load_plan(plan_path)
    base = list(base_ops)
    ref_arr = None
    if ref:
        r = Image.open(ref).convert("RGB")
        ref_arr = np.asarray(r.resize((w, h), Image.LANCZOS), dtype=np.float64)
    images, labels, metrics = [], [], []
    for v in variants:
        name = v.get("name") or ("v%d" % (len(labels) + 1))
        specs = base + list(v.get("ops") or [])
        arr, _ = RP.render_doc_with_ops(doc, specs, gpu=gpu, nthreads=threads)
        rgb = np.ascontiguousarray(arr[..., :3])
        images.append(Image.fromarray(rgb))
        labels.append(name)
        m = {"variant": name, "ops": specs, "luma": round(RU.luma_of(rgb), 2)}
        if ref_arr is not None:
            m["mean_abs_vs_ref"] = round(float(np.abs(rgb.astype(np.float64) - ref_arr).mean()), 2)
        metrics.append(m)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    sh = sheet(images, labels, cols=cols, cell=cell)
    sh.save(out_dir / "sheet.png")
    CU.write_json(out_dir / "metrics.json", metrics)
    return {"sheet": str(out_dir / "sheet.png"), "metrics": metrics}


def main():
    ap = argparse.ArgumentParser(description="渲染多个 ops 候选并拼对照图")
    ap.add_argument("plan")
    ap.add_argument("--variants", required=True, help="候选 JSON（[{name, ops}]）")
    ap.add_argument("--base-ops", help="所有候选共用的 ops JSON")
    ap.add_argument("--ref", help="参考图（给 mean_abs）")
    ap.add_argument("--out", required=True, help="输出目录")
    ap.add_argument("--cols", type=int, default=3)
    ap.add_argument("--cell", type=int, default=300)
    CU.add_common_args(ap, quiet=False)
    a = ap.parse_args()
    res = run(a.plan, CU.load_json(a.variants), a.out,
              base_ops=(CU.load_json(a.base_ops) if a.base_ops else ()),
              ref=a.ref, gpu=a.gpu, threads=a.threads, cols=a.cols, cell=a.cell)
    print(json.dumps(res, ensure_ascii=False))


if __name__ == "__main__":
    main()
