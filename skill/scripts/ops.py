"""计划空间编辑算子：把「改画指令」变成对 plan doc 的确定性改动。

设计对应扩散模型里的条件编辑，但全部落在可见、可 diff 的结构上：

    扩散里                         这里
    --------                       ----
    latent 空间                    plan doc（canvas / layers / strokes）
    文本条件 (cross-attention)     算子 dict -> 直接改字段
    CFG 引导强度                   guidance   ∈ [0,1]
    SDEdit 噪声强度                redraw_fraction ∈ [0,1]
    masked inpainting              masks.py 的 selection + weights
    采样调度器                     不需要（没有随机采样）

硬约束（测试守住）：
  * 纯函数：返回新 doc，不改入参
  * 确定性：同 (doc, ops, seed) 逐字节相同（见 canonical/fingerprint）
  * guidance=0 或 mask 为空 -> doc 逐字节不变
  * selection 之外的笔触一个字段都不动

用法：
    doc2 = apply(doc, [{"op": "set_layer_hue", "layer": "petal", "hue": 220}])
    sel, w = masks.weights(doc, masks.rect(20, 20, 80, 60), feather=8)
    doc3 = apply(doc, [{"op": "densify", "layer": "petal", "k": 1.3}], selection=sel, weights=w)
"""
import copy
import colorsys
import hashlib
import json

import numpy as np


# ---------- 确定性工具 ----------

def _u(*parts):
    """稳定哈希 -> [0,1)。不用 Python hash()（有随机盐）。"""
    key = "|".join(str(p) for p in parts).encode("utf-8")
    return int.from_bytes(hashlib.blake2b(key, digest_size=4).digest(), "big") / 2.0 ** 32


def _seed_int(*parts):
    key = "|".join(str(p) for p in parts).encode("utf-8")
    return int.from_bytes(hashlib.blake2b(key, digest_size=8).digest(), "big") % (2 ** 31)


def canonical(doc):
    return json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def fingerprint(doc):
    return hashlib.sha256(canonical(doc).encode("utf-8")).hexdigest()[:16]


def _strength(guidance, w):
    return max(0.0, min(1.0, float(guidance) * float(w)))


def _to_rgb(c):
    if isinstance(c, str):
        c = c.lstrip("#")
        return np.array([int(c[i:i + 2], 16) for i in (0, 2, 4)], np.float64)
    return np.asarray(c, np.float64)[:3]


def _set_rgb(hexlike):
    return [round(float(v), 2) for v in hexlike]


def _hsv(rgb255):
    r, g, b = (np.asarray(rgb255, np.float64) / 255.0).tolist()
    return colorsys.rgb_to_hsv(r, g, b)


def _from_hsv(h, s, v):
    r, g, b = colorsys.hsv_to_rgb(float(h) % 1.0, max(0.0, min(1.0, s)), max(0.0, min(1.0, v)))
    return np.array([r, g, b], np.float64) * 255.0


def _targets(doc, layer, selection, weights, guidance):
    """产出 (下标, 笔触, 强度)；强度 = guidance × 该笔掩膜权重。"""
    wmap = {}
    if selection is not None and weights is not None:
        wmap = {int(i): float(w) for i, w in zip(selection, weights)}
    sellist = None if selection is None else set(int(i) for i in selection)
    for i, s in enumerate(doc["strokes"]):
        if layer is not None and s["layer"] != layer:
            continue
        if sellist is not None and i not in sellist:
            continue
        st = _strength(guidance, wmap.get(i, 1.0))
        if st > 0:
            yield i, s, st


# ---------- 算子 ----------

def set_layer_hue(doc, layer, hue, guidance=1.0, selection=None, weights=None, seed=0, **_):
    """色相旋转到目标角度（沿最短弧插值，guidance=1 精确命中）。"""
    n = 0
    for _, s, st in _targets(doc, layer, selection, weights, guidance):
        h, sat, v = _hsv(_to_rgb(s["color"]))
        target = (float(hue) % 360.0) / 360.0
        dh = ((target - h + 0.5) % 1.0) - 0.5
        s["color"] = _set_rgb(_from_hsv(h + dh * st, sat, v))
        n += 1
    return doc


def set_layer_saturation(doc, layer, sat, guidance=1.0, selection=None, weights=None, seed=0, **_):
    """饱和度插值到绝对值（0..1）。"""
    for _, s, st in _targets(doc, layer, selection, weights, guidance):
        h, cur, v = _hsv(_to_rgb(s["color"]))
        s["color"] = _set_rgb(_from_hsv(h, cur + (float(sat) - cur) * st, v))
    return doc


def tone_map(doc, gain=1.0, lift=0.0, gamma=1.0, layer=None, guidance=1.0, selection=None,
             weights=None, seed=0, **_):
    """色调映射：c' = 255 * clamp(((c/255) ** (1/gamma)) * gain + lift)。

    改的是笔触颜色，所以 multiply 层（shadow/texture）的压暗也会一起被提起来——
    这是"整幅偏暗"的通用修法。gain=1 / lift=0 / gamma=1 时逐字节不变；
    guidance<1 时按强度在旧值与新值之间插值。
    """
    gain, lift, gamma = float(gain), float(lift), float(gamma)
    if gain <= 0:
        raise ValueError("tone_map: gain must be > 0")
    if gamma <= 0:
        raise ValueError("tone_map: gamma must be > 0")
    if gain == 1.0 and lift == 0.0 and gamma == 1.0:
        return doc
    for _, s, st in _targets(doc, layer, selection, weights, guidance):
        cur = np.clip(_to_rgb(s["color"]), 0.0, 255.0) / 255.0
        target = np.clip(np.power(cur, 1.0 / gamma) * gain + lift, 0.0, 1.0) * 255.0
        s["color"] = _set_rgb(np.clip(_to_rgb(s["color"]) + (target - _to_rgb(s["color"])) * st, 0.0, 255.0))
    return doc


def set_layer_brightness(doc, layer, factor, guidance=1.0, selection=None, weights=None, seed=0, **_):
    """明度乘因子（factor=1 不变，guidance=1 时精确乘 factor）。"""
    for _, s, st in _targets(doc, layer, selection, weights, guidance):
        h, sat, v = _hsv(_to_rgb(s["color"]))
        eff = 1.0 + (float(factor) - 1.0) * st
        s["color"] = _set_rgb(_from_hsv(h, sat, v * eff))
    return doc


def scale_stroke_size(doc, layer, k, guidance=1.0, selection=None, weights=None, seed=0, **_):
    """笔触尺寸乘 k（k=1 逐字节不变）。"""
    for _, s, st in _targets(doc, layer, selection, weights, guidance):
        s["size"] = float(s["size"]) * (1.0 + (float(k) - 1.0) * st)
    return doc


def swap_brush(doc, layer, preset, guidance=1.0, selection=None, weights=None, seed=0, **_):
    """按强度逐笔替换笔刷 preset（离散步：强度即命中概率，稳定哈希决定）。"""
    for i, s, st in _targets(doc, layer, selection, weights, guidance):
        if _u(seed, "brush", i) < st:
            s["preset"] = str(preset)
    return doc


def densify(doc, layer, k, guidance=1.0, selection=None, weights=None, seed=0, **_):
    """在选中笔触旁追加子笔触（k>1；k=1 逐字节不变）。"""
    targets = list(_targets(doc, layer, selection, weights, guidance))
    if not targets:
        return doc
    order = sorted(targets, key=lambda t: _u(seed, "densify", t[0]))
    extra_total = int(round((float(k) - 1.0) * sum(t[2] for t in targets)))
    if extra_total <= 0:
        return doc
    per = {}
    for rank in range(extra_total):
        i = order[rank % len(order)][0]
        per[i] = per.get(i, 0) + 1
    out = []
    for i, s in enumerate(doc["strokes"]):
        out.append(s)
        if i not in per:
            continue
        pts = np.asarray(s["points"], np.float64)
        rng = np.random.default_rng(_seed_int(seed, "child", i))
        for c in range(per[i]):
            shift = rng.normal(0.0, 0.35, size=2) * float(s["size"])
            child = copy.deepcopy(s)
            cp = pts.copy()
            cp[:, 0] += shift[0]
            cp[:, 1] += shift[1]
            child["points"] = [[round(float(v), 2) for v in pt] for pt in cp.tolist()]
            child["size"] = float(s["size"]) * 0.85
            child["opacity"] = float(s["opacity"]) * 0.9
            out.append(child)
    doc["strokes"] = out
    doc["count"] = len(out)
    return doc


def erase(doc, layer=None, guidance=1.0, selection=None, weights=None, seed=0, **_):
    """删除笔触（强度即命中概率）。selection=None 且 guidance=1 等于清掉整层。"""
    drop = set()
    for i, _, st in _targets(doc, layer, selection, weights, guidance):
        if _u(seed, "erase", i) < st:
            drop.add(i)
    if not drop:
        return doc
    doc["strokes"] = [s for i, s in enumerate(doc["strokes"]) if i not in drop]
    doc["count"] = len(doc["strokes"])
    return doc



# ---------- 从零加笔：SVG path 展平 ----------

def _path_tokens(d):
    import re
    return re.findall(r"[MmLlHhVvCcSsQqTtZz]|[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?", str(d))


def _path_to_polylines(d, steps=16):
    """SVG path -> 折线列表（M/L/H/V/C/S/Q/T/Z；弧线 A 按直线近似）。"""
    toks = _path_tokens(d)
    subs, cur = [], []
    x = y = 0.0
    sx = sy = 0.0
    last_c2 = None
    last_q = None
    i = 0
    cmd = None

    def flush():
        nonlocal cur
        if cur:
            subs.append(cur)
        cur = []

    def add(px, py):
        nonlocal x, y
        cur.append([px, py])
        x, y = px, py

    def take():
        nonlocal i
        if i >= len(toks):
            raise ValueError("path ended early (missing coordinate)")
        v = float(toks[i])
        i += 1
        return v

    def cubic(c1x, c1y, c2x, c2y, px, py):
        nonlocal x, y
        x0, y0 = x, y
        for k in range(1, steps + 1):
            t = k / steps
            mt = 1.0 - t
            cur.append([mt**3*x0 + 3*mt*mt*t*c1x + 3*mt*t*t*c2x + t**3*px,
                        mt**3*y0 + 3*mt*mt*t*c1y + 3*mt*t*t*c2y + t**3*py])
        x, y = px, py

    def quad(cx, cy, px, py):
        nonlocal x, y
        x0, y0 = x, y
        for k in range(1, steps + 1):
            t = k / steps
            mt = 1.0 - t
            cur.append([mt*mt*x0 + 2*mt*t*cx + t*t*px,
                        mt*mt*y0 + 2*mt*t*cy + t*t*py])
        x, y = px, py

    while i < len(toks):
        t = toks[i]
        if t.isalpha():
            cmd = t
            i += 1
            if cmd in "Zz":
                if cur:
                    add(sx, sy)
                flush()
                last_c2 = last_q = None
                cmd = None
            continue
        if cmd is None:
            raise ValueError("path number without command")
        up = cmd.upper()
        rel = cmd.islower()
        ox, oy = (x, y) if rel else (0.0, 0.0)
        if up == "M":
            px, py = take() + ox, take() + oy
            flush()
            add(px, py)
            sx, sy = px, py
            cmd = "l" if rel else "L"
            last_c2 = last_q = None
        elif up == "L":
            add(take() + ox, take() + oy)
            last_c2 = last_q = None
        elif up == "H":
            add(take() + ox, y)
            last_c2 = last_q = None
        elif up == "V":
            add(x, take() + oy)
            last_c2 = last_q = None
        elif up == "C":
            c1x, c1y = take() + ox, take() + oy
            c2x, c2y = take() + ox, take() + oy
            px, py = take() + ox, take() + oy
            cubic(c1x, c1y, c2x, c2y, px, py)
            last_c2 = (c2x, c2y)
            last_q = None
        elif up == "S":
            if last_c2 is None:
                c1x, c1y = x, y
            else:
                c1x, c1y = 2.0 * x - last_c2[0], 2.0 * y - last_c2[1]
            c2x, c2y = take() + ox, take() + oy
            px, py = take() + ox, take() + oy
            cubic(c1x, c1y, c2x, c2y, px, py)
            last_c2 = (c2x, c2y)
            last_q = None
        elif up == "Q":
            cx, cy = take() + ox, take() + oy
            px, py = take() + ox, take() + oy
            quad(cx, cy, px, py)
            last_q = (cx, cy)
            last_c2 = None
        elif up == "T":
            if last_q is None:
                cx, cy = x, y
            else:
                cx, cy = 2.0 * x - last_q[0], 2.0 * y - last_q[1]
            px, py = take() + ox, take() + oy
            quad(cx, cy, px, py)
            last_q = (cx, cy)
            last_c2 = None
        elif up == "A":
            take(); take(); take(); take(); take()
            add(take() + ox, take() + oy)
            last_c2 = last_q = None
        else:
            raise ValueError("unsupported path command %r" % cmd)
    flush()
    return subs


def draw_path(doc, layer, d, color=(217, 180, 90), size=6.0, opacity=1.0,
              preset="round_soft", guidance=1.0, tag=None, selection=None, weights=None, seed=0, **_):
    """从零加笔：把 SVG path 展平成笔触，追加到指定层（不依赖参考图）。

    guidance=0 → 计划逐字节不变；guidance<1 时新笔触不透明度按比例衰减。
    坐标就是 canvas 坐标，所以掩膜/脏矩形可以直接算 bbox。
    """
    g = _strength(guidance, 1.0)
    if g <= 0.0:
        return doc
    polys = _path_to_polylines(d)
    if not polys:
        return doc
    names = [l.get("name") for l in (doc.get("layers") or [])]
    if layer not in names:
        raise ValueError("unknown layer %r; known: %s" % (layer, ", ".join(str(n) for n in names)))
    rgb = _set_rgb(_to_rgb(color))
    op = max(0.0, min(1.0, float(opacity) * g))
    for poly in polys:
        pts = [[float(px), float(py), 1.0] for px, py in poly]
        if len(pts) == 1:
            pts.append([pts[0][0] + 1e-3, pts[0][1] + 1e-3, 1.0])
        st = {"layer": layer, "preset": preset, "size": float(size),
              "opacity": op, "color": rgb, "points": pts}
        if tag:
            st["tag"] = str(tag)
        doc["strokes"].append(st)
    return doc


def _stroke_bbox(s):
    pts = np.asarray(s["points"], np.float64)
    half = float(s.get("size", 1.0)) * 0.5
    return (float(pts[:, 0].min()) - half, float(pts[:, 1].min()) - half,
            float(pts[:, 0].max()) + half, float(pts[:, 1].max()) + half)


def _stroke_area(s):
    x0, y0, x1, y1 = _stroke_bbox(s)
    return max(0.0, x1 - x0) * max(0.0, y1 - y0)


def redraw(doc, fraction=1.0, jitter=0.15, layer=None, guidance=1.0, order="random",
           selection=None, weights=None, seed=0, **_):
    """重抽一部分笔触的参数（SDEdit 类比：改多少由 fraction 决定）。

    fraction=0 逐字节不变；fraction=1 选中笔触全部重抽；被改笔触数 = round(fraction·N)。
    """
    targets = list(_targets(doc, layer, selection, weights, guidance))
    if not targets:
        return doc
    if order == "coverage":          # 覆盖度优先：bbox 面积大的笔触先改
        picked = sorted(targets, key=lambda t: (-_stroke_area(t[1]), t[0]))
    elif order == "layer":           # 层序优先：计划里靠前的层先改
        names = [l.get("name") for l in (doc.get("layers") or [])]
        rank = {nm: k for k, nm in enumerate(names)}
        picked = sorted(targets, key=lambda t: (rank.get(t[1].get("layer"), len(rank)), t[0]))
    elif order == "zorder":          # z 序优先：上层/后画的先改
        picked = sorted(targets, key=lambda t: (-t[0],))
    elif order == "random":          # 旧行为：稳定哈希抽样（可复现）
        picked = sorted(targets, key=lambda t: _u(seed, "redraw", t[0]))
    else:
        raise ValueError("order must be random/coverage/layer/zorder, got %r" % (order,))
    n = int(round(float(fraction) * len(targets)))
    if n <= 0:
        return doc
    for i, s, _st in picked[:n]:
        rng = np.random.default_rng(_seed_int(seed, "redraw", i))
        s["size"] = float(s["size"]) * (1.0 + float(jitter) * rng.uniform(-1.0, 1.0))
        s["opacity"] = max(0.0, min(1.0, float(s["opacity"]) * (1.0 + float(jitter) * rng.uniform(-1.0, 1.0))))
        h, sat, v = _hsv(_to_rgb(s["color"]))
        s["color"] = _set_rgb(_from_hsv(h + float(jitter) * 0.1 * rng.uniform(-1.0, 1.0),
                                        sat, v * (1.0 + float(jitter) * rng.uniform(-1.0, 1.0))))
    return doc


def recolor_palette(doc, palette, guidance=1.0, selection=None, weights=None, seed=0, **_):
    """把颜色吸附到给定色板（取最近的，再按强度插值）。"""
    pal = np.asarray(palette, np.float64)
    if pal.ndim != 2 or pal.shape[1] != 3:
        raise ValueError("palette must be [[r,g,b], ...]")
    for _, s, st in _targets(doc, None, selection, weights, guidance):
        cur = _to_rgb(s["color"])
        j = int(np.argmin(((pal - cur) ** 2).sum(axis=1)))
        s["color"] = _set_rgb(cur + (pal[j] - cur) * st)
    return doc


def split_shading(doc, layer=None, guidance=1.0, selection=None, weights=None, seed=0, **_):
    """把笔触颜色拆成 albedo（材质反照率）+ shade（受光量），存在笔触字段里。

    intrinsic 的笔画级近似：同层内按 luma 归一化——shade = luma / 层内 luma 均值，
    albedo = color / shade。拆完之后改光照用 set_light 只动 shade，
    颜色的色相/材质（albedo）保持不变，这是"改光照而不是重新上色"的最小实现。

    可重复执行：已拆过的笔触会重新按当前颜色计算（幂等）。
    """
    import numpy as _np

    def _luma(c):
        r, g, b = _to_rgb(c)
        return float(0.2126 * r + 0.7152 * g + 0.0722 * b)

    per_layer = {}
    for s in doc["strokes"]:
        if layer is not None and s["layer"] != layer:
            continue
        per_layer.setdefault(s["layer"], []).append(_luma(s["color"]))
    mean = {k: (sum(v) / max(1, len(v))) for k, v in per_layer.items()}
    n = 0
    for i, s, st in _targets(doc, layer, selection, weights, guidance):
        lum = _luma(s["color"])
        m = max(mean.get(s["layer"], lum), 1e-6)
        shade = lum / m
        albedo = _np.clip(_np.asarray(_to_rgb(s["color"])) / max(shade, 0.05), 0.0, 255.0)
        if "albedo" not in s:
            s["albedo"] = [float(x) for x in albedo]
        s["shade"] = float(shade)
        n += 1
    return doc


def set_light(doc, scale=1.0, tint=None, layer=None, guidance=1.0, selection=None,
              weights=None, seed=0, **_):
    """只改光照：color = albedo × shade × scale × tint（需要先 split_shading）。

    scale=1 且 tint=None 时逐字节不变。tint 是 [r,g,b] 乘子（暖光用 [1.1,1.0,0.85] 之类）。
    """
    import numpy as _np

    scale = float(scale)
    if scale < 0:
        raise ValueError("set_light: scale must be >= 0")
    t = _np.ones(3) if tint is None else _np.asarray(tint, _np.float64)[:3]
    if t.shape != (3,) or (t <= 0).any():
        raise ValueError("set_light: tint must be 3 positive numbers")
    if abs(scale - 1.0) < 1e-12 and bool((t == 1.0).all()):
        return doc
    n = 0
    for _, s, st in _targets(doc, layer, selection, weights, guidance):
        if "albedo" not in s or "shade" not in s:
            raise ValueError("set_light 需要先 split_shading（笔触缺少 albedo/shade）")
        base = _np.asarray(s["albedo"], _np.float64) * float(s["shade"])
        shade = float(s["shade"]) * (1.0 + (scale - 1.0) * st)
        tt = 1.0 + (t - 1.0) * st
        col = _np.clip(_np.asarray(s["albedo"], _np.float64) * shade * tt, 0.0, 255.0)
        s["color"] = _set_rgb(col)
        s["shade"] = shade
        n += 1
    return doc


def replan_region(doc, ref, regions=None, layers=("band", "highlight"), mode="cover", thr=0.9,
                  ring="auto", highlight_mode="keep", shadow_mode="drop", style_file=None,
                  gpu=False, mask_obj=None, **_):
    """区域重规划算子：用参考图把区域内的笔触重画一遍（区域外原样保留）。

    掩膜走 selection 语义（mask / mask_file / polygon / instance / all），
    所以"把这片区域照参考图重画"可以写成一条 op，而不是跑一个旁路脚本。
    """
    if mask_obj is None:
        raise ValueError("replan_region 需要区域掩膜（mask / mask_file / polygon / instance / all）")
    import json as _json
    import tempfile
    from pathlib import Path as _Path

    import plan_io
    import planner_region as PR

    with tempfile.TemporaryDirectory() as td:
        td = _Path(td)
        plan_in = td / "in.plan.json"
        plan_in.write_text(_json.dumps(doc), encoding="utf-8")
        mask_p = td / "mask.json"
        mask_p.write_text(_json.dumps(mask_obj, ensure_ascii=False), encoding="utf-8")
        out_p = td / "out.plan.json"
        PR.run(str(plan_in), str(out_p), str(mask_p), list(layers), ref=ref, regions=regions,
               style_file=style_file, highlight_mode=highlight_mode, shadow_mode=shadow_mode,
               mode=mode, thr=thr, ring=ring, gpu=gpu, threads=8)
        new_doc, _w, _h = plan_io.load_plan(out_p)
    return new_doc


def relight_dir(doc, azimuth=315.0, elevation=40.0, strength=0.8, blur=0, mode="dome", layer=None,
                guidance=1.0, selection=None, weights=None, seed=0, **_):
    """方向光：模型看图说"光从左上/右后来"，这个算子把方位角变成确定性的 shade 重算。

    法线是"图像平面伪法线"，由 shade 场的梯度推出来（n ∝ (-∂shade/∂x, -∂shade/∂y, 1)）：
    亮暗变化的走向决定了小平面朝向，再与光方向做 Lambert 点积。
    这是笔画级近似，能表达侧逆光的方向差异，但不是三维几何——已知边界。
    """
    import cv2
    import numpy as _np

    targets = list(_targets(doc, layer, selection, weights, guidance))
    if not targets:
        return doc
    for _, s0, _st in targets:
        if "albedo" not in s0 or "shade" not in s0:
            raise ValueError("relight_dir 需要先 split_shading（笔触缺少 albedo/shade）")

    xs = [p[0] for _, s0, _ in targets for p in s0["points"]]
    ys = [p[1] for _, s0, _ in targets for p in s0["points"]]
    w = int(max(xs)) + 2
    h = int(max(ys)) + 2
    if mode == "dome":
        # 球面伪法线：把主体当成一个椭球，法线由笔触相对主体中心的位置推出来。
        # 这不是三维重建，但方向光该有的"亮面朝光源走"一定成立（可验证）。
        cx, cy = (min(xs) + max(xs)) * 0.5, (min(ys) + max(ys)) * 0.5
        rx = max(1.0, (max(xs) - min(xs)) * 0.5)
        ry = max(1.0, (max(ys) - min(ys)) * 0.5)
        az0 = _np.deg2rad(float(azimuth))
        el0 = _np.deg2rad(float(elevation))
        L0 = _np.array([_np.cos(az0) * _np.cos(el0), -_np.sin(az0) * _np.cos(el0), _np.sin(el0)])
        # 先算一遍 lam，再按选区均值归一——否则换方向会顺带把整体亮度压低 15%（实测过）
        lams = []
        for _, s0, _st in targets:
            px = sum(p[0] for p in s0["points"]) / len(s0["points"])
            py = sum(p[1] for p in s0["points"]) / len(s0["points"])
            nx_, ny_ = (px - cx) / rx, (py - cy) / ry
            nz_ = 1.2
            nm = (nx_ * nx_ + ny_ * ny_ + nz_ * nz_) ** 0.5
            lams.append((nx_ / nm) * L0[0] + (ny_ / nm) * L0[1] + (nz_ / nm) * L0[2])
        mean_lam = float(_np.mean(lams)) if lams else 0.0
        n = 0
        for k, (_, s0, st) in enumerate(targets):
            px = sum(p[0] for p in s0["points"]) / len(s0["points"])
            py = sum(p[1] for p in s0["points"]) / len(s0["points"])
            rel = (lams[k] - mean_lam) / max(abs(mean_lam), 1e-6)
            new_shade = float(s0["shade"]) * (1.0 + rel * float(strength) * st)
            new_shade = max(0.02, min(8.0, new_shade))
            s0["color"] = _set_rgb(_np.clip(_np.asarray(s0["albedo"], _np.float64) * new_shade, 0.0, 255.0))
            s0["shade"] = new_shade
            n += 1
        return doc
    if mode != "gradient":
        raise ValueError("relight_dir: mode must be dome or gradient")
    acc = _np.zeros((h, w), _np.float64)
    cnt = _np.zeros((h, w), _np.float64)
    for _, s0, _ in targets:
        sh = float(s0["shade"])
        for p in s0["points"]:
            xi = min(w - 1, max(0, int(p[0])))
            yi = min(h - 1, max(0, int(p[1])))
            acc[yi, xi] += sh
            cnt[yi, xi] += 1.0
    field = acc / _np.maximum(cnt, 1.0)
    k = max(3, int(blur) | 1)
    field = cv2.GaussianBlur(field, (k, k), 0)
    gy, gx = _np.gradient(field)
    sc = 8.0 / max(1e-6, float(_np.abs(_np.stack([gx, gy])).max())) * 0.5
    nx, ny = -gx * sc, -gy * sc
    nz = _np.ones_like(nx)
    norm = _np.sqrt(nx * nx + ny * ny + nz * nz)
    nx, ny, nz = nx / norm, ny / norm, nz / norm
    az = _np.deg2rad(float(azimuth))
    el = _np.deg2rad(float(elevation))
    L = _np.array([_np.cos(az) * _np.cos(el), -_np.sin(az) * _np.cos(el), _np.sin(el)])
    lam = _np.clip(nx * L[0] + ny * L[1] + nz * L[2], 0.0, None)
    lam_mean = float(lam[cnt > 0].mean()) if (cnt > 0).any() else float(lam.mean())
    lam = lam / max(lam_mean, 1e-6)

    n = 0
    for _, s0, st in targets:
        shade = 0.0
        for p in s0["points"]:
            xi = min(w - 1, max(0, int(p[0])))
            yi = min(h - 1, max(0, int(p[1])))
            shade += float(lam[yi, xi])
        shade /= max(1, len(s0["points"]))
        new_shade = float(s0["shade"]) * (1.0 + (shade - 1.0) * float(strength) * st)
        new_shade = max(0.02, min(8.0, new_shade))
        col = _np.clip(_np.asarray(s0["albedo"], _np.float64) * new_shade, 0.0, 255.0)
        s0["color"] = _set_rgb(col)
        s0["shade"] = new_shade
        n += 1
    return doc


OPS = {
    "relight_dir": relight_dir,
    "replan_region": replan_region,
    "split_shading": split_shading,
    "set_light": set_light,
    "set_layer_hue": set_layer_hue,
    "tone_map": tone_map,
    "set_layer_saturation": set_layer_saturation,
    "set_layer_brightness": set_layer_brightness,
    "scale_stroke_size": scale_stroke_size,
    "swap_brush": swap_brush,
    "densify": densify,
    "erase": erase,
    "redraw": redraw,
    "recolor_palette": recolor_palette,
    "draw_path": draw_path,
}

# 每个算子需要的必填键（编译器/校验用）
REQUIRED = {
    "set_layer_hue": ["layer", "hue"],
    "set_layer_saturation": ["layer", "sat"],
    "set_layer_brightness": ["layer", "factor"],
    "scale_stroke_size": ["layer", "k"],
    "swap_brush": ["layer", "preset"],
    "densify": ["layer", "k"],
    "erase": [],
    "redraw": [],
    "recolor_palette": ["palette"],
    "draw_path": ["layer", "d"],
    "tone_map": [],
    "split_shading": [],
    "set_light": [],
    "replan_region": ["ref"],
    "relight_dir": [],
}


# 允许的键（编译器给的 JSON 里出现别的键一律拒绝——模型最容易在这里瞎编）
ALLOWED = {
    "relight_dir": {"azimuth", "elevation", "strength", "blur", "mode", "layer", "guidance"},
    "replan_region": {"ref", "regions", "layers", "mode", "thr", "ring", "highlight_mode",
                      "shadow_mode", "style_file", "gpu"},
    "set_layer_hue": {"layer", "hue", "guidance"},
    "set_layer_saturation": {"layer", "sat", "guidance"},
    "set_layer_brightness": {"layer", "factor", "guidance"},
    "scale_stroke_size": {"layer", "k", "guidance"},
    "swap_brush": {"layer", "preset", "guidance"},
    "densify": {"layer", "k", "guidance"},
    "erase": {"layer", "guidance"},
    "redraw": {"fraction", "jitter", "layer", "order", "guidance"},
    "recolor_palette": {"palette", "guidance"},
    "draw_path": {"layer", "d", "color", "size", "opacity", "preset", "guidance", "tag"},
    "tone_map": {"gain", "lift", "gamma", "layer", "guidance"},
    "split_shading": {"layer", "guidance"},
    "set_light": {"scale", "tint", "layer", "guidance"},
}
INTERNAL = {"seed", "selection", "weights", "mask", "mask_file", "all", "polygon", "instance", "mask_obj",
            "select_tag", "sel_box", "sel_hue", "sel_sat_min", "sel_val_min"}


def validate(spec):
    name = spec.get("op")
    if name not in OPS:
        raise ValueError("unknown op %r; known: %s" % (name, ", ".join(sorted(OPS))))
    for k in REQUIRED[name]:
        if k not in spec:
            raise ValueError("op %r missing %r" % (name, k))
    extra = set(spec) - {"op"} - ALLOWED[name] - INTERNAL
    if extra:
        raise ValueError("op %r got unknown key(s): %s" % (name, ", ".join(sorted(extra))))
    g = spec.get("guidance", 1.0)
    if not (0.0 <= float(g) <= 1.0):
        raise ValueError("guidance must be within [0,1], got %r" % (g,))
    return spec


def _copy_doc(doc):
    """结构化浅拷贝：strokes 里每个 dict 单独复制（算子只改标量字段，
    points 由 densify 显式重建），避免对 100MB 级计划做 deepcopy。"""
    out = dict(doc)
    out["layers"] = [dict(l) for l in (doc.get("layers") or [])]
    out["strokes"] = [dict(s) for s in doc["strokes"]]
    return out


def _canvas(doc):
    w, h = [int(v) for v in (doc.get("canvas") or [0, 0])]
    return w, h


def resolve_mask(doc, spec):
    """掩膜来源统一到 ops 语义里：文件 / 内联 raster / 多边形 / 实例（模型给的种子点）。

    spec 形如 {"mask": {...}} / {"mask_file": p} / {"all": true} / {"polygon": [[x,y],...]}
    或 {"instance": {"image": p, "seeds": [...], "negatives": [...], "box": [...], ...}}。
    instance 会先长掩膜再**客观验证**，验证不过就报错——不允许"选了但其实没选中"悄悄过去。
    """
    import json as _json
    from pathlib import Path as _Path
    import masks as M

    if isinstance(spec, (str, _Path)):
        return M.from_json(_json.loads(_Path(spec).read_text(encoding="utf-8")))
    if not isinstance(spec, dict):
        raise ValueError("selection spec 必须是 dict/路径，得到 %r" % type(spec))
    if "mask" in spec:
        m = spec["mask"]
        return M.from_json(_json.loads(_Path(m).read_text(encoding="utf-8"))) if isinstance(m, (str, _Path)) else m
    if "mask_file" in spec:
        return M.from_json(_json.loads(_Path(spec["mask_file"]).read_text(encoding="utf-8")))
    if spec.get("all"):
        return M.all_()
    if "polygon" in spec:
        return M.polygon([tuple(map(float, p)) for p in spec["polygon"]])
    if "instance" in spec:
        import numpy as _np
        import visual_mask as VM
        from PIL import Image as _Image
        inst = dict(spec["instance"])
        img = inst.get("image")
        if not img:
            raise ValueError("instance 需要 image：没有原图就无法长掩膜")
        arr = _np.asarray(_Image.open(img).convert("RGB"))
        seeds = [tuple(map(int, p)) for p in inst.get("seeds") or []]
        if not seeds:
            raise ValueError("instance 缺少 seeds")
        close = VM.seeds_mask(arr, seeds, inst.get("tolerance", 60), inst.get("hue_tol", 14.0),
                              [tuple(map(int, p)) for p in inst.get("negatives") or []],
                              inst.get("min_value", 0.0))
        bits, _ = VM.grow_mask(seeds, close, inst.get("grow", 6))
        box = inst.get("box")
        if box:
            x0, y0, x1, y1 = [int(v) for v in box]
            clip = _np.zeros(bits.shape, bool)
            clip[max(0, y0):y1, max(0, x0):x1] = True
            bits = bits & clip
        if inst.get("verify", True):
            import pick_instance as PI
            v = PI.verify(bits, inst.get("min_area", 0.002), inst.get("max_area", 0.35),
                          inst.get("max_components", 1), inst.get("border_tol", 0.10),
                          inst.get("allow_border", False), box)
            if not v["ok"]:
                raise ValueError("instance 验证不通过：%s（area=%.4f comps=%d fill=%.2f）"
                                 % ("; ".join(v["reasons"]), v["area_frac"], v["components"], v["fill"]))
        return M.from_bool(bits)
    raise ValueError("selection spec 里没有可用的掩膜来源（mask/mask_file/all/polygon/instance）")


_SEL_KEYS = ("mask", "mask_file", "all", "polygon", "instance")


def select_by_predicate(doc, spec):
    """按笔触自身属性选（框 + 色相/饱和/亮度阈值），完全绕开"给照片做分割"。

    注意尺度：hue 用 ops._hsv 的归一化值（0..1，绿≈0.33），不是角度；sat/val 也是 0..1。

    编辑的是画出来的笔触，不是照片：所以"把左下这片叶子调亮"可以直接写成
    {"sel_box": [x0,y0,x1,y1], "sel_hue": [h, tol], "sel_sat_min": s, "sel_val_min": v}。
    用 sel_ 前缀是为了不和算子自己的参数（如 set_layer_hue 的 hue）撞名。
    阈值由模型看图给，算子只做筛选，结果可复现。
    """
    import numpy as _np

    box = spec.get("sel_box")
    h0, ht = (spec.get("sel_hue") or (None, 0))[:2]
    sat_min = float(spec.get("sel_sat_min", 0.0))
    val_min = float(spec.get("sel_val_min", 0.0))
    out = []
    for i, st in enumerate(doc["strokes"]):
        pts = _np.asarray(st["points"], _np.float64)
        cx, cy = float(pts[:, 0].mean()), float(pts[:, 1].mean())
        if box and not (box[0] <= cx <= box[2] and box[1] <= cy <= box[3]):
            continue
        r, g, b = _to_rgb(st["color"]) if not isinstance(st["color"], (list, tuple)) else st["color"][:3]
        mx, mn = max(r, g, b), min(r, g, b)
        v = mx / 255.0
        s_ = 0.0 if mx <= 0 else (mx - mn) / mx
        if s_ < sat_min or v < val_min:
            continue
        if h0 is not None:
            hh = _hsv([r, g, b])[0]
            d = abs(hh - float(h0))
            d = min(d, 1.0 - d)
            if d > float(ht):
                continue
        out.append(i)
    return out


def select_by_tag(doc, tag):
    """按 tag 精确选笔触下标（draw_path 加的笔可以真正被删掉，不用回放整条链）。"""
    return [i for i, s in enumerate(doc["strokes"]) if str(s.get("tag")) == str(tag)]


def apply(doc, specs, selection=None, weights=None, seed=0):
    """按顺序执行算子列表；返回新 doc（入参不受影响）。"""
    out = _copy_doc(doc)
    for spec in specs:
        raw = dict(spec)
        name = raw.get("op")
        if name not in OPS:
            raise ValueError("unknown op %r; known: %s" % (name, ", ".join(sorted(OPS))))
        sel, wts = selection, weights
        if any(k in raw for k in ("sel_box", "sel_hue", "sel_sat_min", "sel_val_min")):
            sel = select_by_predicate(out, {k: raw[k] for k in ("sel_box", "sel_hue", "sel_sat_min", "sel_val_min") if k in raw})
            wts = np.ones(len(sel), np.float64)
        if "select_tag" in raw:
            sel = select_by_tag(out, raw["select_tag"])
            wts = np.ones(len(sel), np.float64)
        if any(k in raw for k in _SEL_KEYS):
            import masks as M
            m = resolve_mask(raw, {k: raw[k] for k in _SEL_KEYS if k in raw})
            sel = M.select(out, m)
            wts = M.weights(out, m)[1]
        clean = validate(dict(raw))
        clean.pop("op")
        if any(k in raw for k in _SEL_KEYS):
            clean["mask_obj"] = m
        for k in _SEL_KEYS:
            clean.pop(k, None)
        clean.pop("select_tag", None)
        for k in ("sel_box", "sel_hue", "sel_sat_min", "sel_val_min"):
            clean.pop(k, None)
        clean.setdefault("selection", sel)
        clean.setdefault("weights", wts)
        clean.setdefault("seed", seed)
        out = OPS[name](out, **clean)
    out["count"] = len(out["strokes"])
    return out


def main():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import plan_io
    import masks

    if len(sys.argv) < 2:
        print("usage: ops.py <list|apply|describe> ...")
        return
    cmd = sys.argv[1]
    if cmd == "list":
        print(json.dumps({"ops": sorted(OPS), "required": REQUIRED}, ensure_ascii=False))
    elif cmd == "apply":
        plan_path, ops_path, out_path = sys.argv[2], sys.argv[3], sys.argv[4]
        doc, w, h = plan_io.load_plan(plan_path)
        specs = json.loads(Path(ops_path).read_text(encoding="utf-8"))
        sel = wt = None
        if "--mask" in sys.argv:
            mask = json.loads(sys.argv[sys.argv.index("--mask") + 1])
            feather = float(sys.argv[sys.argv.index("--feather") + 1]) if "--feather" in sys.argv else 0.0
            sel, wt = masks.weights(doc, mask, feather)
        doc2 = apply(doc, specs, selection=sel, weights=wt)
        Path(out_path).write_text(json.dumps(doc2, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
        print(json.dumps({"in": len(doc["strokes"]), "out": len(doc2["strokes"]),
                          "selected": None if sel is None else len(sel),
                          "fingerprint": fingerprint(doc2)}, ensure_ascii=False))
    elif cmd == "describe":
        print(json.dumps({k: (v.__doc__ or "").strip().split("\n")[0] for k, v in OPS.items()}, ensure_ascii=False, indent=1))
    else:
        print("unknown command")


if __name__ == "__main__":
    main()
