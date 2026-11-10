"""tile 级局部重绘：只清一块矩形、只重放与它相交的笔触，CPU/GPU 双后端。

前提：笔触回放是确定性的，且区域图能按像素裁剪。验收标准是「局部重绘结果
== 整份重画结果」逐位相同（见 tests/test_incremental.py）。
"""
"""tile 级局部重绘：CPU/GPU 双后端、多块脏区合并、合成结果缓存。"""
import sys

import numpy as np

_GPU_WARNED = False


def _mask_array(m, w, h):
    if not isinstance(m, dict) or m.get("encoding") != "png-base64":
        return None
    import base64, io
    from PIL import Image
    im = Image.open(io.BytesIO(base64.b64decode(m["data"]))).convert("L")
    if im.size != (w, h):
        im = im.resize((w, h), Image.BILINEAR)
    return np.asarray(im, dtype=np.uint8)


def _clip_rect(pts, x0, y0, x1, y1):
    import plan_svg
    return plan_svg._clip_poly([(float(p[0]), float(p[1])) for p in pts],
                               [(x0, y0), (x1, y0), (x1, y1), (x0, y1)])


def bboxes(plan, name):
    d = plan[name]
    offs, pts, par = d["offs"], d["pts"], d["params"]
    n = int(offs.size - 1)
    out = np.zeros((n, 4), np.float32)
    for i in range(n):
        b, e = int(offs[i]), int(offs[i + 1])
        if e <= b:
            out[i] = (1e9, 1e9, -1e9, -1e9)
            continue
        seg = pts[b:e].reshape(-1, 3)
        r = float(par[i, 0]) * 0.7 + 2.0
        out[i] = (seg[:, 0].min() - r, seg[:, 1].min() - r,
                  seg[:, 0].max() + r, seg[:, 1].max() + r)
    return out


def dirty_rects(doc, plan, sel_by_layer, w, h):
    """按引擎真实的落笔范围算脏矩形（`size*0.7+2`，见 bboxes）。

    不要用「笔触中心 ± size/2」估算：引擎的外扩更大，估小了会把旧颜色留在边缘
    （实测末帧与整份重画差 312 px，见 tests/test_paint_video.py）。
    """
    pos, cnt = {}, {}
    for i, st in enumerate(doc["strokes"]):
        n = cnt.get(st["layer"], 0)
        pos[i] = n
        cnt[st["layer"]] = n + 1
    out = {}
    for name, idxs in sel_by_layer.items():
        if not len(idxs):
            continue
        bb = bboxes(plan, name)
        arr = bb[np.asarray([pos[i] for i in idxs], np.int64)]
        out[name] = [(max(0, int(np.floor(arr[:, 0].min()))), max(0, int(np.floor(arr[:, 1].min()))),
                      min(w - 1, int(np.ceil(arr[:, 2].max()))), min(h - 1, int(np.ceil(arr[:, 3].max()))))]
    return out


def _sub(plan, name, sel):
    d = plan[name]
    offs, pts = d["offs"], d["pts"]
    parts, o2, par2, rid2 = [], [0], [], []
    cur = 0
    for i in sel:
        b, e = int(offs[i]), int(offs[i + 1])
        seg = pts[b:e]
        parts.append(seg)
        cur += int(seg.size)
        o2.append(cur)
        par2.append(d["params"][i])
        rid2.append(int(d["rids"][i]))
    if not parts:
        return None
    return (np.concatenate(parts).astype(np.float32), np.asarray(o2, np.int32),
            np.asarray(par2, np.float32).reshape(-1, 8), np.asarray(rid2, np.int32))


def draw(p, plan, spec, name, li, sel, region, gpu, nthreads=8):
    gpu = bool(getattr(p, "gpu_used", gpu))
    s = _sub(plan, name, sel)
    if s is None:
        return 0
    pts, o2, par2, rid2 = s
    offs = o2[:-1] if gpu else o2
    p.stroke_batch_raw(li, pts, offs, par2, rid2, nthreads=nthreads, region=region)
    return len(sel)


def _is_advanced(items):
    for s in items or []:
        if str(s.get("preset")) == "fill" or isinstance(s.get("paint"), dict) or isinstance(s.get("mask"), dict):
            return True
    return False


def _apply_layer_mask(p, li, m):
    if not isinstance(m, dict) or m.get("encoding") != "png-base64":
        return
    import base64, io
    from PIL import Image
    im = Image.open(io.BytesIO(base64.b64decode(m["data"]))).convert("L")
    if im.size != (p.w, p.h):
        im = im.resize((p.w, p.h), Image.BILINEAR)
    p.apply_mask(li, np.asarray(im, dtype=np.uint8))


def _draw_items(p, li, items, gpu, nthreads=8, region=None):
    for s in items:
        pts = np.asarray(s.get("points") or [], np.float32)
        if len(pts) < 1:
            continue
        if str(s.get("preset")) == "fill":
            q = pts[:, :2]
            if isinstance(s.get("paint"), dict):
                p.fill_gradient(li, q, s["paint"], s.get("opacity", 1.0))
            else:
                p.fill_polygon(li, q, s.get("color", (255, 255, 255)), s.get("opacity", 1.0))
        elif isinstance(s.get("paint"), dict):
            p.stroke_gradient(li, pts, s.get("size", 8.0), s.get("opacity", 1.0),
                              s["paint"], s.get("preset", "round_soft"), s.get("hardness", 0.55))
        else:
            p.stroke(li, pts, s.get("size", 8.0), s.get("opacity", 1.0), s.get("color", (255, 255, 255)),
                     s.get("preset", "round_soft"), s.get("hardness", 0.55), region_id=s.get("region_id", 0))


def full(p, plan, spec, base_of, gpu, nthreads=8):
    gpu = bool(getattr(p, "gpu_used", gpu))
    for li, (nm, _) in enumerate(spec):
        d = plan[nm]
        items = d.get("items")
        if items and _is_advanced(items):
            _draw_items(p, li + 1, items, gpu, nthreads)
            _apply_layer_mask(p, li + 1, d.get("mask"))
            continue
        n = int(d["offs"].size - 1)
        if n == 0:
            continue
        draw(p, plan, spec, nm, li + 1, np.arange(n), base_of(nm), gpu, nthreads)


def mask_for(rects, base, H, W):
    m = np.zeros((H, W), bool)
    for (x0, y0, x1, y1) in rects:
        m[y0:y1 + 1, x0:x1 + 1] = True
    return np.where(m, base, np.int32(-5)).astype(np.int32)


def _has_paint(items):
    return any(isinstance(s.get("paint"), dict) for s in items or [])


def _rebuild_local(p, plan, spec, dirty, base_of, gpu, nthreads=8):
    H, W = p.h, p.w
    total = 0
    for li, (nm, _) in enumerate(spec):
        rects = dirty.get(nm)
        if not rects:
            continue
        d = plan[nm]
        items = d.get("items") or []
        mask = _mask_array(d.get("mask"), W, H)
        for (x0, y0, x1, y1) in rects:
            p.set_region(mask_for([(x0, y0, x1, y1)], base_of(nm), H, W))
            p.clear_rect(li + 1, x0, y0, x1, y1)
            for s in items:
                pts = np.asarray(s.get("points") or [], np.float32)
                if str(s.get("preset")) == "fill":
                    q = _clip_rect(pts[:, :2], x0, y0, x1, y1)
                    if len(q) >= 3:
                        if isinstance(s.get("paint"), dict):
                            p.fill_gradient(li + 1, q, s["paint"], s.get("opacity", 1.0))
                        else:
                            p.fill_polygon(li + 1, q, s.get("color", (255, 255, 255)), s.get("opacity", 1.0))
                elif len(pts) >= 1:
                    p.stroke(li + 1, pts, s.get("size", 8.0), s.get("opacity", 1.0),
                             s.get("color", (255, 255, 255)), s.get("preset", "round_soft"),
                             s.get("hardness", 0.55), region_id=s.get("region_id", 0))
            if mask is not None:
                p.apply_mask_rect(li + 1, mask, x0, y0, x1, y1)
            total += len(items)
    return total


def rebuild(p, plan, spec, dirty, base_of, gpu, nthreads=8):
    gpu = bool(getattr(p, "gpu_used", gpu))
    """dirty: {layer_name: [(x0,y0,x1,y1), ...]}；一次区域图、一次合成。"""
    advanced = [plan[nm].get("items") for nm, _ in spec if _is_advanced(plan[nm].get("items"))]
    if advanced:
        if any(_has_paint(items) for items in advanced):
            return full(p, plan, spec, base_of, gpu, nthreads)
        return _rebuild_local(p, plan, spec, dirty, base_of, gpu, nthreads)
    H, W = p.h, p.w
    total = 0
    for li, (nm, _) in enumerate(spec):
        rects = dirty.get(nm)
        if not rects:
            continue
        d = plan[nm]
        n = int(d["offs"].size - 1)
        bb = bboxes(plan, nm)
        sel = [i for i in range(n)
               if any(bb[i, 2] >= x0 and bb[i, 0] <= x1 and bb[i, 3] >= y0 and bb[i, 1] <= y1
                      for (x0, y0, x1, y1) in rects)]
        region = mask_for(rects, base_of(nm), H, W)
        p.set_region(region)
        for (x0, y0, x1, y1) in rects:
            p.clear_rect(li + 1, x0, y0, x1, y1)
        total += draw(p, plan, spec, nm, li + 1, sel, region, gpu, nthreads)
    return total
