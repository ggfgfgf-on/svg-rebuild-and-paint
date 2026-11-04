"""计划空间掩膜：把「改哪里」表达成笔触选择 + 每笔权重。

对应扩散 inpainting 的 mask，但掩膜不栅格化——它只回答两个问题：

  1. selection: 哪些笔触受影响（全局下标，按几何/颜色相交判断）
  2. weights:   每笔 0..1 的影响强度（按到掩膜边界的距离羽化）

配合 ops.py 用：

    m   = masks.rect(20, 20, 80, 60)
    sel = masks.select(doc, m)
    w   = masks.weights(doc, m, feather=8)
    doc2 = ops.apply(doc, [{"op": "set_layer_hue", "layer": "petal", "hue": 220}],
                     selection=sel, weights=w)

掩膜类型（都是纯 dict，可 JSON 化，方便编译产物落盘）：

    {"type": "rect",    "x": 0, "y": 0, "w": 10, "h": 10}
    {"type": "point",   "x": 5, "y": 5, "r": 12}
    {"type": "polygon", "points": [[x, y], ...]}
    {"type": "color",   "rgb": [r, g, b], "tol": 40}      # 按笔触均色选
    {"type": "all"}
"""
import numpy as np


def all_():
    return {"type": "all"}


def rect(x, y, w, h):
    return {"type": "rect", "x": float(x), "y": float(y), "w": float(w), "h": float(h)}


def point(x, y, r):
    return {"type": "point", "x": float(x), "y": float(y), "r": float(r)}


def polygon(points):
    return {"type": "polygon", "points": [[float(a), float(b)] for a, b in points]}


def color(rgb, tol=40.0):
    return {"type": "color", "rgb": [float(v) for v in rgb], "tol": float(tol)}


def _pts(stroke):
    p = np.asarray(stroke["points"], np.float64)
    return p[:, :2] if p.ndim == 2 and p.shape[1] >= 2 else np.zeros((0, 2))


def stroke_radius(stroke):
    return 0.5 * float(stroke.get("size", 1.0))


def stroke_bbox(stroke):
    p = _pts(stroke)
    if len(p) == 0:
        return None
    r = stroke_radius(stroke)
    return (float(p[:, 0].min() - r), float(p[:, 1].min() - r),
            float(p[:, 0].max() + r), float(p[:, 1].max() + r))


def _rgb(stroke):
    c = stroke.get("color", [0, 0, 0])
    if isinstance(c, str):
        c = c.lstrip("#")
        return np.array([int(c[i:i + 2], 16) for i in (0, 2, 4)], np.float64)
    return np.asarray(c, np.float64)[:3]


def _in_poly(pts, poly):
    """射线法，向量化（pts: n×2，poly: m×2）。"""
    n, m = len(pts), len(poly)
    if n == 0 or m < 3:
        return np.zeros(n, bool)
    x = pts[:, 0][:, None]
    y = pts[:, 1][:, None]
    x1, y1 = poly[:, 0][None, :], poly[:, 1][None, :]
    x2, y2 = np.roll(poly[:, 0], -1)[None, :], np.roll(poly[:, 1], -1)[None, :]
    cond = ((y1 > y) != (y2 > y))
    with np.errstate(divide="ignore", invalid="ignore"):
        xi = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
    hit = cond & (x < xi)
    return (hit.sum(axis=1) % 2) == 1


def _seg_dist(pts, poly):
    """点到多边形边界的最短距离（pts: n×2，poly: m×2）→ n。"""
    a = poly
    b = np.roll(poly, -1, axis=0)
    p = pts[:, None, :]
    ab = (b - a)[None, :, :]
    ap = p - a[None, :, :]
    denom = (ab * ab).sum(axis=2)
    denom = np.where(denom < 1e-12, 1e-12, denom)
    t = np.clip((ap * ab).sum(axis=2) / denom, 0.0, 1.0)
    proj = a[None, :, :] + t[:, :, None] * ab
    d = np.sqrt(((p - proj) ** 2).sum(axis=2))
    return d.min(axis=1)


def _clip01(v):
    return np.clip(v, 0.0, 1.0)


def point_weight(mask, pts, radius=0.0, feather=0.0):
    d = np.sqrt(((pts - np.array([mask["x"], mask["y"]])) ** 2).sum(axis=1)) - radius
    inside = d <= mask["r"]
    if feather <= 0:
        return inside.astype(np.float64)
    return np.where(inside, _clip01((mask["r"] - d) / feather), 0.0)


def rect_weight(mask, pts, radius=0.0, feather=0.0):
    x0, y0 = mask["x"] - radius, mask["y"] - radius
    x1, y1 = mask["x"] + mask["w"] + radius, mask["y"] + mask["h"] + radius
    dx = np.minimum(pts[:, 0] - x0, x1 - pts[:, 0])
    dy = np.minimum(pts[:, 1] - y0, y1 - pts[:, 1])
    inside = (dx >= 0) & (dy >= 0)
    if feather <= 0:
        return inside.astype(np.float64)
    return np.where(inside, _clip01(np.minimum(dx, dy) / feather), 0.0)


def poly_weight(mask, pts, feather=0.0):
    poly = np.asarray(mask["points"], np.float64)
    inside = _in_poly(pts, poly)
    if feather <= 0:
        return inside.astype(np.float64)
    d = _seg_dist(pts, poly)
    return np.where(inside, _clip01(d / feather), 0.0)


def stroke_weight(mask, stroke, feather=0.0, mode="any"):
    """单笔相对掩膜的权重。

    mode="any"（默认）：采样点最大值 —— 只要有一个点落进掩膜就算选中（会整笔改色）。
    mode="all"：采样点最小值 —— 整笔都在掩膜内才等于 1；跨边界的笔触会被排除，
    适合"只改背景、别碰花"这类要求（笔触级算子改的是整笔，光靠 any 会染到主体）。
    """
    t = mask.get("type")
    if t == "all":
        return 1.0
    if mode == "center":
        # 整笔按"中心点是否在掩膜内"决定：比 any 少一圈外溢（any 只要有一点碰到就整笔改色），
        # 比 all 少一圈空洞（all 会把跨边界的笔全排除）。
        # 实现：把笔触临时缩成半径 0 的单点再走 any 的判定，复用同一套几何，避免另造一套。
        pts0 = _pts(stroke)
        if len(pts0) == 0:
            return 0.0
        probe = dict(stroke)
        probe["points"] = [[float(np.mean(pts0[:, 0])), float(np.mean(pts0[:, 1]))]]
        probe["size"] = 0.0
        probe.pop("radius", None)
        return float(stroke_weight(mask, probe, 0.0, "any"))
    if t == "and":
        # 交集：取最小值。用途典型是「红色笔触 ∩ 花头区域」——只靠颜色会把
        # 画面下方偏红的茎/叶缘也染上（实测叶子下沿出现蓝色斑点）。
        return float(min(stroke_weight(sub, stroke, feather) for sub in mask["masks"]))
    if t == "color":
        d = float(np.linalg.norm(_rgb(stroke) - np.asarray(mask["rgb"], np.float64)))
        return float(max(0.0, min(1.0, 1.0 - d / max(mask["tol"], 1e-6))))
    pts = _pts(stroke)
    if len(pts) == 0:
        return 0.0
    r = stroke_radius(stroke)
    if t == "point":
        w = point_weight(mask, pts, r, feather)
    elif t == "rect":
        w = rect_weight(mask, pts, r, feather)
    elif t == "polygon":
        w = poly_weight(mask, pts, feather)
    elif t == "raster":
        w = raster_weight(mask, pts, r)
    else:
        raise ValueError("unknown mask type %r" % (t,))
    if not len(w):
        return 0.0
    return float(w.min()) if mode == "all" else float(w.max())


def _densify(pts, step):
    """在折线上按 step 像素插点（端点保留）。"""
    pts = np.asarray(pts, np.float64)
    if len(pts) < 2:
        return pts
    out = [pts[0]]
    for a, b in zip(pts[:-1], pts[1:]):
        d = float(np.linalg.norm(b - a))
        n = int(d // max(step, 1e-6))
        for k in range(1, n + 1):
            out.append(a + (b - a) * (k / float(n + 1)) if n else b)
        out.append(b)
    return np.asarray(out, np.float64)


def _mask_bbox(mask):
    t = mask.get("type")
    if t == "rect":
        return (mask["x"], mask["y"], mask["x"] + mask["w"], mask["y"] + mask["h"])
    if t == "point":
        return (mask["x"] - mask["r"], mask["y"] - mask["r"],
                mask["x"] + mask["r"], mask["y"] + mask["r"])
    if t == "polygon":
        p = np.asarray(mask["points"], np.float64)
        return (float(p[:, 0].min()), float(p[:, 1].min()),
                float(p[:, 0].max()), float(p[:, 1].max()))
    if t == "and":
        # 交集包围盒：各子掩膜 bbox 的交
        bbs = [b for b in (_mask_bbox(sub) for sub in mask["masks"]) if b is not None]
        if not bbs:
            return None
        a = np.asarray(bbs, np.float64)
        return (float(a[:, 0].max()), float(a[:, 1].max()),
                float(a[:, 2].min()), float(a[:, 3].min()))
    return None


def weights(doc, mask, feather=0.0, layer=None, mode="any"):
    """返回 (selection, weights)：全局笔触下标 + 对应 0..1 权重。mode 见 stroke_weight。"""
    sel, wt = [], []
    mb = _mask_bbox(mask)
    if mb is not None:
        pad = float(feather) + 1.0
        mb = (mb[0] - pad, mb[1] - pad, mb[2] + pad, mb[3] + pad)
    for i, s in enumerate(doc["strokes"]):
        if layer is not None and s["layer"] != layer:
            continue
        if mb is not None:
            b = stroke_bbox(s)
            if b is None or b[2] < mb[0] or b[0] > mb[2] or b[3] < mb[1] or b[1] > mb[3]:
                continue
        w = stroke_weight(mask, s, feather, mode)
        if w > 0:
            sel.append(i)
            wt.append(w)
    return sel, wt


def select(doc, mask, layer=None, mode="any"):
    return weights(doc, mask, 0.0, layer, mode)[0]


def bbox(doc, selection):
    """选中笔触的脏矩形（含笔刷半径）。空选择 → None。"""
    xs = []
    for i in selection:
        b = stroke_bbox(doc["strokes"][i])
        if b:
            xs.append(b)
    if not xs:
        return None
    a = np.asarray(xs)
    return (float(a[:, 0].min()), float(a[:, 1].min()), float(a[:, 2].max()), float(a[:, 3].max()))


def _label(mask_bool):
    """4 连通标记；有 scipy 用它，没有就 BFS 兜底。返回 (labels, n)。"""
    try:
        from scipy import ndimage
        return ndimage.label(mask_bool)
    except Exception:
        pass
    h, w = mask_bool.shape
    labels = np.zeros((h, w), np.int32)
    n = 0
    from collections import deque
    for sy in range(h):
        for sx in range(w):
            if not mask_bool[sy, sx] or labels[sy, sx]:
                continue
            n += 1
            q = deque([(sy, sx)])
            labels[sy, sx] = n
            while q:
                y, x = q.popleft()
                for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    ny, nx = y + dy, x + dx
                    if 0 <= ny < h and 0 <= nx < w and mask_bool[ny, nx] and not labels[ny, nx]:
                        labels[ny, nx] = n
                        q.append((ny, nx))
    return labels, n


_RASTER_CACHE = {}


def _encode_bits(b):
    import base64
    import numpy as np_mod
    return base64.b64encode(np_mod.packbits(np_mod.ascontiguousarray(b, dtype=bool)).tobytes()).decode("ascii")


def _decode_bits(mask):
    key = (mask.get("w"), mask.get("h"), mask.get("x"), mask.get("y"), hash(mask.get("bits")))  # 内容键：id() 会被复用，导致读到上一个掩膜的位图
    hit = _RASTER_CACHE.get(key)
    if hit is not None:
        return hit
    import base64
    import numpy as np_mod
    raw = base64.b64decode(mask["bits"])
    b = np_mod.unpackbits(np_mod.frombuffer(raw, dtype=np_mod.uint8)).astype(bool)
    b = b[:mask["w"] * mask["h"]].reshape(mask["h"], mask["w"])
    _RASTER_CACHE[key] = b
    return b


def from_bool(b, x0=0, y0=0):
    """像素级布尔图 -> raster 掩膜（任意形状，不再退化成包围盒）。"""
    import numpy as np_mod
    b = np_mod.ascontiguousarray(b, dtype=bool)
    h, w = b.shape
    return {"type": "raster", "x": int(x0), "y": int(y0), "w": int(w), "h": int(h),
            "bits": _encode_bits(b)}


_RASTER_FEATHER_CACHE = {}


def _dist_map(mask):
    """掩膜内部到边界的距离图（feather 用）；按内容缓存。"""
    key = (mask.get("w"), mask.get("h"), mask.get("x"), mask.get("y"), mask.get("bits"))
    hit = _RASTER_FEATHER_CACHE.get(key)
    if hit is not None:
        return hit
    import cv2
    dist = cv2.distanceTransform(_decode_bits(mask).astype(np.uint8), cv2.DIST_L2, 3)
    _RASTER_FEATHER_CACHE[key] = dist
    return dist


def raster_weight(mask, pts, r=0.0, feather=0.0):
    import numpy as np_mod
    b = _decode_bits(mask)
    h, w = b.shape
    dist = _dist_map(mask) if feather and feather > 0 else None
    out = np_mod.zeros(len(pts), np_mod.float64)
    for k, p in enumerate(pts):
        x = int(np_mod.floor(p[0] - mask["x"]))
        y = int(np_mod.floor(p[1] - mask["y"]))
        if 0 <= x < w and 0 <= y < h and b[y, x]:
            out[k] = 1.0 if dist is None else float(min(1.0, dist[y, x] / float(feather)))
    return out


def _calibrated_local(name, default):
    """工程标定（SVP_CALIBRATED）优先，其次技能默认；读不到就用传入默认值。"""
    import json as _j, os as _os
    from pathlib import Path as _P
    p = _os.environ.get("SVP_CALIBRATED") or ""
    for cand in ([_P(p)] if p and _P(p).is_file() else []) + [_P(__file__).resolve().parent / "calibrated.json"]:
        try:
            v = _j.loads(cand.read_text(encoding="utf-8")).get(name)
        except Exception:
            v = None
        if isinstance(v, dict) and "value" in v:
            return v["value"]
        if isinstance(v, (int, float)):
            return v
    return default


def from_image(image, color, tol=None, keep_largest=True, pad=0):
    tol = float(_calibrated_local("tol", 40.0)) if tol is None else float(tol)
    """像素级颜色连通域 -> 计划空间矩形掩膜（取最大连通块的包围盒）。

    这是「掩膜由模型/图像给出」的那条来源：先按颜色+容差找像素，再做连通域，
    最后退化成 rect 掩膜交给笔触选择与脏矩形计算——不需要每笔回读像素。
    """
    from PIL import Image
    arr = np.asarray(Image.open(image).convert("RGB"), np.int32)
    d = np.abs(arr - np.asarray(color, np.int32)[:3]).max(axis=2)
    labels, n = _label(d <= float(tol))
    if n == 0:
        return None
    keep = labels > 0
    if keep_largest:
        sizes = np.bincount(labels.ravel())
        sizes[0] = 0
        keep = labels == int(sizes.argmax())
    ys, xs = np.nonzero(keep)
    x0, y0 = int(xs.min()) - pad, int(ys.min()) - pad
    x1, y1 = int(xs.max()) + pad, int(ys.max()) + pad
    return rect(x0, y0, x1 - x0 + 1, y1 - y0 + 1)


def to_json(mask):
    return dict(mask)


def from_json(obj):
    m = dict(obj)
    t = m.get("type")
    if t == "and":
        parts = m.get("masks") or []
        if len(parts) < 2:
            raise ValueError("and 掩膜至少需要 2 个子掩膜")
        m["masks"] = [from_json(x) for x in parts]
        return m
    if t not in ("rect", "point", "polygon", "color", "all", "raster"):
        raise ValueError("unknown mask type %r" % (t,))
    return m


def _inside_frac(mask, pts):
    """每个采样点落在掩膜内的程度（0/1，羽化不计）。"""
    t = mask.get("type")
    if t == "all" or t == "color":
        return np.ones(len(pts), np.float64)
    if t == "point":
        return point_weight(mask, pts, 0.0, 0.0)
    if t == "rect":
        return rect_weight(mask, pts, 0.0, 0.0)
    if t == "polygon":
        return poly_weight(mask, pts, 0.0)
    if t == "raster":
        return raster_weight(mask, pts, 0.0, 0.0)
    if t == "and":
        return np.min(np.stack([_inside_frac(s, pts) for s in mask["masks"]]), axis=0)
    raise ValueError("unknown mask type %r" % (t,))


def spill(doc, mask, mode="any", layer=None):
    """有多少"颜料面积"落在掩膜外——笔触级算子的溢出闸门。

    口径：被选中的笔按 采样点数 x 笔宽 估面积，掩膜外的那部分算溢出。
    不算像素级渲染差（那要重渲染），所以它是**预估**，用来在改色前提示
    "你这么选会漫出 20%"，不是精确测量。
    """
    sel, wts = weights(doc, mask, mode=mode, layer=layer)
    strokes = doc["strokes"]
    tot = out = 0.0
    for i, w in zip(sel, wts):   # wts 与 sel 对齐，不是与 strokes 对齐
        if w <= 0:
            continue
        st = strokes[i]
        pts = _pts(st)
        if len(pts) == 0:
            continue
        inside = _inside_frac(mask, pts)
        r = max(stroke_radius(st), 0.5)
        area = float(len(pts)) * 2.0 * r
        tot += area
        out += area * float(1.0 - float(np.mean(inside)))
    return {"spill": (out / tot) if tot > 0 else 0.0, "selected": int(len(sel)),
            "paint_area": round(tot, 1), "outside_area": round(out, 1)}
