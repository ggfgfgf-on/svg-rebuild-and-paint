"""语义改图闭环：一次调用走完 掩膜 → 应用 → 渲染 → 度量 → 对照图。

模型只负责两件事：给视觉提示（种子点/框）和写 ops；机械部分（区域生长、渲染、度量、
对照图、落盘）全部自动。每次调用是一个"迭代"，产物落在 --out-dir/<tag>/ 下，便于横向比较。

单步（最常见）:
    python scripts/edit_loop.py plan.json --ops white.json --seeds "620,150;600,240" \
        --box 420,60,735,470 --out-dir lab/white --tag it2 --gpu
    # 也可以直接把 ops 写在命令行里，不落文件（"指令→ops 自动产出"用这条）
    python scripts/edit_loop.py plan.json --ops-json '[{"op":"tone_map","gain":1.1}]' \
        --seeds "620,150" --out-dir lab/x --tag it3 --gpu

多步（一个指令里有多个区域/多组操作，按顺序作用，后一步看到的是前一步的结果）:
    python scripts/edit_loop.py plan.json --recipe recipe.json --out-dir lab/night --tag r1 --gpu

recipe.json:
    {"instruction": "把背景换成暖夜色并把叶子高光抬起来",
     "steps": [{"name": "bg", "mask_file": "mask_bg.json", "mask_mode": "all",
                "ops": [{"op": "set_layer_hue", "layer": "bed", "hue": 18}]},
               {"name": "leaf", "seeds": [[520, 700]], "box": [300, 470, 860, 900],
                "ops": [{"op": "tone_map", "layer": "highlight", "gain": 1.6}]}]}
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


def _delta(a, b):
    """逐像素平均绝对差。必须转 float：uint8 相减会下溢回绕（实测把 10 的变化报成 155）。"""
    return np.abs(a.astype(np.float64) - b.astype(np.float64)).mean(2)


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


def _build_mask(base, step):
    """一步的掩膜：优先用现成掩膜文件，否则从视觉提示（种子点+框）生长。"""
    if step.get("mask_file"):
        return masks.from_json(json.loads(Path(step["mask_file"]).read_text(encoding="utf-8")))
    seeds = [tuple(s) for s in step.get("seeds") or ()]
    if not seeds:
        raise SystemExit("step %r 既没有 mask_file 也没有 seeds" % step.get("name"))
    close = VM.seeds_mask(base, seeds, step.get("tolerance", 80), step.get("hue_tol", 16.0))
    box = step.get("box")
    if box:
        clip = np.zeros_like(close)
        clip[box[1]:box[3], box[0]:box[2]] = True
        close &= clip
    m, _ = VM.grow_mask(seeds, close, step.get("grow", 4))
    return masks.from_bool(m)


def _full_mask(mask, h, w):
    b = masks._decode_bits(mask)
    full = np.zeros((h, w), bool)
    full[mask["y"]:mask["y"] + b.shape[0], mask["x"]:mask["x"] + b.shape[1]] = b
    return full


def run_steps(plan, steps, out_dir, tag="it", ref=None, gpu=False, threads=8, crop_pad=24,
              instruction=None):
    doc, w, h = plan_io.load_plan(plan)
    for st in steps:
        compile_ops.validate_ops(doc, st["ops"])

    root = Path(out_dir) / tag
    root.mkdir(parents=True, exist_ok=True)
    base_arr = RU.render_arr(doc, gpu=gpu, nthreads=threads)[..., :3]
    Image.fromarray(base_arr.astype(np.uint8)).save(root / "before.png")

    step_metrics, crop_box = [], None
    for i, st in enumerate(steps):
        mask = _build_mask(base_arr, st)
        full = _full_mask(mask, h, w)
        out, nsel = _apply_masked(doc, st["ops"], mask, st.get("feather", 0.0),
                                  st.get("mask_mode", "any"))
        arr = RU.render_arr(out, gpu=gpu, nthreads=threads)[..., :3]
        ys, xs = np.nonzero(full)
        bbox = [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1] if len(xs) else [0, 0, w, h]
        d = root / ("%d_%s" % (i + 1, st.get("name", "step")))
        d.mkdir(parents=True, exist_ok=True)
        Image.fromarray(arr.astype(np.uint8)).save(d / "after.png")
        VM.overlay(base_arr, full, str(d / "mask_overlay.png"))
        (d / "mask.json").write_text(json.dumps(masks.to_json(mask), ensure_ascii=False), encoding="utf-8")
        (d / "ops.json").write_text(json.dumps(st["ops"], ensure_ascii=False, indent=1), encoding="utf-8")
        delta = _delta(arr, base_arr)
        step_metrics.append({
            "step": i + 1, "name": st.get("name", "step"), "strokes_selected": int(nsel),
            "mask": {"pixels": int(full.sum()), "bbox": bbox, "share": round(float(full.mean()), 4)},
            "inside": {"before_rgb": _mean(base_arr, full), "after_rgb": _mean(arr, full),
                       "before_luma": _luma(base_arr, full), "after_luma": _luma(arr, full),
                       "mean_abs_change": round(float(delta[full].mean()), 2)},
            "outside_mean_abs_change": round(float(delta[~full].mean()), 2),
            "sheet": _sheet(base_arr, arr, (max(0, bbox[0] - crop_pad), max(0, bbox[1] - crop_pad),
                                            min(w, bbox[2] + crop_pad), min(h, bbox[3] + crop_pad)),
                            d / "sheet.jpg"),
        })
        if crop_box is None:
            crop_box = bbox
        else:
            crop_box = [min(crop_box[0], bbox[0]), min(crop_box[1], bbox[1]),
                        max(crop_box[2], bbox[2]), max(crop_box[3], bbox[3])]
        doc, base_arr = out, arr

    Image.fromarray(base_arr.astype(np.uint8)).save(root / "after.png")
    cb = crop_box or [0, 0, w, h]
    summary = {"tag": tag, "plan": str(plan), "instruction": instruction, "steps": len(steps),
               "step_metrics": step_metrics,
               "luma_whole": [_luma(RU.render_arr(plan_io.load_plan(plan)[0], gpu=gpu, nthreads=threads)[..., :3]),
                              _luma(base_arr)],
               "artifacts": {"dir": str(root),
                             "sheet": _sheet(Image.open(root / "before.png").convert("RGB") and
                                             np.asarray(Image.open(root / "before.png").convert("RGB")),
                                             base_arr,
                                             (max(0, cb[0] - crop_pad), max(0, cb[1] - crop_pad),
                                              min(w, cb[2] + crop_pad), min(h, cb[3] + crop_pad)),
                                             root / "sheet.jpg", labels=("original", "final"))}}
    if ref:
        R = Image.open(ref).convert("RGB")
        rarr = np.asarray(R.resize((w, h), Image.LANCZOS), dtype=np.float64)
        before = np.asarray(Image.open(root / "before.png").convert("RGB"), dtype=np.float64)
        summary["vs_ref"] = {"before": round(float(np.abs(before - rarr).mean()), 2),
                             "after": round(float(np.abs(base_arr - rarr).mean()), 2)}
    (root / "metrics.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    return summary


def run(plan, specs, out_dir, tag="it", seeds=None, box=None, tolerance=80, hue_tol=16.0,
        grow=4, mask_file=None, mask_mode="any", feather=0.0, ref=None, gpu=False, threads=8,
        instruction=None):
    if not mask_file and not seeds:
        raise SystemExit("需要 --seeds（视觉提示）或 --mask-file")
    step = {"name": tag, "ops": specs, "mask_file": mask_file, "mask_mode": mask_mode,
            "feather": feather, "tolerance": tolerance, "hue_tol": hue_tol, "grow": grow}
    if seeds:
        step["seeds"] = [list(s) for s in seeds]
    if box:
        step["box"] = list(box)
    return run_steps(plan, [step], out_dir, tag=tag, ref=ref, gpu=gpu, threads=threads,
                     instruction=instruction)


def main():
    ap = argparse.ArgumentParser(description="语义改图闭环：掩膜→应用→渲染→度量→对照图")
    ap.add_argument("plan")
    ap.add_argument("--ops", help="ops JSON（文件）")
    ap.add_argument("--ops-json", help="ops JSON（直接写字符串，不落文件；传 - 表示从 stdin 读）")
    ap.add_argument("--recipe", help="多步 recipe JSON（steps 数组）")
    ap.add_argument("--instruction", help="原始指令，仅记录进 metrics")
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
    if a.recipe:
        rec = CU.load_json(a.recipe)
        res = run_steps(a.plan, rec["steps"], a.out_dir, tag=a.tag, ref=a.ref, gpu=a.gpu,
                        threads=a.threads, instruction=rec.get("instruction") or a.instruction)
    else:
        if not (a.ops or a.ops_json):
            raise SystemExit("需要 --ops / --ops-json / --recipe 之一")
        if a.ops:
            specs = CU.load_json(a.ops)
        elif a.ops_json == "-":
            specs = json.loads(sys.stdin.read())     # 从标准输入喂 ops，绕开各 shell 的引号地狱
        else:
            specs = json.loads(a.ops_json)
        seeds = [tuple(int(v) for v in s.split(",")) for s in a.seeds.split(";")] if a.seeds else None
        box = tuple(int(v) for v in a.box.split(",")) if a.box else None
        res = run(a.plan, specs, a.out_dir, tag=a.tag, seeds=seeds, box=box, tolerance=a.tolerance,
                  hue_tol=a.hue_tol, grow=a.grow, mask_file=a.mask_file, mask_mode=a.mask_mode,
                  feather=a.feather, ref=a.ref, gpu=a.gpu, threads=a.threads,
                  instruction=a.instruction)
    print(json.dumps(res, ensure_ascii=False))


if __name__ == "__main__":
    main()
