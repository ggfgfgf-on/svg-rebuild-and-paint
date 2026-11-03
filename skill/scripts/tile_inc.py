"""tile 级局部重绘：只清一块矩形、只重放与它相交的笔触，CPU/GPU 双后端。

前提：笔触回放是确定性的，且区域图能按像素裁剪。验收标准是「局部重绘结果
== 整份重画结果」逐位相同（见 tests/test_incremental.py）。
"""
"""tile 级局部重绘：CPU/GPU 双后端、多块脏区合并、合成结果缓存。"""
import numpy as np


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
    s = _sub(plan, name, sel)
    if s is None:
        return 0
    pts, o2, par2, rid2 = s
    offs = o2[:-1] if gpu else o2
    p.stroke_batch_raw(li, pts, offs, par2, rid2, nthreads=nthreads, region=region)
    return len(sel)


def full(p, plan, spec, base_of, gpu, nthreads=8):
    for li, (nm, _) in enumerate(spec):
        d = plan[nm]
        n = int(d["offs"].size - 1)
        if n == 0:
            continue
        draw(p, plan, spec, nm, li + 1, np.arange(n), base_of(nm), gpu, nthreads)


def mask_for(rects, base, H, W):
    m = np.zeros((H, W), bool)
    for (x0, y0, x1, y1) in rects:
        m[y0:y1 + 1, x0:x1 + 1] = True
    return np.where(m, base, np.int32(-5)).astype(np.int32)


def rebuild(p, plan, spec, dirty, base_of, gpu, nthreads=8):
    """dirty: {layer_name: [(x0,y0,x1,y1), ...]}；一次区域图、一次合成。"""
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
