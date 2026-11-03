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
              preset="round_soft", guidance=1.0, selection=None, weights=None, seed=0, **_):
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
        doc["strokes"].append({"layer": layer, "preset": preset, "size": float(size),
                               "opacity": op, "color": rgb, "points": pts})
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


OPS = {
    "set_layer_hue": set_layer_hue,
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
}


# 允许的键（编译器给的 JSON 里出现别的键一律拒绝——模型最容易在这里瞎编）
ALLOWED = {
    "set_layer_hue": {"layer", "hue", "guidance"},
    "set_layer_saturation": {"layer", "sat", "guidance"},
    "set_layer_brightness": {"layer", "factor", "guidance"},
    "scale_stroke_size": {"layer", "k", "guidance"},
    "swap_brush": {"layer", "preset", "guidance"},
    "densify": {"layer", "k", "guidance"},
    "erase": {"layer", "guidance"},
    "redraw": {"fraction", "jitter", "layer", "order", "guidance"},
    "recolor_palette": {"palette", "guidance"},
    "draw_path": {"layer", "d", "color", "size", "opacity", "preset", "guidance"},
}
INTERNAL = {"seed", "selection", "weights"}


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


def apply(doc, specs, selection=None, weights=None, seed=0):
    """按顺序执行算子列表；返回新 doc（入参不受影响）。"""
    out = _copy_doc(doc)
    for spec in specs:
        spec = validate(dict(spec))
        name = spec.pop("op")
        spec.setdefault("selection", selection)
        spec.setdefault("weights", weights)
        spec.setdefault("seed", seed)
        out = OPS[name](out, **spec)
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
