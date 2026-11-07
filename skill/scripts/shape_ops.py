"""形状能力（新增，不改原有 20 个算子）。

这些函数是 **plan 生成 helper**，不是 ops 算子，也不改渲染器：
  * fill_polygon   —— 在多边形内撒普通笔触，做块面/剪影/岩台。
  * tapered_stroke —— 沿 SVG path 生成由粗到细的普通笔触，做枝干/鹿角/毛流。

产物就是 plan["strokes"] 里的普通笔触，所以能被 densify / relight_dir /
light_rig / tone_map 等原有算子继续处理；不是新笔刷，也不是像素填充。

三个共同约定：
  * 坐标系是 canvas 像素，原点左上，y 向下；polygon 与 path 都用这套坐标。
  * 只往 doc 追加笔触；doc 里没有目标层时自动 add_layer，已有层不改 blend。
  * 同 (doc, 参数, seed) 逐字节确定；guidance=0 时 doc 不变。

层序注意：plan_io.dump_plan 默认按“首笔出现顺序”生成 layers；可传
layer_order=[...] 显式指定（可含空层）。helper 会按调用顺序建层；如果合成
顺序重要，按最终层序调用 helper，或 dump 时传 layer_order。不要依赖 dict
顺序或脚本里的变量名。

参数含义的完整说明见 docs/SHAPE_API.md；这里只保留签名与最短契约。
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


def _signed_area(pts):
    s = 0.0
    for i in range(len(pts)):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % len(pts)]
        s += x0 * y1 - x1 * y0
    return 0.5 * s


def fill_polygon(doc, layer, polygon, color=(217, 180, 90), color2=None, size=8.0,
                 opacity=1.0, preset="round_soft", spacing=None, jitter=0.35,
                 angle=0.0, gradient_angle=0.0, hardness=0.7, blend="normal",
                 tag=None, guidance=1.0, seed=0, edge=True, edge_inset=0.0,
                 pressure=1.0, holes=None):
    """在多边形内用笔触撒点填充；只在多边形内的点落笔。

    Args:
        doc: plan dict（canvas/layers/strokes）。会被原地追加笔触。
        layer: 目标层名；不存在时自动建层，blend 只在建层时生效。
        polygon: [[x,y], ...]，>=3 个点；可凹；自动闭合；外环。
        holes: 可选 [[x,y], ...] 列表，每个是洞/内环；填充时这些区域被挖掉。
            边圈 edge 目前只补外环；洞缘要干净可另调用 fill/taper。
        color: 起始颜色，[r,g,b] 0..255 或 "#rrggbb"。
        color2: 可选终点颜色；给定时按 gradient_angle 做逐笔线性插值，
            不是像素渐变，颜色分辨率由 spacing 决定。
        size: 笔刷直径（像素）；spacing 默认 0.45*size。
        opacity: 单笔不透明度 0..1；重叠区域会叠加，不是整层统一 alpha。
        preset: 笔刷名；合法名见 paint_native.PRESETS。
        spacing: 撒点间距（像素）；越小越实、笔数越多，默认 0.45*size。
        jitter: 0..1，位置与笔向抖动；0 是规则网格，0.2-0.5 更像手绘。
        angle: 笔触方向基准（度）；也是隔行错位方向的基准。
        gradient_angle: color->color2 的渐变方向（度）；0 为 +x，90 为 +y。
        hardness: 笔刷硬度 0..1；写入每笔，越大边缘越硬。
        blend: 新层混合模式（normal/screen/...）；已有层不改。
        tag: 可选字符串，写到每笔的 tag 字段，便于后续 select_tag。
        guidance: 0..1；0 不改 doc，<1 时按比例乘 opacity。
        seed: 确定性抖动种子；同一 doc/参数/seed 结果逐字节相同。
        edge: 是否沿多边形边补一圈；True 减少圆笔触锯齿边。
        edge_inset: 边圈向多边形内缩的像素；0 表示边笔中心仍在边界上。
            想避免笔尖越过剪影，可设 edge_inset≈size*0.5，但轮廓会略缩。
        pressure: 0..1，写入每笔点的压力；默认 1.0。

    Returns:
        同一个 doc（已追加笔触）。

    Notes:
        * 这是“笔刷堆叠填充”，不是矢量填充；边缘由笔刷半径决定。
        * holes 是挖洞，不做路径布尔；多个不相交的块面请多次调用。
        * 要颜色/体积分层，调用多次、每次换 polygon/color/color2/opacity，
          或把不同遍写到不同 layer；不要指望一次 fill 产生体积。
        * 超过 250000 个撒点会报错；先增大 spacing 或 size。
    """
    g = max(0.0, min(1.0, float(guidance)))
    if g <= 0.0:
        return doc
    pts = _polygon_points(polygon)
    hole_pts = [_polygon_points(h) for h in (holes or [])]
    names = [l.get("name") for l in (doc.get("layers") or [])]
    if layer not in names:
        O.add_layer(doc, layer, blend=blend)
    rgb = O._set_rgb(O._to_rgb(color))
    rgb2 = None if color2 is None else O._set_rgb(O._to_rgb(color2))
    op = max(0.0, min(1.0, float(opacity) * g))
    size = max(0.5, float(size))
    spacing = max(0.5, float(spacing)) if spacing is not None else max(1.0, size * 0.45)
    jitter = max(0.0, min(1.0, float(jitter)))
    hard = max(0.0, min(1.0, float(hardness)))
    pr = max(0.0, min(1.0, float(pressure)))
    inset = max(0.0, float(edge_inset))
    x0, y0 = pts.min(axis=0)
    x1, y1 = pts.max(axis=0)
    nx = int(math.floor((x1 - x0) / spacing)) + 1
    ny = int(math.floor((y1 - y0) / spacing)) + 1
    if nx * ny > 250000:
        raise ValueError("fill_polygon: too many stamps (%d); increase spacing or size" % (nx * ny))
    th = math.radians(float(angle))

    ga = math.radians(float(gradient_angle))
    gx, gy = math.cos(ga), math.sin(ga)
    proj = pts[:, 0] * gx + pts[:, 1] * gy
    pmin, pmax = float(proj.min()), float(proj.max())
    span = max(1e-6, pmax - pmin)

    def color_at(x, y):
        if rgb2 is None:
            return rgb
        t = max(0.0, min(1.0, ((x * gx + y * gy) - pmin) / span))
        return [round(a + (b - a) * t, 2) for a, b in zip(rgb, rgb2)]

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
            if any(_point_in_polygon(px, py, h) for h in hole_pts):
                continue
            a = th + (O._u(layer, ix, iy, seed, "a") - 0.5) * math.pi * 2.0 * jitter
            ln = max(0.5, size * 0.20)
            qx, qy = px + math.cos(a) * ln, py + math.sin(a) * ln
            s = {"layer": layer, "preset": preset,
                 "size": float(size * (0.85 + 0.30 * O._u(layer, ix, iy, seed, "s"))),
                 "opacity": op, "color": color_at(px, py), "hardness": hard,
                 "points": [[float(px), float(py), pr], [float(qx), float(qy), pr]]}
            if tag:
                s["tag"] = str(tag)
            doc["strokes"].append(s)

    if edge:
        sign = 1.0 if _signed_area(pts) > 0 else -1.0
        for i in range(len(pts)):
            ax, ay = pts[i]
            bx, by = pts[(i + 1) % len(pts)]
            dx, dy = bx - ax, by - ay
            ln = math.hypot(dx, dy)
            if ln <= 1e-9:
                continue
            nxv, nyv = (-dy / ln) * sign, (dx / ln) * sign
            ax, ay = ax + nxv * inset, ay + nyv * inset
            bx, by = bx + nxv * inset, by + nyv * inset
            m = max(1, int(math.ceil(ln / max(0.5, size * 0.18))))
            for k in range(m):
                t0, t1 = k / float(m), (k + 1) / float(m)
                x0e, y0e = ax + (bx - ax) * t0, ay + (by - ay) * t0
                x1e, y1e = ax + (bx - ax) * t1, ay + (by - ay) * t1
                s = {"layer": layer, "preset": preset, "size": float(size * 0.9),
                     "opacity": op, "color": color_at((x0e + x1e) * 0.5, (y0e + y1e) * 0.5),
                     "hardness": hard,
                     "points": [[float(x0e), float(y0e), pr], [float(x1e), float(y1e), pr]]}
                if tag:
                    s["tag"] = str(tag)
                doc["strokes"].append(s)
    return doc


def assemble_parts(doc, layer, parts, color=(217, 180, 90), color2=None,
                   size=8.0, opacity=1.0, preset="round_soft", spacing=None,
                   jitter=0.35, angle=0.0, gradient_angle=0.0, hardness=0.7,
                   blend="normal", guidance=1.0, seed=0, edge=True,
                   edge_inset=0.0, far_near_delta=0.0):
    """Fill a model-authored list of parts in z order; no body-part schema.

    parts: list of dicts. Each dict may contain:
      polygon  required [[x,y], ...]
      name     optional string, used for tag when no tag is given
      z        draw order within this call (default 0; lower first)
      value    -1..1, multiplied by far_near_delta for a simple near/far shift
      color / color2 / size / opacity / spacing / preset / hardness /
      edge / edge_inset / holes / tag / pressure
    Missing keys fall back to the function arguments.

    This helper only fills the parts the caller wrote. It does not know
    body/head/leg names, proportions, or occlusion semantics; those are the
    caller's geometry. The same layer is used for all parts, so z controls
    brush order, not layer order.
    """
    g = max(0.0, min(1.0, float(guidance)))
    if g <= 0.0:
        return doc
    ordered = sorted(enumerate(parts or []),
                     key=lambda kv: (float(kv[1].get("z", 0.0)), kv[0]))
    base_color = O._to_rgb(color)
    base_color2 = None if color2 is None else O._to_rgb(color2)
    for seq, (_, part) in enumerate(ordered):
        poly = part.get("polygon")
        if not poly:
            continue
        val = max(-1.0, min(1.0, float(part.get("value", 0.0))))
        shift = float(far_near_delta) * val

        def shifted(c):
            if c is None:
                return None
            rgb = O._to_rgb(c)
            return [max(0.0, min(255.0, float(x) + shift)) for x in rgb]

        part_name = part.get("name")
        tag = part.get("tag")
        if tag is None and part_name:
            tag = "part:" + str(part_name)
        fill_polygon(
            doc, layer, poly,
            color=shifted(part.get("color", base_color)),
            color2=shifted(part.get("color2", base_color2)),
            size=part.get("size", size),
            opacity=part.get("opacity", opacity),
            preset=part.get("preset", preset),
            spacing=part.get("spacing", spacing),
            jitter=part.get("jitter", jitter),
            angle=part.get("angle", angle),
            gradient_angle=part.get("gradient_angle", gradient_angle),
            hardness=part.get("hardness", hardness),
            blend=blend,
            tag=tag,
            guidance=g,
            seed=int(part.get("seed", seed + seq)),
            edge=part.get("edge", edge),
            edge_inset=part.get("edge_inset", edge_inset),
            pressure=part.get("pressure", 1.0),
            holes=part.get("holes"),
        )
    return doc


def tapered_stroke(doc, layer, d, color=(217, 180, 90), size=10.0, size_end=1.0,
                  steps=24, opacity=1.0, preset="round_soft", blend="normal",
                  tag=None, guidance=1.0, seed=0, copies=1, spread=0.0,
                  hardness=0.7, pressure=1.0, pressure_end=None):
    """沿 SVG path 生成由粗到细的笔触，用于枝干/鹿角/毛流。

    Args:
        doc: plan dict；原地追加笔触。
        layer: 目标层名；不存在时自动建层，blend 只在建层时生效。
        d: SVG path 字符串，语法同 draw_path（M/L/H/V/C/S/Q/T/Z；A 近似）。
        color: 颜色 [r,g,b] 或 "#rrggbb"；所有段同色。
        size: 起点笔径（像素）。
        size_end: 终点笔径（像素）；要尖端细，size_end < size。
        steps: 分段数 2..512；越大越连续，笔触数 = steps * copies * 折线数。
        opacity: 单笔不透明度 0..1；重叠会叠加。
        preset: 笔刷名；合法名见 paint_native.PRESETS。
        blend: 新层混合模式；已有层不改。
        tag: 可选 tag，写到每笔。
        guidance: 0..1；0 不改 doc，<1 时按比例乘 opacity。
        seed: 保留参数；当前实现无随机抖动，但同参数结果确定。
        copies: >=1；沿法线并行复制几笔，用于鹿角/枝干的厚度。
        spread: copies>1 时相邻副本的法线间距（像素）；0 则重叠。
        hardness: 笔刷硬度 0..1；写入每笔。
        pressure: 起点压力 0..1；写入每段点的压力（渲染器会读）。
        pressure_end: 终点压力 0..1；缺省 = pressure（等压）。给定时沿路径线性插值。

    Returns:
        同一个 doc（已追加笔触）。

    Notes:
        * 每小段是一笔普通笔触，圆头；相邻段重叠形成连续锥形。
        * 单笔径 taper 不会自动产生体积；copies/spread 并行多笔才有厚度。
        * pressure 与 size 是两套变化：size 改笔宽，pressure 改渲染压力。
        * 路径太长/太弯时，增大 steps 改善连续性，代价是笔数。
    """
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
    cnum = max(1, min(64, int(copies)))
    gap = float(spread)
    hard = max(0.0, min(1.0, float(hardness)))
    p0 = max(0.0, min(1.0, float(pressure)))
    p1 = p0 if pressure_end is None else max(0.0, min(1.0, float(pressure_end)))
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
        for c in range(cnum):
            off = (c - (cnum - 1) * 0.5) * gap
            for k in range(n):
                t = (k + 0.5) / float(n)
                w = w0 + (w1 - w0) * t
                x0, y0 = samples[k]
                x1, y1 = samples[k + 1]
                dx, dy = x1 - x0, y1 - y0
                ln = math.hypot(dx, dy)
                if ln > 1e-9:
                    nxv, nyv = -dy / ln, dx / ln
                else:
                    nxv, nyv = 0.0, 0.0
                x0, y0 = x0 + nxv * off, y0 + nyv * off
                x1, y1 = x1 + nxv * off, y1 + nyv * off
                pr = p0 + (p1 - p0) * t
                s = {"layer": layer, "preset": preset, "size": float(max(0.5, w)),
                     "opacity": op, "color": rgb, "hardness": hard,
                     "points": [[float(x0), float(y0), pr], [float(x1), float(y1), pr]]}
                if tag:
                    s["tag"] = str(tag)
                doc["strokes"].append(s)
    return doc
