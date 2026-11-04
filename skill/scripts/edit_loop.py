"""语义改图闭环：一次调用走完 掩膜 → 应用 → 渲染 → 度量 → 对照图。

模型只负责两件事：给视觉提示（种子点/框）和写 ops；机械部分（区域生长、渲染、度量、
对照图、落盘）全部自动。每次调用是一个"迭代"，产物落在 --out-dir/<tag>/ 下，便于横向比较。

用法:
    python scripts/edit_loop.py plan.json --ops white.json --seeds "620,150;600,240" \
        --box 420,60,735,470 --ref photo.png --out-dir lab/white --tag it2 --gpu
    python scripts/edit_loop.py plan.json --ops white.json --mask-file mask.json --out-dir lab/x --tag a
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent))

import cli_util as CU
import compile_ops
import masks
import ops as ops_mod
import plan_io
import render_util as RU
import visual_mask as VM


def _touched(doc, specs):
    out = set()
    for sp in specs:
        if sp.get("layer") is None:
            out.update(l["name"] for l in doc["layers"])
        else:
            out.add(sp["layer"])
    return out


def _apply_masked(doc, specs, mask, feather=0.0, mask_mode="any"):
    """按层取掩膜权重后应用算子（与 instruction_demo 同一套语义）。"""
    sel, wt = [], []
    for name in sorted(_touched(doc, specs)):
        s1, w1 = masks.weights(doc, mask, feather, layer=name, mode=mask_mode)
        sel += s1
        wt += w1
    return ops_mod.apply(doc, specs, selection=sel, weights=wt), len(sel)


def _mean(x, m):
    return tuple(int(v) for v in x[m].mean(0))


def _luma(x, m=None):
    a = x if m is None else x[m]
    return round(float((a @ [0.2126, 0.7152, 0.0722]).mean()), 2)


def _sheet(base, edited, box, path, labels=("before", "after"), h=340):
    x0, y0, x1, y1 = box
    ims = []
    for label, arr in zip(labels, (base, edited)):
        im = Image.fromarray(arr.astype(np.uint8)).crop((x0, y0, x1, y1))
        im = im.resize((max(1, int(im.width * h / im.height)), h), Image.LANCZOS)
        ims.append((label, im))
    out = Image.new("RGB", (sum(i.width for _, i in ims) + 8 * (len(ims) - 1), h + 18), (18, 18, 18))
    d = ImageDraw.Draw(out)
    x = 0
    for label, im in ims:
        out.paste(im, (x, 18))
        d.text((x + 3, 3), label, fill=(235, 235, 235))
        x += im.width + 8
    out.save(path, "JPEG", quality=92)
    return str(path)


def run(plan, specs, out_dir, tag="it", seeds=None, box=None, tolerance=80, hue_tol=16.0,
        grow=4, mask_file=None, mask_mode="any", feather=0.0, ref=None, gpu=False,
        threads=8, crop_pad=24):
    if not mask_file and not seeds:
        raise SystemExit("需要 --seeds（视觉提示）或 --mask-file")
    doc, w, h = plan_io.load_plan(plan)
    compile_ops.validate_ops(doc, specs)

    base = RU.render_arr(doc, gpu=gpu, nthreads=threads)[..., :3]

    if mask_file:
        mask = masks.from_json(json.loads(Path(mask_file).read_text(encoding="utf-8")))
        overlay = None
    else:
        close = VM.seeds_mask(base, seeds, tolerance, hue_tol)
        if box:
            clip = np.zeros_like(close)
            clip[box[1]:box[3], box[0]:box[2]] = True
            close &= clip
        m, _ = VM.grow_mask(seeds, close, grow)
        mask = masks.from_bool(m)
        overlay = None

    out = _apply_masked(doc, specs, mask, feather, mask_mode)[0]
    edited = RU.render_arr(out, gpu=gpu, nthreads=threads)[..., :3]

    b = masks._decode_bits(mask)
    full = np.zeros((h, w), bool)
    full[mask["y"]:mask["y"] + b.shape[0], mask["x"]:mask["x"] + b.shape[1]] = b

    ys, xs = np.nonzero(full)
    box_all = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
    cx0 = max(0, box_all[0] - crop_pad); cy0 = max(0, box_all[1] - crop_pad)
    cx1 = min(w, box_all[2] + crop_pad); cy1 = min(h, box_all[3] + crop_pad)

    d = Path(out_dir) / tag
    d.mkdir(parents=True, exist_ok=True)
    Image.fromarray(base.astype(np.uint8)).save(d / "before.png")
    Image.fromarray(edited.astype(np.uint8)).save(d / "after.png")
    (d / "mask.json").write_text(json.dumps(masks.to_json(mask), ensure_ascii=False), encoding="utf-8")
    VM.overlay(base, full, str(d / "mask_overlay.png"))
    (d / "ops.json").write_text(json.dumps(specs, ensure_ascii=False, indent=1), encoding="utf-8")

    delta = np.abs(edited - base).mean(2)
    m = {"tag": tag, "plan": str(plan), "ops": len(specs), "mask_mode": mask_mode,
         "mask": {"pixels": int(full.sum()), "bbox": list(box_all), "share": round(float(full.mean()), 4)},
         "inside": {"before_rgb": _mean(base, full), "after_rgb": _mean(edited, full),
                    "before_luma": _luma(base, full), "after_luma": _luma(edited, full),
                    "mean_abs_change": round(float(delta[full].mean()), 2)},
         "outside": {"mean_abs_change": round(float(delta[~full].mean()), 2)},
         "luma_whole": [_luma(base), _luma(edited)],
         "artifacts": {"dir": str(d)}}
    if ref:
        R = Image.open(ref).convert("RGB")
        rarr = np.asarray(R.resize((w, h), Image.LANCZOS), dtype=np.float64)
        m["vs_ref"] = {"before": round(float(np.abs(base - rarr).mean()), 2),
                       "after": round(float(np.abs(edited - rarr).mean()), 2)}
    m["artifacts"]["sheet"] = _sheet(base, edited, (cx0, cy0, cx1, cy1), d / "sheet.jpg")
    (d / "metrics.json").write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")
    return m


def main():
    ap = argparse.ArgumentParser(description="语义改图闭环：掩膜→应用→渲染→度量→对照图")
    ap.add_argument("plan")
    ap.add_argument("--ops", required=True, help="ops JSON")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--tag", default="it")
    ap.add_argument("--seeds", help="视觉提示：x,y;x,y;...（在基线渲染图上给点）")
    ap.add_argument("--box", help="x0,y0,x1,y1 硬约束")
    ap.add_argument("--tolerance", type=int, default=80, help="明度容差")
    ap.add_argument("--hue-tol", type=float, default=16.0)
    ap.add_argument("--grow", type=int, default=4)
    ap.add_argument("--mask-file", help="直接用已有 raster 掩膜（跳过视觉提示）")
    ap.add_argument("--mask-mode", default="any", choices=["any", "all"])
    ap.add_argument("--feather", type=float, default=0.0)
    ap.add_argument("--ref", help="参考图：额外报告 before/after 与它的平均差")
    CU.add_common_args(ap, quiet=False)
    a = ap.parse_args()
    seeds = [tuple(int(v) for v in s.split(",")) for s in a.seeds.split(";")] if a.seeds else None
    box = tuple(int(v) for v in a.box.split(",")) if a.box else None
    res = run(a.plan, CU.load_json(a.ops), a.out_dir, tag=a.tag, seeds=seeds, box=box,
              tolerance=a.tolerance, hue_tol=a.hue_tol, grow=a.grow, mask_file=a.mask_file,
              mask_mode=a.mask_mode, feather=a.feather, ref=a.ref, gpu=a.gpu, threads=a.threads)
    print(json.dumps(res, ensure_ascii=False))


if __name__ == "__main__":
    main()
