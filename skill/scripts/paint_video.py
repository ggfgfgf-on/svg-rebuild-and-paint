"""改画 → 30fps 视频：先把计划画起来，再在指定进度处把改动"重画"一遍。

    python paint_video.py <plan.json> [--ops ops.json] [--mask-file mask.json]
        [--rect "x,y,w,h"] [--feather 24] [--frames 600] [--fps 30] [--groups 60]
        [--edit-at 0.75] [--out <mp4>] [--gpu]

帧率恒定 30fps（帧数/时长由 --frames/--fps 决定，不丢帧）；最后一帧必须与
"整份重画改后计划"逐位相同，否则报错退出。
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
import paint_native
import plan_io
import tile_inc



def schedule(n_frames, n_groups, edit_at, draw_until):
    """帧 -> (画到第几组, 是否到编辑点)。

    绘制必须在编辑点之前**全部完成**：否则重算过的笔触会在后面的分组里被再画一遍，
    半透明叠加两次会让像素和整份重画对不上（实测差 3xx px）。"""
    out = []
    for i in range(n_frames):
        f = i / max(n_frames - 1, 1)
        if f <= draw_until:
            t = (f / max(draw_until, 1e-6)) ** 1.30
            g = min(n_groups - 1, int(t * n_groups))
        else:
            g = n_groups - 1
        out.append((g, f >= edit_at))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("plan")
    ap.add_argument("--ops", default=None)
    ap.add_argument("--mask-file", default=None)
    ap.add_argument("--rect", default=None)
    ap.add_argument("--feather", type=float, default=24.0)
    ap.add_argument("--hard-mask", action="store_true")
    ap.add_argument("--frames", type=int, default=600)
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--groups", type=int, default=60)
    ap.add_argument("--edit-at", type=float, default=0.85)
    ap.add_argument("--draw-until", type=float, default=0.8)
    ap.add_argument("--out", default=str(Path.home() / ".cache" / "ops_demo" / "edit.mp4"))
    ap.add_argument("--gpu", action="store_true")
    a = ap.parse_args()

    import subprocess

    from PIL import Image

    import imageio_ffmpeg
    t0 = time.perf_counter()
    doc, w, h = plan_io.load_plan(a.plan)
    p, cat, w, h = D.new_painter(doc, gpu=a.gpu)
    plan1, spec = D.doc_to_plan(doc, cat)
    layer_li = {nm: i + 1 for i, (nm, _) in enumerate(spec)}   # clear_rect 用 1-based 层号
    region = np.zeros((h, w), np.int32)

    # 每层按顺序切成 groups 组
    groups = []
    for li, (name, _) in enumerate(spec):
        n = int(plan1[name]["offs"].size - 1)
        if n == 0:
            continue
        per = max(1, int(np.ceil(n / max(1, a.groups // max(1, len(spec))))))
        for b in range(0, n, per):
            groups.append((name, li + 1, np.arange(b, min(b + per, n))))

    groups_total = len(groups)
    plan2, spec2, dirty, touched = plan1, spec, {}, set()
    out = doc
    structural = False
    if a.ops:
        specs = compile_ops.validate_ops(doc, json.loads(Path(a.ops).read_text(encoding="utf-8")))
        ops.resolve_asset_paths(specs, Path(a.plan).resolve().parent)
        if a.mask_file:
            mask = masks.from_json(json.loads(Path(a.mask_file).read_text(encoding="utf-8")))
        elif a.rect:
            x, y, rw, rh = [float(v) for v in a.rect.split(",")]
            mask = masks.rect(x, y, rw, rh)
        else:
            mask = masks.rect(0, 0, w, h)
        touched = {sp["layer"] for sp in specs if sp.get("layer")} or {l["name"] for l in doc["layers"]}
        sel, wt, sel_by_layer = [], [], {}
        for name in sorted(touched):
            s1, w1 = masks.weights(doc, mask, a.feather, layer=name)
            if a.hard_mask:
                w1 = [1.0] * len(s1)
            sel += s1
            wt += w1
            sel_by_layer[name] = s1
        out = ops.apply(doc, specs, selection=sel, weights=wt)
        plan2, spec2 = D.doc_to_plan(out, cat, base=plan1, only=touched)
        dirty = tile_inc.dirty_rects(doc, plan1, sel_by_layer, w, h)
        # 加层/删层/改名/重排会改变层下标，增量重建只认旧层：这类编辑在编辑帧整幅重建
        structural = ([l["name"] for l in doc["layers"]]
                      != [l["name"] for l in out["layers"]])

    out_path = Path(a.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    frames_dir = out_path.with_suffix("")
    frames_dir = Path(str(frames_dir) + ".frames")
    if frames_dir.exists():
        for old in frames_dir.glob("*.png"):
            old.unlink()
    frames_dir.mkdir(parents=True, exist_ok=True)

    t1 = time.perf_counter()
    drawn = 0
    edited = False
    edit_frames = 0
    for i, (target, want_edit) in enumerate(schedule(a.frames, len(groups), a.edit_at, a.draw_until)):
        if want_edit and not edited and touched:
            while drawn < len(groups):        # 先把剩余的笔画完，避免重算后被重复叠加
                name, li, sel = groups[drawn]
                tile_inc.draw(p, plan1, spec, name, li, sel, region, a.gpu, nthreads=8)
                drawn += 1
            if structural:
                # 层集合变了：旧 painter 没有新层下标，直接换一个按编辑后计划建的 painter 整幅重画
                p.close()
                p, cat, w, h = D.new_painter(out, gpu=a.gpu)
                plan_full, spec_full = D.doc_to_plan(out, cat)
                tile_inc.full(p, plan_full, spec_full, lambda nm: region, gpu=a.gpu, nthreads=8)
                groups, drawn = [], 0
            else:
                for name in sorted(touched):
                    rects = dirty.get(name) or []
                    for (rx0, ry0, rx1, ry1) in rects:
                        p.clear_rect(layer_li[name], rx0, ry0, rx1, ry1)
                tile_inc.rebuild(p, plan2, spec2, dirty, lambda nm: region, gpu=a.gpu, nthreads=8)
            edited = True
            edit_frames = i + 1
        while not edited and drawn <= target and drawn < len(groups):
            name, li, sel = groups[drawn]
            tile_inc.draw(p, plan1, spec, name, li, sel, region, a.gpu, nthreads=8)
            drawn += 1
        if edited:
            while drawn < len(groups):        # 编辑后把剩下的笔触补齐（未改动层）
                name, li, sel = groups[drawn]
                tile_inc.draw(p, plan2, spec2, name, li, sel, region, a.gpu, nthreads=8)
                drawn += 1
        comp = np.ascontiguousarray(p.composite())
        Image.fromarray(comp[..., :3], "RGB").save(frames_dir / ("%05d.png" % i), compress_level=1)
    last = comp
    t2 = time.perf_counter()

    t_enc = time.perf_counter()
    exe = imageio_ffmpeg.get_ffmpeg_exe()
    cmd = [exe, "-y", "-framerate", str(a.fps), "-i", str(frames_dir / "%05d.png"),
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", str(out_path)]
    subprocess.run(cmd, capture_output=True, check=False)
    t_enc = time.perf_counter() - t_enc

    # 自检必须拿「编辑后的计划」做参考；之前两边都用原始计划，结构编辑会假通过
    q, _, _, _ = D.new_painter(out, gpu=a.gpu)
    plan_ref, spec_ref = D.doc_to_plan(out, cat)
    tile_inc.full(q, plan_ref, spec_ref, lambda nm: region, gpu=a.gpu, nthreads=8)
    ref = np.ascontiguousarray(q.composite())
    q.close()
    diff = int(np.any(last != ref, axis=2).sum())

    size_mb = round(out_path.stat().st_size / 1048576, 2)
    print(json.dumps({
        "frames": a.frames, "fps": a.fps, "duration_s": round(a.frames / a.fps, 2),
        "groups": groups_total, "edited_at_frame": edit_frames,
        "render_and_save_s": round(t2 - t1, 2), "encode_s": round(t_enc, 2), "total_s": round(t2 - t0, 2),
        "mb": size_mb, "last_frame_vs_full_render_diff_px": diff,
        "out": str(out_path),
    }, ensure_ascii=False))
    if diff != 0:
        raise SystemExit("last frame differs from a full render (%d px)" % diff)


if __name__ == "__main__":
    main()
