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


def stroke_weight(mask, stroke, feather=0.0):
    """单笔相对掩膜的权重 = 该笔所有采样点的最大值（笔刷半径参与 rect/point 判定）。"""
    t = mask.get("type")
    if t == "all":
        return 1.0
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
    else:
        raise ValueError("unknown mask type %r" % (t,))
    return float(w.max()) if len(w) else 0.0


def weights(doc, mask, feather=0.0, layer=None):
    """返回 (selection, weights)：全局笔触下标 + 对应 0..1 权重。"""
    sel, wt = [], []
    for i, s in enumerate(doc["strokes"]):
        if layer is not None and s["layer"] != layer:
            continue
        w = stroke_weight(mask, s, feather)
        if w > 0:
            sel.append(i)
            wt.append(w)
    return sel, wt


def select(doc, mask, layer=None):
    return weights(doc, mask, 0.0, layer)[0]


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


def to_json(mask):
    return dict(mask)


def from_json(obj):
    m = dict(obj)
    t = m.get("type")
    if t not in ("rect", "point", "polygon", "color", "all"):
        raise ValueError("unknown mask type %r" % (t,))
    return m
