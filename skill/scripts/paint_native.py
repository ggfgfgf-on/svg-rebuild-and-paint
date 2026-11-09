"""paint_core.dll( v2) 的 ctypes 绑定：多笔头预设 / 多图层 / 压力校准表。

缺 DLL 时 load() 返回 None，调用方自行回退。
"""
from __future__ import annotations
import ctypes
import time, json, os
from pathlib import Path
import numpy as np

_NATIVE_DIR = Path(__file__).resolve().parent / "native"
PRESETS = {"round_soft": 0, "round_hard": 1, "flat": 2, "dry": 3, "airbrush": 4,
           "liner": 5, "chalk": 6, "sponge": 7, "bristle": 8, "marker": 9}
BLENDS = {"normal": 0, "multiply": 1, "screen": 2, "overlay": 3, "softlight": 4,
          "dodge": 5, "subtract": 6, "add": 7, "darken": 8, "lighten": 9,
          "colorburn": 10, "hardlight": 11, "vividlight": 12, "linearlight": 13,
          "pinlight": 14, "difference": 15, "exclusion": 16, "divide": 17,
          "linearburn": 18, "grainextract": 19, "grainmerge": 20,
          "hardmix": 21, "hue": 22, "saturation": 23, "color": 24, "luminosity": 25}
_lib = None
_tried = False


def load():
    global _lib, _tried
    if _lib is not None or _tried:
        return _lib
    _tried = True
    if os.environ.get("PAINT_NATIVE", "1").lower() in ("0", "false", "off"):
        return None
    path = Path(os.environ.get("PAINT_NATIVE_DLL") or (_NATIVE_DIR / "paint_core.dll"))
    if not path.exists():
        return None
    try:
        lib = ctypes.CDLL(str(path))
    except OSError:
        return None
    f32 = ctypes.POINTER(ctypes.c_float)
    u8 = ctypes.POINTER(ctypes.c_ubyte)
    i32 = ctypes.POINTER(ctypes.c_int)
    vp = ctypes.c_void_p
    lib.paint_create.restype = vp
    lib.paint_create.argtypes = [ctypes.c_int, ctypes.c_int]
    lib.paint_destroy.argtypes = [vp]
    lib.paint_layer_count.restype = ctypes.c_int
    lib.paint_layer_count.argtypes = [vp]
    lib.paint_add_layer.restype = ctypes.c_int
    lib.paint_add_layer.argtypes = [vp, ctypes.c_int]
    lib.paint_clear_layer.argtypes = [vp, ctypes.c_int] + [ctypes.c_float] * 4
    lib.paint_fill_gradient_polygon.restype = ctypes.c_int
    lib.paint_fill_gradient_polygon.argtypes = [vp, ctypes.c_int, f32, ctypes.c_int, f32, ctypes.c_int] + [ctypes.c_float] * 4 + [ctypes.c_float]
    lib.paint_fill_polygon.restype = ctypes.c_int
    lib.paint_fill_polygon.argtypes = [vp, ctypes.c_int, f32, ctypes.c_int] + [ctypes.c_float] * 4
    lib.paint_stroke_l.restype = ctypes.c_int
    lib.paint_stroke_l.argtypes = [vp, ctypes.c_int, f32, ctypes.c_int] + [ctypes.c_float] * 5 + [
        ctypes.c_int, ctypes.c_float, ctypes.c_float, i32, ctypes.c_int, f32, f32, ctypes.c_int]
    lib.paint_composite.restype = ctypes.c_int
    lib.paint_composite.argtypes = [vp, u8]
    lib.paint_layer_rgba.restype = ctypes.c_int
    lib.paint_layer_rgba.argtypes = [vp, ctypes.c_int, u8]
    lib.paint_tip_add.restype = ctypes.c_int
    lib.paint_tip_add.argtypes = [vp, f32, ctypes.c_int, ctypes.c_int]
    lib.paint_tip_count.restype = ctypes.c_int
    lib.paint_tip_count.argtypes = [vp]
    _lib = lib
    return _lib


def _pid(preset):
    """preset 允许是名字或位图笔尖 id（100+）。"""
    return preset if isinstance(preset, (int, np.integer)) else PRESETS.get(preset, 0)


TIPS_DIR = _NATIVE_DIR / "tips"


def load_tip_image(path):
    """读一张笔尖图（Krita .kpp / PNG）：优先取 alpha 通道，其次灰度。

    裁到非零外接框并等比缩放到最长边 128，返回 0..1 的 float 数组。
    """
    from PIL import Image
    im = Image.open(path)
    # Krita .kpp 常见两种：alpha 就是掩膜，或 alpha 全 255、掩膜在亮度里（黑=有墨）
    alpha = None
    if im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info):
        alpha = np.asarray(im.convert("LA"), dtype=np.float32)[:, :, 1] / 255.0
    lum = np.asarray(im.convert("L"), dtype=np.float32) / 255.0
    if alpha is not None and float(alpha.std()) > 0.02:
        arr = alpha
    else:
        arr = 1.0 - lum          # 黑=有墨
    ys, xs = np.nonzero(arr > 0.02)
    if len(xs) == 0:
        return None
    arr = arr[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    h, w = arr.shape
    s = 128.0 / max(h, w)
    if s < 1.0:
        from PIL import Image as I2
        arr = np.asarray(I2.fromarray((arr * 255).astype(np.uint8)).resize(
            (max(2, int(round(w * s))), max(2, int(round(h * s)))), I2.LANCZOS),
            dtype=np.float32) / 255.0
    return arr


def bake_grain(tip, grain, amount=0.5, scale=1.0):
    """把颗粒/纸纹通道烘焙进笔尖 alpha：tip *= (1-amount) + amount * grain(u·scale)。
    在贴图层完成，CPU 与 GPU 两条路径拿到的是同一张位图，天然逐位一致。"""
    tip = np.asarray(tip, np.float32)
    g = np.asarray(grain, np.float32)
    if g.ndim == 3:
        g = g.mean(axis=2)
    g = g - g.min()
    g = g / max(1e-6, float(g.max()))
    h, w = tip.shape[:2]
    gh, gw = g.shape
    ys = np.clip(((np.arange(h, dtype=np.float32) + 0.5) / h - 0.5) * float(scale) + 0.5, 0.0, 0.999999)
    xs = np.clip(((np.arange(w, dtype=np.float32) + 0.5) / w - 0.5) * float(scale) + 0.5, 0.0, 0.999999)
    samp = g[(ys * gh).astype(np.int32)[:, None], (xs * gw).astype(np.int32)[None, :]]
    mul = (1.0 - float(amount)) + float(amount) * samp
    if tip.ndim == 3:
        mul = mul[..., None]
    return np.clip(tip * mul, 0.0, 1.0).astype(np.float32)


_TIPS_CACHE = None


def _tips_cache(d, files):
    """笔尖解码结果缓存：按（文件名, 大小, mtime）签名校验，命中则跳过 PNG 解码。"""
    global _TIPS_CACHE
    import hashlib, json
    sig = [[f.name, f.stat().st_size, int(f.stat().st_mtime)] for f in files]
    h = hashlib.sha1(json.dumps(sig).encode()).hexdigest()[:16]
    npz = d.parent / ("tips_cache_%s.npz" % h)
    if npz.exists():
        try:
            with np.load(npz) as z:
                return {k: z[k] for k in z.files}
        except Exception:
            pass
    data = {}
    for f in files:
        arr = load_tip_image(f)
        if arr is not None:
            data[f.stem] = arr
    try:
        np.savez(npz, **data)
        for old in d.parent.glob("tips_cache_*.npz"):
            if old.name != npz.name:
                try:
                    old.unlink()
                except OSError:
                    pass
    except Exception:
        pass
    return data


def load_catalog(painter, tips_dir=None, limit=None):
    """把 native/tips/*.png 全部注册进 painter，返回 {名字: preset_id}。"""
    d = Path(tips_dir) if tips_dir else TIPS_DIR
    out = {}
    if not d.exists():
        return out
    files = sorted(d.glob("*.png"))[:limit] if limit else sorted(d.glob("*.png"))
    data = _tips_cache(d, files)
    for f in files:
        arr = data.get(f.stem)
        if arr is None:
            continue
        out[f.stem] = painter.add_tip(arr)
    return out


class Painter:
    def __init__(self, w, h):
        lib = load()
        if lib is None:
            raise RuntimeError("paint_core.dll not available")
        self._lib = lib
        self.w, self.h = int(w), int(h)
        self._ctx = lib.paint_create(self.w, self.h)
        if not self._ctx:
            raise RuntimeError("paint_create failed")
        self._region = None

    def __del__(self):
        self.close()

    def set_dynamics(self, scatter=0.0, size_jit=0.0, alpha_jit=0.0, angle_jit=0.0, taper=0.0):
        """散布 / 尺寸抖动 / 不透明度抖动 / 角度抖动 / 起收笔渐细；全 0 = 关闭。"""
        fn = getattr(self._lib, "paint_set_dynamics", None)
        if fn is None:
            raise RuntimeError("paint_core.dll 不支持动力学参数（需重新编译）")
        fn.argtypes = [ctypes.c_void_p] + [ctypes.c_float] * 5
        fn(self._ctx, float(scatter), float(size_jit), float(alpha_jit), float(angle_jit), float(taper))

    def clear_rect(self, idx, x0, y0, x1, y1):
        """只清掉一层的矩形区域（tile 级局部重绘用）。"""
        fn = getattr(self._lib, "paint_clear_rect", None)
        if fn is None:
            raise RuntimeError("paint_core.dll 不支持 clear_rect")
        fn.argtypes = [ctypes.c_void_p] + [ctypes.c_int] * 5
        fn(self._ctx, int(idx), int(x0), int(y0), int(x1), int(y1))

    def set_color_dynamics(self, amount=0.0):
        """逐 stamp 色彩微差（CPU 后端）：同一笔内每个 stamp 按序号确定性微调明度/冷暖。

        amount=0 时与旧行为逐位一致；>0 只改 RGB，覆盖率（alpha）完全不变。
        GPU 后端用同一套公式，但为让 InterlockedMax 保序，把抖动桶打包进 alpha 的低 8 位
        （见 GpuPainter.set_color_dynamics）；test_stamp_color 覆盖两端一致性。
        """
        fn = getattr(self._lib, "paint_set_color_dynamics", None)
        if fn is None:
            raise RuntimeError("paint_core.dll 不含 paint_set_color_dynamics，请重新编译原生库")
        fn.argtypes = [ctypes.c_void_p, ctypes.c_float]
        fn.restype = None
        fn(self._ctx, ctypes.c_float(float(amount)))

    def set_wet(self, amount=0.0):
        """湿混合：每一笔按 amount 从"本批调用开始时"的图层取色，再与笔色混合。
        amount=0 关闭（逐位等于旧行为）；取色来自只读快照，可并行、结果确定。"""
        fn = getattr(self._lib, "paint_set_wet", None)
        if fn is None:
            raise RuntimeError("paint_core.dll 不支持湿混合（需重新编译）")
        fn.argtypes = [ctypes.c_void_p, ctypes.c_float]
        fn(self._ctx, float(amount))

    def close(self):
        if getattr(self, "_ctx", None):
            self._lib.paint_destroy(self._ctx)
            self._ctx = None

    # ---- 图层 ----
    def add_layer(self, blend="normal"):
        return self._lib.paint_add_layer(self._ctx, BLENDS.get(blend, 0))

    def layer_count(self):
        return self._lib.paint_layer_count(self._ctx)

    def clear_layer(self, idx, rgb=(0, 0, 0), a=1.0):
        r, g, b = rgb
        self._lib.paint_clear_layer(self._ctx, int(idx), r / 255.0, g / 255.0, b / 255.0, float(a))

    def fill_polygon(self, layer, pts, rgb=(0, 0, 0), opacity=1.0):
        pts = np.ascontiguousarray(pts, dtype=np.float32)
        if pts.ndim != 2 or pts.shape[1] != 2:
            raise ValueError("fill points must be Nx2")
        r, g, b = rgb
        return self._lib.paint_fill_polygon(self._ctx, int(layer), pts.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
                                            len(pts), r / 255.0, g / 255.0, b / 255.0, float(opacity))

    def fill_gradient(self, layer, pts, paint, opacity=1.0):
        pts = np.ascontiguousarray(pts, dtype=np.float32)
        if pts.ndim != 2 or pts.shape[1] != 2:
            raise ValueError("fill points must be Nx2")
        stops = paint.get("stops") or []
        arr = np.ascontiguousarray([[float(st[0]), st[1][0] / 255.0, st[1][1] / 255.0, st[1][2] / 255.0,
                                     float(st[2]) if len(st) > 2 else 1.0] for st in stops], dtype=np.float32)
        if len(arr) < 2:
            raise ValueError("gradient needs two stops")
        xs, ys = pts[:, 0], pts[:, 1]
        minx, maxx, miny, maxy = float(xs.min()), float(xs.max()), float(ys.min()), float(ys.max())
        def coord(key, default):
            v = paint.get(key, default)
            if isinstance(v, str) and v.endswith("%"):
                q = float(v[:-1]) / 100.0
                return (minx + (maxx - minx) * q) if key in ("x1", "x2", "cx") else (miny + (maxy - miny) * q)
            return float(v)
        x1, y1 = coord("x1", 0.0), coord("y1", 0.0)
        x2, y2 = coord("x2", 1.0), coord("y2", 0.0)
        return self._lib.paint_fill_gradient_polygon(self._ctx, int(layer), pts.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
                                                     len(pts), arr.ctypes.data_as(ctypes.POINTER(ctypes.c_float)), len(arr),
                                                     x1, y1, x2, y2, float(opacity))

    # ---- 位图笔尖（增量能力）----
    def add_tip(self, tip):
        """注册一张位图笔尖（灰度/alpha，方形或任意 w×h，取值 0..1）。

        返回可直接当 preset 用的整数（100 + 序号）。公式预设 0..9 不受影响。
        """
        if isinstance(tip, (str, Path)):
            from PIL import Image
            im = Image.open(tip).convert("L")
            arr = np.asarray(im, dtype=np.float32) / 255.0
        else:
            arr = np.asarray(tip, dtype=np.float32)
            if arr.max() > 1.5:
                arr = arr / 255.0
        if arr.ndim != 2:
            raise ValueError("tip must be 2-D grayscale")
        arr = np.ascontiguousarray(arr, dtype=np.float32)
        idx = self._lib.paint_tip_add(self._ctx, arr.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
                                      arr.shape[1], arr.shape[0])
        if idx < 0:
            raise RuntimeError("paint_tip_add failed")
        return 100 + idx

    def tip_count(self):
        return self._lib.paint_tip_count(self._ctx)

    # ---- 区域 ----
    def set_region(self, region):
        self._region = None if region is None else np.ascontiguousarray(region, dtype=np.int32)

    # ---- 笔触 ----
    def stroke(self, layer, pts, size, opacity, rgb, preset="round_soft",
               hardness=0.55, spacing=1.0, region_id=0, lut=None):
        pts = np.ascontiguousarray(pts, dtype=np.float32)
        if pts.ndim == 1:
            pts = pts.reshape(1, -1)
        if pts.shape[1] != 3:
            raise ValueError("pts must be Nx3 (x, y, pressure)")
        r, g, b = rgb
        pid = PRESETS.get(preset, 0) if isinstance(preset, str) else int(preset)
        reg_p = None
        if self._region is not None:
            reg_p = self._region.ctypes.data_as(ctypes.POINTER(ctypes.c_int))
        lr = la = None
        n_lut = 0
        if lut:
            lr = np.ascontiguousarray(lut["r"], dtype=np.float32)
            la = np.ascontiguousarray(lut["a"], dtype=np.float32)
            n_lut = int(lr.size)
        self._lib.paint_stroke_l(
            self._ctx, int(layer), pts.ctypes.data_as(ctypes.POINTER(ctypes.c_float)), int(pts.shape[0]),
            float(size), float(opacity), r / 255.0, g / 255.0, b / 255.0,
            pid, float(hardness), float(spacing), reg_p, int(region_id),
            None if lr is None else lr.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
            None if la is None else la.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
            n_lut)

    def stroke_batch(self, layer, strokes, nthreads=8, region=None):
        """批量笔触：按行带多线程，每带按原顺序重放（与顺序渲染逐像素一致）。"""
        if not strokes:
            return 0
        pts_parts, offs, params, rids = [], [], [], []
        cur = 0
        for st in strokes:
            a = np.ascontiguousarray(st["points"], dtype=np.float32)
            if a.ndim == 1:
                a = a.reshape(-1, 3)
            pts_parts.append(a.ravel())
            offs.append(cur)
            cur += a.size
            col = st.get("color", (255, 255, 255))
            params.extend([float(st["size"]), float(st.get("opacity", 1.0)),
                           col[0] / 255.0, col[1] / 255.0, col[2] / 255.0,
                           float(st.get("hardness", 0.55)), float(st.get("spacing", 1.0)),
                           float(_pid(st.get("preset", "round_soft")))])
            rids.append(int(st.get("region_id", 0)))
        pts = np.ascontiguousarray(np.concatenate(pts_parts), dtype=np.float32)
        offs.append(cur)
        offs = np.ascontiguousarray(offs[:-1], dtype=np.int32)
        params = np.ascontiguousarray(params, dtype=np.float32)
        rids = np.ascontiguousarray(rids, dtype=np.int32)
        rowp = np.ascontiguousarray([0, int(pts.size)], dtype=np.int32)
        reg = region if region is not None else self._region
        if os.environ.get("PAINT_NOREGION"):
            reg = None
        reg_p = None if reg is None else np.ascontiguousarray(reg, dtype=np.int32).ctypes.data_as(ctypes.POINTER(ctypes.c_int))
        return self._lib.paint_strokes_batch(
            self._ctx, int(layer), pts.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
            offs.ctypes.data_as(ctypes.POINTER(ctypes.c_int)), len(strokes),
            params.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
            rids.ctypes.data_as(ctypes.POINTER(ctypes.c_int)), reg_p,
            rowp.ctypes.data_as(ctypes.POINTER(ctypes.c_int)),
            None, None, 0, int(nthreads))

    def stroke_batch_raw(self, layer, pts, offs, params, rids, nthreads=8, region=None):
        """零字典直传：pts (N,3) float32、offs (n,) int32 起点、params (n,8)、rids (n,)。"""
        pts = np.ascontiguousarray(pts, dtype=np.float32)
        offs = np.ascontiguousarray(offs, dtype=np.int32)
        params = np.ascontiguousarray(params, dtype=np.float32)
        rids = np.ascontiguousarray(rids, dtype=np.int32)
        rowp = np.ascontiguousarray([0, int(pts.size)], dtype=np.int32)
        reg = region if region is not None else self._region
        reg_p = None if reg is None else np.ascontiguousarray(reg, dtype=np.int32).ctypes.data_as(ctypes.POINTER(ctypes.c_int))
        _t0 = time.perf_counter()
        _res = self._lib.paint_strokes_batch(
            self._ctx, int(layer), pts.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
            offs.ctypes.data_as(ctypes.POINTER(ctypes.c_int)), int(offs.size),
            params.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
            rids.ctypes.data_as(ctypes.POINTER(ctypes.c_int)), reg_p,
            rowp.ctypes.data_as(ctypes.POINTER(ctypes.c_int)),
            None, None, 0, int(nthreads))
        if os.environ.get("PAINT_TIMING"):
            print("[stamp] layer %s strokes %d pts %d %.0f ms" % (layer, offs.size, pts.size // 3, (time.perf_counter() - _t0) * 1000.0))
        return _res

    # ---- 输出 ----
    def composite(self):
        buf = (ctypes.c_ubyte * (self.w * self.h * 4))()
        self._lib.paint_composite(self._ctx, buf)
        return np.frombuffer(buf, dtype=np.uint8).reshape(self.h, self.w, 4).copy()

    def layer_rgba(self, idx):
        buf = (ctypes.c_ubyte * (self.w * self.h * 4))()
        self._lib.paint_layer_rgba(self._ctx, int(idx), buf)
        return np.frombuffer(buf, dtype=np.uint8).reshape(self.h, self.w, 4).copy()

    # ---- 校准表 ----
    @staticmethod
    def load_brush(path=None):
        p = Path(path or (Path(__file__).resolve().parent / "native" / "brush.json"))
        if not p.exists():
            return {}
        return json.loads(p.read_text(encoding="utf-8"))


_HATCH_LIB = None


def paint_hatch(mask, angle, gap, minlen, maxlen, lum):
    """C++ 版 hatch：返回折线列表 [(N,3) float32]。"""
    import ctypes
    global _HATCH_LIB
    if _HATCH_LIB is None:
        path = Path(os.environ.get("PAINT_NATIVE_DLL") or (_NATIVE_DIR / "paint_core.dll"))
        _HATCH_LIB = ctypes.CDLL(str(path))
        _HATCH_LIB.paint_hatch.restype = ctypes.c_int
        _HATCH_LIB.paint_hatch.argtypes = [
            ctypes.POINTER(ctypes.c_ubyte), ctypes.c_int, ctypes.c_int,
            ctypes.c_float, ctypes.c_float, ctypes.c_float, ctypes.c_float,
            ctypes.POINTER(ctypes.c_float), ctypes.POINTER(ctypes.c_float),
            ctypes.POINTER(ctypes.c_int), ctypes.c_int, ctypes.c_int,
            ctypes.POINTER(ctypes.c_int)]
    m = np.ascontiguousarray(mask, dtype=np.uint8)
    lum32 = np.ascontiguousarray(lum, dtype=np.float32)
    h, w = m.shape
    n = int(m.sum())
    if n < 6:
        return []
    maxlines = n
    maxfloats = 3 * (2 * n + 8)
    pts = np.zeros(maxfloats, np.float32)
    offs = np.zeros(maxlines + 1, np.int32)
    total = ctypes.c_int(0)
    lines = _HATCH_LIB.paint_hatch(
        m.ctypes.data_as(ctypes.POINTER(ctypes.c_ubyte)), w, h,
        float(angle), float(gap), float(minlen), float(maxlen),
        lum32.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
        pts.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
        offs.ctypes.data_as(ctypes.POINTER(ctypes.c_int)),
        maxlines, maxfloats, ctypes.byref(total))
    out = []
    for i in range(int(lines)):
        s0 = int(offs[i])
        e0 = int(offs[i + 1]) if i + 1 < int(lines) else int(total.value)
        out.append(pts[s0:e0].reshape(-1, 3))
    return out


# ---- 区域规划内核（C++）----
class _RParams(ctypes.Structure):
    _fields_ = [
        ("band_count", ctypes.c_int),
        ("bed_min", ctypes.c_float), ("bed_max", ctypes.c_float),
        ("bed_thick_mul", ctypes.c_float), ("bed_area_div", ctypes.c_float),
        ("bed_opacity", ctypes.c_float), ("bed_gap_frac", ctypes.c_float),
        ("bed_minlen", ctypes.c_float), ("bed_maxlen", ctypes.c_float),
        ("bed_thick", ctypes.c_float),
        ("band_size_div", ctypes.c_float), ("band_clamp_lo", ctypes.c_float),
        ("band_clamp_hi", ctypes.c_float), ("band_size_scale", ctypes.c_float),
        ("band_thick_frac", ctypes.c_float), ("band_gap_frac", ctypes.c_float),
        ("band_stroke_scale", ctypes.c_float), ("band_minlen", ctypes.c_float),
        ("band_maxlen", ctypes.c_float), ("band_opacity", ctypes.c_float),
        ("angle", ctypes.c_float),
        ("thick", ctypes.c_float),
        ("band_min_px", ctypes.c_int),
        ("sh_bands", ctypes.c_int),
        ("sh_off_frac", ctypes.c_float), ("sh_gap_mul", ctypes.c_float),
        ("sh_stroke_scale", ctypes.c_float), ("sh_opacity", ctypes.c_float),
        ("sh_color_mul", ctypes.c_float), ("sh_minlen", ctypes.c_float),
        ("sh_maxlen", ctypes.c_float), ("lx", ctypes.c_float), ("ly", ctypes.c_float),
    ]


_PLAN_LIB = None


def plan_region_emit(xs, ys, lum, ref, params, gx=None, gy=None, gmag=None):
    """调用 C++ 区域内核，返回 [(layer, band, size, opacity, r,g,b, points)]。"""
    import ctypes
    global _PLAN_LIB
    if _PLAN_LIB is None:
        path = Path(os.environ.get("PAINT_NATIVE_DLL") or (_NATIVE_DIR / "paint_core.dll"))
        _PLAN_LIB = ctypes.CDLL(str(path))
        _PLAN_LIB.plan_region_emit.restype = ctypes.c_int
        _PLAN_LIB.plan_region_emit.argtypes = [
            ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int), ctypes.c_int,
            ctypes.POINTER(ctypes.c_float), ctypes.POINTER(ctypes.c_ubyte),
            ctypes.c_int, ctypes.c_int,
            ctypes.POINTER(ctypes.c_float), ctypes.POINTER(ctypes.c_float), ctypes.POINTER(ctypes.c_float),
            ctypes.POINTER(_RParams),
            ctypes.POINTER(ctypes.c_float), ctypes.c_int,
            ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_float), ctypes.c_int,
            ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int)]
    H, W = ref.shape[:2]
    xs = np.ascontiguousarray(xs, np.int32); ys = np.ascontiguousarray(ys, np.int32)
    lum32 = np.ascontiguousarray(lum, np.float32)
    if gx is None:
        gx = np.zeros_like(lum32); gy = np.zeros_like(lum32); gmag = np.zeros_like(lum32)
    gxa = np.ascontiguousarray(gx, np.float32); gya = np.ascontiguousarray(gy, np.float32)
    gma = np.ascontiguousarray(gmag, np.float32)
    ref8 = np.ascontiguousarray(ref, np.uint8)
    rp = _RParams(**params)
    maxs = max(128, int(xs.size) * 6 + 128)
    maxf = max(4096, int(xs.size) * 40 + 2048)
    vals = np.zeros(maxf, np.float32)
    meta = np.zeros(maxs * 8, np.int32)
    metaf = np.zeros(maxs * 5, np.float32)
    ns = ctypes.c_int(0); nf = ctypes.c_int(0)
    _PLAN_LIB.plan_region_emit(
        xs.ctypes.data_as(ctypes.POINTER(ctypes.c_int)),
        ys.ctypes.data_as(ctypes.POINTER(ctypes.c_int)), int(xs.size),
        lum32.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
        ref8.ctypes.data_as(ctypes.POINTER(ctypes.c_ubyte)),
        W, H,
        gxa.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
        gya.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
        gma.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
        ctypes.byref(rp),
        vals.ctypes.data_as(ctypes.POINTER(ctypes.c_float)), maxf,
        meta.ctypes.data_as(ctypes.POINTER(ctypes.c_int)),
        metaf.ctypes.data_as(ctypes.POINTER(ctypes.c_float)), maxs,
        ctypes.byref(ns), ctypes.byref(nf))
    out = []
    pos = 0
    for i in range(int(ns.value)):
        layer, band, npts = meta[i * 8], meta[i * 8 + 1], meta[i * 8 + 2]
        sz, op, r, g, b = metaf[i * 5:i * 5 + 5]
        pts = vals[pos:pos + npts * 3].reshape(-1, 3)
        pos += npts * 3
        out.append((int(layer), int(band), float(sz), float(op), float(r), float(g), float(b), pts))
    return out
