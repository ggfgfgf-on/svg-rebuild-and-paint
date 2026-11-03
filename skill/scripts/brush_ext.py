"""笔感与互操作扩展：smudge / 厚涂光照 / 动态材质 / 色彩动态 / 速度响应 / PSD·KRA 读写。

设计约束：
- 全部是纯 numpy 后处理或笔触预处理，CPU 结果与后端无关（同输入 → 同输出）；
- 随机量一律由 seed 决定，不使用全局随机状态；
- 每个能力都有明确的参数与默认值，便于写进 plan 或命令行。

用法概览：

    import brush_ext as bx

    rgba = bx.smudge_rgba(rgba, path_xyz, radius=18, amount=0.6, hardness=0.5)
    rgba = bx.impasto_rgba(rgba, light=(-0.44, 0.90, 0.35), height=1.4, spec=0.7)
    strokes = bx.apply_color_dynamics(strokes, hue=0.015, sat=0.12, val=0.10, seed=7)
    strokes = bx.apply_velocity_response(strokes, gain=0.35, boost=0.0)
    tip = bx.grain_variant(tip, grain, amount=0.5, scale=1.0, seed=3)
    layers = bx.read_kra("a.kra")      # [{name, rgba, opacity, blend}]
    layers = bx.read_psd("a.psd")      # 优先 psd-tools，退化到合成图
"""
import io
import json
import zipfile
from pathlib import Path

import numpy as np


# ---------------------------------------------------------------- 颜色空间
def rgb_to_hsv(rgb):
    """rgb: (..., 3) 0..1 → hsv。"""
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    mx = np.max(rgb[..., :3], axis=-1)
    mn = np.min(rgb[..., :3], axis=-1)
    d = mx - mn
    h = np.zeros_like(mx)
    m = d > 1e-9
    rm = m & (mx == r)
    gm = m & (mx == g) & ~rm
    bm = m & ~rm & ~gm
    h[rm] = ((g[rm] - b[rm]) / d[rm]) % 6.0
    h[gm] = ((b[gm] - r[gm]) / d[gm]) + 2.0
    h[bm] = ((r[bm] - g[bm]) / d[bm]) + 4.0
    h = h / 6.0
    s = np.where(mx > 1e-9, d / np.maximum(mx, 1e-9), 0.0)
    return np.stack([h, s, mx], axis=-1)


def hsv_to_rgb(hsv):
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    i = np.floor(h * 6.0)
    f = h * 6.0 - i
    p = v * (1.0 - s)
    q = v * (1.0 - f * s)
    t = v * (1.0 - (1.0 - f) * s)
    i = (i.astype(np.int64) % 6)
    out = np.zeros(hsv.shape[:-1] + (3,), np.float32)
    conds = [i == k for k in range(6)]
    vals = [(v, t, p), (q, v, p), (p, v, t), (p, q, v), (t, p, v), (v, p, q)]
    for c, (r, g, b) in zip(conds, vals):
        out[c] = np.stack([r, g, b], axis=-1)[c]
    return out


# ---------------------------------------------------------------- 确定性随机
def stroke_seed(base_seed, index):
    """把 (seed, 笔触序号) 混成一个稳定的 32 位种子。"""
    x = (int(base_seed) * 1664525 + int(index) * 1013904223 + 0x9E3779B9) & 0xFFFFFFFF
    x ^= x >> 16
    x = (x * 0x7FEB352D) & 0xFFFFFFFF
    x ^= x >> 15
    return x


def _unit(seed, salt=0):
    x = (seed * 22695477 + salt * 69069 + 1) & 0xFFFFFFFF
    x ^= x >> 13
    x = (x * 2654435761) & 0xFFFFFFFF
    return (x & 0xFFFFFF) / float(0x1000000)


# ---------------------------------------------------------------- smudge
def _bilinear(img, x, y):
    h, w = img.shape[:2]
    x = np.clip(x, 0, w - 1.001)
    y = np.clip(y, 0, h - 1.001)
    x0, y0 = np.floor(x).astype(np.int64), np.floor(y).astype(np.int64)
    fx, fy = (x - x0)[..., None], (y - y0)[..., None]
    a = img[y0, x0]
    b = img[y0, x0 + 1]
    c = img[y0 + 1, x0]
    d = img[y0 + 1, x0 + 1]
    return (a * (1 - fx) * (1 - fy) + b * fx * (1 - fy) + c * (1 - fx) * fy + d * fx * fy)


def smudge_rgba(rgba, path, radius=16.0, amount=0.6, hardness=0.5, step=None):
    """沿笔迹把底色往前推（拖色）。

    rgba   : (H, W, 4) float32 预乘或直通都可，函数只做颜色搬运；
    path   : (N, 3) [x, y, pressure]
    radius : 拖色半径（像素）
    amount : 0..1，越大越像把颜料拉走
    采样读的是调用前的原图（冻结快照），因此重复拖动不会自我放大；总颜料量大致有界但不严格守恒。
    hardness: 0..1，边缘硬度
    """
    out = np.array(rgba, np.float32, copy=True)
    src_img = out.copy()   # 采样永远读原图，避免重复拖动自我放大
    if len(path) < 2 or amount <= 0:
        return out
    pts = np.asarray(path, np.float32)
    step = float(step or max(1.0, radius * 0.25))
    h, w = out.shape[:2]
    for i in range(1, len(pts)):
        p0, p1 = pts[i - 1], pts[i]
        seg = p1[:2] - p0[:2]
        ln = float(np.linalg.norm(seg))
        if ln < 1e-3:
            continue
        dirv = seg / ln
        n = max(1, int(ln / step))
        for k in range(n + 1):
            t = k / n
            cx, cy = p0[0] + seg[0] * t, p0[1] + seg[1] * t
            r = int(np.ceil(radius * 1.3))
            x0, x1 = max(0, int(cx) - r), min(w, int(cx) + r + 1)
            y0, y1 = max(0, int(cy) - r), min(h, int(cy) + r + 1)
            if x1 <= x0 or y1 <= y0:
                continue
            yy, xx = np.mgrid[y0:y1, x0:x1]
            d = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2) / max(radius, 1e-3)
            fall = np.clip((1.0 - d) / max(1e-3, 1.0 - hardness), 0, 1)
            fall = fall * fall * (3 - 2 * fall) * float(amount)
            src = _bilinear(src_img, xx - dirv[0] * radius * 0.6, yy - dirv[1] * radius * 0.6)
            dst = out[y0:y1, x0:x1]
            f = fall[..., None]
            out[y0:y1, x0:x1] = dst * (1 - f) + src * f
    return out


# ---------------------------------------------------------------- 厚涂光照
def impasto_rgba(rgba, light=(-0.44, 0.90, 0.35), height=1.0, spec=0.6,
                 shininess=24.0, ambient=0.55, combine="overlay"):
    """用 alpha 当高度场做浮雕 + 高光。

    返回新数组；只改 RGB，alpha 保留。combine="overlay" 时把光照叠到原色上，
    combine="replace" 直接用光照结果。
    """
    a = np.asarray(rgba, np.float32)
    hgt = a[..., 3] * float(height)
    gy, gx = np.gradient(hgt)
    nx, ny, nz = -gx, -gy, np.ones_like(hgt)
    ln = np.sqrt(nx * nx + ny * ny + nz * nz) + 1e-9
    nx, ny, nz = nx / ln, ny / ln, nz / ln
    L = np.asarray(light, np.float32)
    L = L / (np.linalg.norm(L) + 1e-9)
    lam = np.clip(nx * L[0] + ny * L[1] + nz * L[2], 0, 1)
    Hh = L + np.array([0, 0, 1], np.float32)
    Hh = Hh / (np.linalg.norm(Hh) + 1e-9)
    sp = np.power(np.clip(nx * Hh[0] + ny * Hh[1] + nz * Hh[2], 0, 1), float(shininess)) * float(spec)
    shade = ambient + (1.0 - ambient) * lam
    rgb = a[..., :3]
    lit = rgb * shade[..., None] + sp[..., None]
    if combine == "overlay":
        out = np.clip(rgb * 0.35 + lit * 0.65, 0, 1)
    else:
        out = np.clip(lit, 0, 1)
    res = a.copy()
    res[..., :3] = out
    return res


# ---------------------------------------------------------------- 色彩动态
def apply_color_dynamics(strokes, hue=0.02, sat=0.15, val=0.12, seed=0, per_layer=None):
    """逐笔触色相/饱和度/明度抖动（确定性）。

    strokes: [{color:(r,g,b), ...}]，返回新的列表（不改原对象）。
    hue 单位是 0..1 的色环；sat/val 是相对幅度。
    """
    out = []
    for i, st in enumerate(strokes):
        s = stroke_seed(seed, i)
        dh = (_unit(s, 1) - 0.5) * 2 * hue
        ds = (_unit(s, 2) - 0.5) * 2 * sat
        dv = (_unit(s, 3) - 0.5) * 2 * val
        rgb = np.asarray(st["color"], np.float32) / 255.0
        hsv = rgb_to_hsv(rgb)
        hsv[0] = (hsv[0] + dh) % 1.0
        hsv[1] = np.clip(hsv[1] * (1.0 + ds), 0, 1)
        hsv[2] = np.clip(hsv[2] * (1.0 + dv), 0, 1)
        rgb2 = np.clip(hsv_to_rgb(hsv) * 255.0, 0, 255)
        st2 = dict(st)
        st2["color"] = tuple(int(round(v)) for v in rgb2)
        out.append(st2)
    return out


# ---------------------------------------------------------------- 动态材质
def grain_variant(tip, grain, amount=0.5, scale=1.0, seed=0):
    """按 seed 给同一张 grain 做平移/缩放，得到不同的纸纹相位（确定性）。"""
    t = np.asarray(tip, np.float32)
    g = np.asarray(grain, np.float32)
    if g.ndim != 2:
        raise ValueError("grain must be 2-D")
    s = stroke_seed(seed, 0)
    dy = int(_unit(s, 4) * g.shape[0])
    dx = int(_unit(s, 5) * g.shape[1])
    g2 = np.roll(np.roll(g, dy, axis=0), dx, axis=1)
    if abs(scale - 1.0) > 1e-6:
        gh, gw = g2.shape
        ys = np.clip((np.arange(gh) / max(1e-6, scale)) % gh, 0, gh - 1).astype(np.int64)
        xs = np.clip((np.arange(gw) / max(1e-6, scale)) % gw, 0, gw - 1).astype(np.int64)
        g2 = g2[ys][:, xs]
    th, tw = t.shape
    gy = np.linspace(0, g2.shape[0] - 1, th)
    gx = np.linspace(0, g2.shape[1] - 1, tw)
    yi = np.clip(gy.astype(np.int64), 0, g2.shape[0] - 2)
    xi = np.clip(gx.astype(np.int64), 0, g2.shape[1] - 2)
    fy = (gy - yi)[:, None]
    fx = (gx - xi)[None, :]
    gs = (g2[yi][:, xi] * (1 - fx) * (1 - fy) + g2[yi][:, xi + 1] * fx * (1 - fy) +
          g2[yi + 1][:, xi] * (1 - fx) * fy + g2[yi + 1][:, xi + 1] * fx * fy)
    return np.clip(t * ((1.0 - amount) + amount * gs), 0, 1).astype(np.float32)


# ---------------------------------------------------------------- 速度响应
def point_speeds(path):
    """相邻点距离（当速度用），首点补 0。"""
    p = np.asarray(path, np.float32)
    if len(p) < 2:
        return np.zeros(len(p), np.float32)
    d = np.linalg.norm(np.diff(p[:, :2], axis=0), axis=1)
    return np.concatenate([[0.0], d]).astype(np.float32)


def apply_velocity_response(strokes, gain=0.3, boost=0.0, ref=None):
    """按笔速缩放 size/opacity：越快越细越淡（gain 控制幅度）。"""
    out = []
    speeds = [point_speeds(st["points"]) for st in strokes]
    allv = np.concatenate([s for s in speeds if s.size]) if any(s.size for s in speeds) else np.zeros(1)
    scale_ref = float(ref if ref else max(1e-6, np.percentile(allv, 90)))
    for st, sp in zip(strokes, speeds):
        v = float(sp.max()) / scale_ref if sp.size else 0.0
        f = 1.0 - gain * min(1.0, v) + boost
        st2 = dict(st)
        st2["size"] = max(0.5, float(st["size"]) * f)
        st2["opacity"] = float(np.clip(float(st["opacity"]) * (0.7 + 0.3 * f), 0, 1))
        out.append(st2)
    return out


# ---------------------------------------------------------------- KRA / PSD
def read_kra(path):
    """读 Krita .kra：返回 [{name, rgba(uint8 HxWx4), opacity, blend}]，自底向上。"""
    path = Path(path)
    out = []
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        meta = {}
        if "maindoc.xml" in names:
            try:
                import xml.etree.ElementTree as ET
                root = ET.fromstring(z.read("maindoc.xml").decode("utf-8", "ignore"))
                for lay in root.iter():
                    if lay.tag.endswith("layer") and lay.get("name"):
                        meta[lay.get("filename", lay.get("name"))] = {
                            "opacity": float(lay.get("opacity", "255")) / 255.0,
                            "blend": lay.get("composite-op") or lay.get("compositeop") or "normal",
                        }
            except Exception:
                meta = {}
        layer_files = sorted([n for n in names if n.startswith("layers/") and n.endswith(".png")],
                             key=lambda s: int("".join(c for c in Path(s).stem if c.isdigit()) or 0))
        from PIL import Image
        for n in layer_files:
            img = Image.open(io.BytesIO(z.read(n))).convert("RGBA")
            info = meta.get(n, {})
            out.append({"name": Path(n).stem, "rgba": np.asarray(img, np.uint8),
                        "opacity": info.get("opacity", 1.0), "blend": info.get("blend", "normal")})
        if not out and "mergedimage.png" in names:
            img = Image.open(io.BytesIO(z.read("mergedimage.png"))).convert("RGBA")
            out.append({"name": "merged", "rgba": np.asarray(img, np.uint8), "opacity": 1.0, "blend": "normal"})
    return out




# ---------------------------------------------------------------- 区域失效
def smudge_rect(path, radius=16.0):
    """smudge 影响的矩形（含 halo），供增量重算/补片用。"""
    p = np.asarray(path, np.float32)
    pad = float(radius) * 1.6 + 2.0
    return (int(np.floor(p[:, 0].min() - pad)), int(np.floor(p[:, 1].min() - pad)),
            int(np.ceil(p[:, 0].max() + pad)), int(np.ceil(p[:, 1].max() + pad)))


def alpha_rect(rgba, thresh=1e-4):
    """非透明区域包围盒；空图返回 None。"""
    a = np.asarray(rgba)
    m = a[..., 3] > thresh if a.ndim == 3 and a.shape[2] == 4 else np.asarray(a) > thresh
    ys, xs = np.nonzero(m)
    if xs.size == 0:
        return None
    return (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max()))


def impasto_rect(rgba):
    """光照影响范围：非透明区域外扩 2 像素（梯度需要 halo）。"""
    r = alpha_rect(rgba)
    if r is None:
        return None
    h, w = rgba.shape[:2]
    return (max(0, r[0] - 2), max(0, r[1] - 2), min(w - 1, r[2] + 2), min(h - 1, r[3] + 2))


def apply_in_rect(rgba, rect, fn, pad=0):
    """只在 rect（外扩 pad）内应用 fn，返回 (新图, 实际矩形)。

    fn(crop, ox, oy) 必须是局部的：只读 crop，坐标偏移由 ox/oy 换算，边界信息靠 pad 提供。
    """
    h, w = rgba.shape[:2]
    x0, y0, x1, y1 = rect
    cx0, cy0 = max(0, x0 - pad), max(0, y0 - pad)
    cx1, cy1 = min(w - 1, x1 + pad), min(h - 1, y1 + pad)
    crop = np.array(rgba[cy0:cy1 + 1, cx0:cx1 + 1], np.float32, copy=True)
    res = fn(crop, cx0, cy0)
    out = np.array(rgba, np.float32, copy=True)
    # 只写回目标矩形本体，halo 仅用于提供边界信息
    ax0, ay0 = x0 - cx0, y0 - cy0
    ax1, ay1 = x1 - cx0, y1 - cy0
    out[y0:y1 + 1, x0:x1 + 1] = res[ay0:ay1 + 1, ax0:ax1 + 1]
    return out, (x0, y0, x1, y1)



def apply_effect_tiled(rgba, rects, fn, pad=2):
    """把像素局部的效果按矩形块分别应用（每块带 halo 裁剪），结果与整图应用逐位相同。

    这就是「效果参与区域失效」的落地方式：把缓存/脏区列表直接传进来，
    每块只重算自己的感受野（pad >= 效果的感受野半径）。
    """
    out = np.array(rgba, np.float32, copy=True)
    for r in rects:
        out, _ = apply_in_rect(out, r, fn, pad=pad)
    return out

# ---------------------------------------------------------------- 逐段色彩（逐 stamp 的近似）
def split_color_dynamics(strokes, segments=6, hue=0.03, sat=0.12, val=0.10, seed=0, min_points=2):
    """把每笔按点列切成若干子笔触，每段独立取色 —— 得到「逐 stamp 级」的色彩变化。

    不需要改 shader：等价于在笔触内部按段换色，代价是笔触数×segments。
    segments 会被限制在 max(1, len(points) - 1)。
    """
    out = []
    idx = 0
    for k, st in enumerate(strokes):
        pts = np.asarray(st.get("points", []), np.float32).reshape(-1, 3)
        n = len(pts)
        segs = max(1, min(int(segments), n - 1)) if n > 1 else 1
        if segs == 1:
            s = stroke_seed(seed, idx); idx += 1
            hsv = rgb_to_hsv(np.asarray(st["color"], np.float32) / 255.0)
            hsv[0] = (hsv[0] + (_unit(s, 1) - 0.5) * 2 * hue) % 1.0
            hsv[1] = np.clip(hsv[1] * (1 + (_unit(s, 2) - 0.5) * 2 * sat), 0, 1)
            hsv[2] = np.clip(hsv[2] * (1 + (_unit(s, 3) - 0.5) * 2 * val), 0, 1)
            st2 = dict(st); st2["points"] = pts
            st2["color"] = tuple(int(round(v)) for v in np.clip(hsv_to_rgb(hsv) * 255, 0, 255))
            out.append(st2)
            continue
        bounds = np.linspace(0, n - 1, segs + 1).astype(np.int64)
        for j in range(segs):
            a = int(bounds[j]); b = int(bounds[j + 1])
            if b <= a:
                continue
            s = stroke_seed(seed, idx); idx += 1
            hsv = rgb_to_hsv(np.asarray(st["color"], np.float32) / 255.0)
            hsv[0] = (hsv[0] + (_unit(s, 1) - 0.5) * 2 * hue) % 1.0
            hsv[1] = np.clip(hsv[1] * (1 + (_unit(s, 2) - 0.5) * 2 * sat), 0, 1)
            hsv[2] = np.clip(hsv[2] * (1 + (_unit(s, 3) - 0.5) * 2 * val), 0, 1)
            st2 = dict(st)
            st2["points"] = pts[a:b + 1]
            st2["color"] = tuple(int(round(v)) for v in np.clip(hsv_to_rgb(hsv) * 255, 0, 255))
            out.append(st2)
    return out


# ---------------------------------------------------------------- PSD（不依赖 psd-tools）
def _packbits(buf, need):
    import numpy as np  # noqa: F401
    out = bytearray()
    i, n = 0, len(buf)
    while i < n and len(out) < need:
        h = buf[i]; i += 1
        if h < 128:
            out += buf[i:i + h + 1]; i += h + 1
        elif h > 128:
            if i >= n:
                break
            out += bytes([buf[i]]) * (257 - h); i += 1
    return bytes(out[:need])



def _to8(raw, h, w, bps):
    """PSD 通道样本转 uint8：8bit 原样，16bit 取高字节（>>8，等价 /257 取整）。"""
    if bps == 1:
        return np.frombuffer(raw, np.uint8).reshape(h, w)
    arr = np.frombuffer(raw, ">u2").reshape(h, w)
    return (arr >> 8).astype(np.uint8)

def _psd_parse_layers(data):
    import struct
    pos = 0

    def take(n):
        nonlocal pos
        b = data[pos:pos + n]; pos += n
        return b

    def u16():
        return struct.unpack(">H", take(2))[0]

    def i16():
        return struct.unpack(">h", take(2))[0]

    def u32():
        return struct.unpack(">I", take(4))[0]

    sig = take(4)
    if sig != b"8BPS":
        raise ValueError("not a PSD")
    ver = u16()
    if ver != 1:
        raise ValueError("unsupported PSD version %d" % ver)
    take(6)
    channels, height, width, depth, mode = struct.unpack(">HIIHH", take(14))
    if depth not in (8, 16):
        raise ValueError("only 8/16-bit PSD supported (got %d)" % depth)
    bps = depth // 8
    take(u32())             # color mode data
    take(u32())             # image resources

    lm_len = u32()
    lm_end = pos + lm_len
    li_len = u32()
    li_end = pos + li_len
    count = abs(i16())
    records = []
    for _ in range(count):
        top, left, bottom, right = struct.unpack(">iiii", take(16))
        nch = u16()
        chans = []
        for _ in range(nch):
            cid = i16()
            clen = u32()
            chans.append([cid, clen])
        take(4)             # '8BIM'
        take(4)             # blend key
        opacity = data[pos]; pos += 1
        pos += 3            # clipping, flags, filler
        extra_len = u32()
        extra_end = pos + extra_len
        take(u32())         # mask data
        take(u32())         # blending ranges
        nlen = data[pos]; pos += 1
        name = take(nlen).decode("latin-1", "ignore")
        uname, sect = None, None
        while pos + 12 <= extra_end:
            take(4)                  # '8BIM' / '8B64'
            key = take(4).decode("latin-1", "ignore")
            ln = u32()
            blob = take(ln)
            pos += (4 - (ln % 4)) % 4
            if key == "luni" and len(blob) >= 4:
                cnt = struct.unpack(">I", blob[:4])[0]
                uname = blob[4:4 + cnt * 2].decode("utf-16-be", "ignore")
            elif key == "lsct" and len(blob) >= 4:
                sect = struct.unpack(">I", blob[:4])[0]
        pos = extra_end
        records.append({"rect": (left, top, right, bottom), "chans": chans,
                        "opacity": opacity, "name": uname or name,
                        "section": sect})

    out = []
    for rec in records:
        left, top, right, bottom = rec["rect"]
        w, h = right - left, bottom - top
        planes = {}
        for cid, _clen in rec["chans"]:
            comp = struct.unpack(">H", take(2))[0]
            if w <= 0 or h <= 0:
                continue
            if comp == 0:
                raw = take(w * h * bps)
                planes[cid] = _to8(raw, h, w, bps)
            elif comp == 1:
                counts = np.frombuffer(take(h * 2), ">u2")
                rows = []
                for c in counts:
                    rows.append(_packbits(take(int(c)), w))
                planes[cid] = _to8(b"".join(rows), h, w, bps)
            else:
                raise ValueError("unsupported PSD compression %d" % comp)
        rgba = np.zeros((h, w, 4), np.uint8)
        for cid in (0, 1, 2):
            if cid in planes:
                rgba[..., cid] = planes[cid]
        rgba[..., 3] = planes.get(-1, np.full((h, w), 255, np.uint8))
        out.append({"name": rec["name"], "rgba": rgba, "opacity": rec["opacity"] / 255.0,
                    "blend": "normal", "kind": "group" if rec["section"] in (1, 2) else "layer",
                    "section": rec["section"]})
    return out


def read_psd(path):
    """读 PSD：psd-tools → 内置 8BPS 解析器 → PIL 合成图。"""
    path = Path(path)
    try:
        from psd_tools import PSDImage
        psd = PSDImage.open(str(path))
        out = []
        for layer in psd:                     # 自底向上
            img = layer.composite()
            if img is None:
                continue
            out.append({"name": layer.name, "rgba": np.asarray(img.convert("RGBA"), np.uint8),
                        "opacity": float(getattr(layer, "opacity", 255)) / 255.0,
                        "blend": (getattr(layer, "blend_mode", "normal") or "normal").lower()
                        if isinstance(getattr(layer, "blend_mode", ""), str) else "normal"})
        if out:
            return out
    except Exception:
        pass
    try:
        return _psd_parse_layers(Path(path).read_bytes())
    except Exception:
        pass
    from PIL import Image
    img = Image.open(str(path)).convert("RGBA")
    return [{"name": "merged", "rgba": np.asarray(img, np.uint8), "opacity": 1.0, "blend": "normal"}]
