"""统一入口：编译 → 校验 → 编辑 → 增量渲染，可选直接出 30fps 视频。

    # 1) 用模型编译并立刻出图（需要 provider key；没有就走 --ops 手动路径）
    python paint_cli.py edit plan.json --instruction "把花改成蓝色" --provider deepseek --out a.png

    # 2) 用已落盘的编译产物（agent 编译，运行时零网络）
    python paint_cli.py edit plan.json --ops examples/ops_rose_violet.json \
        --mask-file examples/mask_rose_head.json --feather 24 --out a.png --video a.mp4

    # 3) 只编译 / 只评测 / 只出视频
    python paint_cli.py compile plan.json --instruction "把床层压暗" --provider ark --out ops.json
    python paint_cli.py eval plan.json examples/batch/expected_hard.json examples/batch/compiled_hard.json
    python paint_cli.py video plan.json --ops ops.json --mask-file m.json --out a.mp4 --frames 600

子命令与底层脚本的关系：edit/video 复用 instruction_demo 与 paint_video 的同一套函数，
不重新实现渲染；compile 复用 compile_ops；eval 复用 eval_instructions。
"""
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np

import compile_ops
import masks
import ops
import ops_incremental_demo as D
import paint_native
import plan_io
import tile_inc

HERE = Path(__file__).resolve().parent


def _mask_from_args(a, w, h):
    if getattr(a, "mask_file", None):
        return masks.from_json(json.loads(Path(a.mask_file).read_text(encoding="utf-8")))
    if getattr(a, "rect", None):
        x, y, rw, rh = [float(v) for v in a.rect.split(",")]
        return masks.rect(x, y, rw, rh)
    return masks.rect(0, 0, w, h)


def _compile(a, doc):
    if getattr(a, "ops", None):
        return json.loads(Path(a.ops).read_text(encoding="utf-8")), "file"
    if not getattr(a, "instruction", None):
        raise SystemExit("需要 --ops <file> 或 --instruction '...' --provider <name>")
    mask = None
    if getattr(a, "mask_file", None):
        mask = masks.from_json(json.loads(Path(a.mask_file).read_text(encoding="utf-8")))
    image = getattr(a, "image", None)
    if not image and getattr(a, "thumb", False):
        import tempfile
        tp = Path(tempfile.gettempdir()) / "compile_thumb.png"
        compile_ops.render_thumb(a.plan, tp, gpu=bool(getattr(a, "gpu", False)))
        image = str(tp)
    prompt = compile_ops.build_prompt(compile_ops.summarize(doc), a.instruction, mask,
                                      has_image=bool(image))
    provider = getattr(a, "provider", None) or "deepseek"
    try:
        specs = compile_ops.compile_with_model(prompt, provider=provider,
                                               model=getattr(a, "model", None), image=image)
    except Exception as ex:
        sys.stderr.write("compile failed: %s\n\n--- prompt ---\n%s\n" % (ex, prompt))
        raise SystemExit(2)
    return specs, "model:" + provider


def cmd_edit(a):
    t0 = time.perf_counter()
    doc, w, h = plan_io.load_plan(a.plan)
    specs, source = _compile(a, doc)
    compile_ops.validate_ops(doc, specs)
    mask = _mask_from_args(a, w, h)

    touched = {sp["layer"] for sp in specs if sp.get("layer")} or {l["name"] for l in doc["layers"]}
    sel, wt, sel_by_layer = [], [], {}
    for name in sorted(touched):
        s1, w1 = masks.weights(doc, mask, a.feather, layer=name)
        if getattr(a, "hard_mask", False):
            w1 = [1.0] * len(s1)
        sel += s1
        wt += w1
        sel_by_layer[name] = s1
    out = ops.apply(doc, specs, selection=sel, weights=wt)

    p, cat, w, h = D.new_painter(doc, gpu=a.gpu)
    region = np.zeros((h, w), np.int32)
    if getattr(a, "fast", False):
        # 只想要最终图时，直接整幅渲染编辑后的计划（省掉"先画旧的再局部重算"的一整遍）
        plan2, spec2 = D.doc_to_plan(out, cat)
        tile_inc.full(p, plan2, spec2, lambda nm: region, gpu=a.gpu, nthreads=8)
        arr = np.ascontiguousarray(p.composite())
        p.close()
        x0 = y0 = 0
        x1, y1 = w - 1, h - 1
    else:
        plan1, spec = D.doc_to_plan(doc, cat)
        tile_inc.full(p, plan1, spec, lambda nm: region, gpu=a.gpu, nthreads=8)
        p.composite()

        plan2, spec2 = D.doc_to_plan(out, cat, base=plan1, only=touched)
        dirty = tile_inc.dirty_rects(doc, plan1, sel_by_layer, w, h)
        x0 = min(r[0] for rs in dirty.values() for r in rs) if dirty else 0
        y0 = min(r[1] for rs in dirty.values() for r in rs) if dirty else 0
        x1 = max(r[2] for rs in dirty.values() for r in rs) if dirty else w - 1
        y1 = max(r[3] for rs in dirty.values() for r in rs) if dirty else h - 1
        tile_inc.rebuild(p, plan2, spec2, dirty, lambda nm: region, gpu=a.gpu, nthreads=8)
        arr = np.ascontiguousarray(p.composite())
        p.close()

    from PIL import Image
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(arr, "RGBA").save(a.out)

    report = {"ok": True, "compiled_from": source, "ops": specs, "selected": len(sel),
              "dirty_rect": [x0, y0, x1, y1], "png": str(a.out),
              "elapsed_s": round(time.perf_counter() - t0, 2)}
    if getattr(a, "video", None):
        ops_path = a.ops
        if not ops_path:                      # 模型编译的算子要先落盘，视频脚本只吃文件
            tmp_ops = Path(a.out).with_suffix(".ops.json")
            tmp_ops.write_text(json.dumps(specs, ensure_ascii=False), encoding="utf-8")
            ops_path = str(tmp_ops)
            report["ops_file"] = ops_path
        cmd = [sys.executable, str(HERE / "paint_video.py"), a.plan,
               "--ops", ops_path, "--frames", str(a.frames), "--fps", str(a.fps),
               "--out", a.video]
        if a.mask_file:
            cmd += ["--mask-file", a.mask_file]
        elif a.rect:
            cmd += ["--rect", a.rect]
        cmd += ["--feather", str(a.feather)]
        if a.gpu:
            cmd.append("--gpu")
        if getattr(a, "hard_mask", False):
            cmd.append("--hard-mask")
        res = subprocess.run(cmd, capture_output=True, text=True)
        report["video"] = a.video
        report["video_ok"] = res.returncode == 0
        if res.stdout.strip():
            try:
                report["video_stats"] = json.loads(res.stdout.strip().splitlines()[-1])
            except Exception:
                report["video_stats"] = res.stdout.strip()[-300:]
        if res.returncode != 0:
            report["video_stderr"] = res.stderr.strip()[-400:]
    print(json.dumps(report, ensure_ascii=False))


def cmd_compile(a):
    doc, w, h = plan_io.load_plan(a.plan)
    mask = None
    if a.mask_file:
        mask = masks.from_json(json.loads(Path(a.mask_file).read_text(encoding="utf-8")))
    prompt = compile_ops.build_prompt(compile_ops.summarize(doc), a.instruction, mask)
    try:
        specs = compile_ops.compile_with_model(prompt, provider=a.provider, model=a.model)
    except Exception as ex:
        sys.stderr.write("compile failed: %s\n\n--- prompt ---\n%s\n" % (ex, prompt))
        raise SystemExit(2)
    compile_ops.validate_ops(doc, specs)
    Path(a.out).write_text(json.dumps(specs, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"ok": True, "ops": len(specs), "out": a.out}, ensure_ascii=False))


def cmd_video(a):
    specs = json.loads(Path(a.ops).read_text(encoding="utf-8"))
    doc, w, h = plan_io.load_plan(a.plan)
    compile_ops.validate_ops(doc, specs)
    cmd = [sys.executable, str(HERE / "paint_video.py"), a.plan, "--ops", a.ops,
           "--frames", str(a.frames), "--fps", str(a.fps), "--out", a.out,
           "--feather", str(a.feather)]
    if a.mask_file:
        cmd += ["--mask-file", a.mask_file]
    elif a.rect:
        cmd += ["--rect", a.rect]
    if a.gpu:
        cmd.append("--gpu")
    res = subprocess.run(cmd, capture_output=True, text=True)
    sys.stdout.write(res.stdout)
    if res.returncode != 0:
        sys.stderr.write(res.stderr[-800:])
        raise SystemExit(res.returncode)


def cmd_eval(a):
    res = subprocess.run([sys.executable, str(HERE / "eval_instructions.py"),
                          a.plan, a.expected, a.compiled], capture_output=True, text=True)
    sys.stdout.write(res.stdout)
    raise SystemExit(res.returncode)


def main():
    import argparse
    ap = argparse.ArgumentParser(prog="paint_cli.py")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p, need_plan=True):
        if need_plan:
            p.add_argument("plan")
        p.add_argument("--ops", default=None)
        p.add_argument("--mask-file", default=None)
        p.add_argument("--rect", default=None)
        p.add_argument("--feather", type=float, default=24.0)
        p.add_argument("--hard-mask", action="store_true", help="掩膜权重二值化为 1（改色时要满强度）")
        p.add_argument("--gpu", action="store_true")

    e = sub.add_parser("edit", help="编译/加载算子 -> 编辑 -> 增量渲染（可选出视频）")
    common(e)
    e.add_argument("--instruction", default=None)
    e.add_argument("--provider", default=None)
    e.add_argument("--model", default=None)
    e.add_argument("--out", default="edit.png")
    e.add_argument("--video", default=None)
    e.add_argument("--frames", type=int, default=600)
    e.add_argument("--fps", type=int, default=30)
    e.add_argument("--fast", action="store_true",
                   help="不做裸图+局部重算的两遍，直接渲染编辑后的计划（快，但失去增量自检）")
    e.set_defaults(func=cmd_edit)

    c = sub.add_parser("compile", help="用 provider 编译指令 -> ops.json")
    c.add_argument("plan")
    c.add_argument("--instruction", required=True)
    c.add_argument("--provider", default="deepseek")
    c.add_argument("--model", default=None)
    c.add_argument("--mask-file", default=None)
    c.add_argument("--out", default="ops.json")
    c.add_argument("--image", default=None)
    c.add_argument("--thumb", action="store_true")
    c.set_defaults(func=cmd_compile)

    v = sub.add_parser("video", help="用已编译 ops 出 30fps 视频")
    common(v)
    v.add_argument("--out", default="edit.mp4")
    v.add_argument("--frames", type=int, default=600)
    v.add_argument("--fps", type=int, default=30)
    v.set_defaults(func=cmd_video)

    ev = sub.add_parser("eval", help="跑指令集评测")
    ev.add_argument("plan")
    ev.add_argument("expected")
    ev.add_argument("compiled")
    ev.set_defaults(func=cmd_eval)

    a = ap.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
