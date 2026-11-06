"""形状能力（新增，不改原有 20 个算子）。

两个纯函数，直接往 plan doc 追加普通笔触：
  * fill_polygon   —— 用笔刷填一个多边形（主体块面/岩台/剪影起手）
  * tapered_stroke —— 沿 SVG path 画由粗到细的锥形笔（鹿角/枝干/毛流）

它们不是新笔刷，也不改渲染器；产物仍是可 diff、可 densify、可光照的普通笔触。
用法见 docs/EXAMPLES.md。
"""
import math

import numpy as np

import ops as O


def _polygon_points(polygon):
    pts = np.asarray(polygon, np.float64)
    if pts.ndim != 2 or pts.shape[0] < 3 or pts.shape[1] != 2:
        raise ValueError("polygon needs >=3 [x,y] points")
    if np.allclose(pts[0], pts[-1]):
        pts = pts[:-1]
    if len(pts) < 3:
        raise ValueError("polygon needs >=3 distinct points")
    return pts


def _point_in_polygon(x, y, pts):
    inside = False
    j = len(pts) - 1
    for i in range(len(pts)):
        xi, yi = pts[i]
        xj, yj = pts[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def fill_polygon(doc, layer, polygon, color=(217, 180, 90), size=8.0, opacity=1.0,
                 preset="round_soft", spacing=None, jitter=0.35, angle=0.0,
                 blend="normal", tag=None, guidance=1.0, seed=0, edge=True):
    """网格撒点填多边形；只在多边形内的点落笔。"""
    g = max(0.0, min(1.0, float(guidance)))
    if g <= 0.0:
        return doc
    pts = _polygon_points(polygon)
    names = [l.get("name") for l in (doc.get("layers") or [])]
    if layer not in names:
        O.add_layer(doc, layer, blend=blend)
    rgb = O._set_rgb(O._to_rgb(color))
    op = max(0.0, min(1.0, float(opacity) * g))
    size = max(0.5, float(size))
    spacing = max(0.5, float(spacing)) if spacing is not None else max(1.0, size * 0.45)
    jitter = max(0.0, min(1.0, float(jitter)))
    x0, y0 = pts.min(axis=0)
    x1, y1 = pts.max(axis=0)
    nx = int(math.floor((x1 - x0) / spacing)) + 1
    ny = int(math.floor((y1 - y0) / spacing)) + 1
    if nx * ny > 250000:
        raise ValueError("fill_polygon: too many stamps (%d); increase spacing or size" % (nx * ny))
    th = math.radians(float(angle))
    n = 0
    for iy in range(ny):
        y = y0 + (iy + 0.5) * spacing
        row_off = (0.5 * spacing) if (iy % 2) else 0.0
        for ix in range(nx):
            x = x0 + (ix + 0.5) * spacing + row_off
            u = O._u(layer, ix, iy, seed, "x")
            v = O._u(layer, ix, iy, seed, "y")
            px = x + (u - 0.5) * spacing * jitter
            py = y + (v - 0.5) * spacing * jitter
            if not _point_in_polygon(px, py, pts):
                continue
            a = th + (O._u(layer, ix, iy, seed, "a") - 0.5) * math.pi * 2.0 * jitter
            ln = max(0.5, size * 0.20)
            qx, qy = px + math.cos(a) * ln, py + math.sin(a) * ln
            s = {"layer": layer, "preset": preset,
                 "size": float(size * (0.85 + 0.30 * O._u(layer, ix, iy, seed, "s"))),
                 "opacity": op, "color": rgb,
                 "points": [[float(px), float(py), 1.0], [float(qx), float(qy), 1.0]]}
            if tag:
                s["tag"] = str(tag)
            doc["strokes"].append(s)
            n += 1
    if edge:
        for i in range(len(pts)):
            ax, ay = pts[i]
            bx, by = pts[(i + 1) % len(pts)]
            ln = math.hypot(bx - ax, by - ay)
            if ln <= 1e-9:
                continue
            m = max(1, int(math.ceil(ln / max(0.5, size * 0.18))))
            for k in range(m):
                t0, t1 = k / float(m), (k + 1) / float(m)
                x0, y0 = ax + (bx - ax) * t0, ay + (by - ay) * t0
                x1, y1 = ax + (bx - ax) * t1, ay + (by - ay) * t1
                s = {"layer": layer, "preset": preset, "size": float(size * 0.9),
                     "opacity": op, "color": rgb,
                     "points": [[float(x0), float(y0), 1.0], [float(x1), float(y1), 1.0]]}
                if tag:
                    s["tag"] = str(tag)
                doc["strokes"].append(s)
                n += 1
    return doc


def tapered_stroke(doc, layer, d, color=(217, 180, 90), size=10.0, size_end=1.0,
                  steps=24, opacity=1.0, preset="round_soft", blend="normal",
                  tag=None, guidance=1.0, seed=0):
    """沿 path 按弧长等分，每小段一笔，笔径从 size 收到 size_end。"""
    g = max(0.0, min(1.0, float(guidance)))
    if g <= 0.0:
        return doc
    polys = O._path_to_polylines(d)
    if not polys:
        return doc
    names = [l.get("name") for l in (doc.get("layers") or [])]
    if layer not in names:
        O.add_layer(doc, layer, blend=blend)
    rgb = O._set_rgb(O._to_rgb(color))
    op = max(0.0, min(1.0, float(opacity) * g))
    w0, w1 = max(0.5, float(size)), max(0.5, float(size_end))
    n = max(2, min(512, int(steps)))
    for poly in polys:
        pts = np.asarray(poly, np.float64)
        if len(pts) < 2:
            continue
        seg = np.linalg.norm(np.diff(pts, axis=0), axis=1)
        total = float(seg.sum())
        if total <= 1e-9:
            continue
        cum = np.concatenate([[0.0], np.cumsum(seg)])
        samples = []
        for k in range(n + 1):
            target = total * (k / float(n))
            idx = int(np.searchsorted(cum, target, side="right") - 1)
            idx = max(0, min(idx, len(seg) - 1))
            denom = seg[idx] if seg[idx] > 1e-12 else 1.0
            frac = max(0.0, min(1.0, (target - cum[idx]) / denom))
            x = pts[idx, 0] + (pts[idx + 1, 0] - pts[idx, 0]) * frac
            y = pts[idx, 1] + (pts[idx + 1, 1] - pts[idx, 1]) * frac
            samples.append((x, y))
        for k in range(n):
            t = (k + 0.5) / float(n)
            w = w0 + (w1 - w0) * t
            x0, y0 = samples[k]
            x1, y1 = samples[k + 1]
            s = {"layer": layer, "preset": preset, "size": float(max(0.5, w)),
                 "opacity": op, "color": rgb,
                 "points": [[float(x0), float(y0), 1.0], [float(x1), float(y1), 1.0]]}
            if tag:
                s["tag"] = str(tag)
            doc["strokes"].append(s)
    return doc
