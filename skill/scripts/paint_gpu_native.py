"""paint_gpu.dll 的 Python 封装：API 与 paint_native.Painter 对齐，可互换使用。"""
import ctypes
import os
import time
import numpy as np
from gradient_xform import apply_linear as _grad_xform, radial_matrix as _radial_matrix

import paint_native

_DLL = None


def load():
    global _DLL
    if _DLL is not None:
        return _DLL
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "native", "paint_gpu.dll")
    lib = ctypes.CDLL(path)
    lib.pg_create.restype = ctypes.c_void_p
    lib.pg_create.argtypes = [ctypes.c_int, ctypes.c_int]
    lib.pg_add_layer.restype = ctypes.c_int
    lib.pg_add_layer.argtypes = [ctypes.c_void_p, ctypes.c_int]
    lib.pg_layer_count.restype = ctypes.c_int
    lib.pg_layer_count.argtypes = [ctypes.c_void_p]
    lib.pg_clear_layer.argtypes = [ctypes.c_void_p, ctypes.c_int] + [ctypes.c_float] * 4
    lib.pg_set_region.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_int), ctypes.c_int]
    lib.pg_add_tip.restype = ctypes.c_int
    lib.pg_add_tip.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_float), ctypes.c_int, ctypes.c_int]
    lib.pg_stroke_batch.restype = ctypes.c_int
    lib.pg_stroke_batch.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_float),
                                    ctypes.POINTER(ctypes.c_int), ctypes.c_int,
                                    ctypes.POINTER(ctypes.c_float), ctypes.POINTER(ctypes.c_int)]
    lib.pg_comp_begin.argtypes = [ctypes.c_void_p, ctypes.c_int]
    lib.pg_composite_layer.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
    lib.pg_read_acc.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_float)]
    lib.pg_read_layer.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_float)]
    lib.pg_fill_polygon.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_float), ctypes.c_int] + [ctypes.c_float] * 4
    lib.pg_fill_gradient_polygon.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_float), ctypes.c_int, ctypes.POINTER(ctypes.c_float), ctypes.c_int] + [ctypes.c_float] * 5
    lib.pg_fill_gradient_polygon_spread.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_float), ctypes.c_int, ctypes.POINTER(ctypes.c_float), ctypes.c_int] + [ctypes.c_float] * 5 + [ctypes.c_int]
    lib.pg_fill_radial_polygon_spread.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_float), ctypes.c_int, ctypes.POINTER(ctypes.c_float), ctypes.c_int] + [ctypes.c_float] * 6 + [ctypes.c_int]
    lib.pg_fill_radial_xform.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_float), ctypes.c_int, ctypes.POINTER(ctypes.c_float), ctypes.c_int] + [ctypes.c_float] * 11 + [ctypes.c_float, ctypes.c_int]
    lib.pg_set_stroke_gradient_spread.argtypes = [ctypes.POINTER(ctypes.c_float), ctypes.c_int] + [ctypes.c_float] * 4 + [ctypes.c_int]
    lib.pg_set_stroke_gradient_radial.argtypes = [ctypes.POINTER(ctypes.c_float), ctypes.c_int] + [ctypes.c_float] * 5 + [ctypes.c_int]
    lib.pg_set_stroke_gradient_radial_xform.argtypes = [ctypes.POINTER(ctypes.c_float), ctypes.c_int] + [ctypes.c_float] * 11 + [ctypes.c_int]
    lib.pg_set_stroke_gradient.argtypes = [ctypes.POINTER(ctypes.c_float), ctypes.c_int] + [ctypes.c_float] * 4
    lib.pg_apply_layer_mask.restype = ctypes.c_int
    lib.pg_apply_layer_mask.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_ubyte), ctypes.c_int, ctypes.c_int]
    lib.pg_apply_layer_mask_rect.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_ubyte), ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
    lib.pg_comp_checkpoint.argtypes = [ctypes.c_void_p]
    lib.pg_comp_restore.argtypes = [ctypes.c_void_p]
    lib.pg_set_layer_rgba.restype = ctypes.c_int
    lib.pg_set_layer_rgba.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_ubyte)]
    lib.pg_read_rgba8.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ubyte)]
    lib.pg_destroy.argtypes = [ctypes.c_void_p]
    lib.pg_prof.restype = ctypes.c_char_p
    _DLL = lib
    return lib


def _stop_rgb(c):
    v = [float(x) for x in c[:3]]
    return [x / 255.0 for x in v] if max(v) > 1.0 else v

class GpuPainter:
    def __init__(self, w, h):
        self._lib = load()
        self.w, self.h = int(w), int(h)
        self._ctx = self._lib.pg_create(self.w, self.h)
        if not self._ctx:
            raise RuntimeError("pg_create failed")
        self._blends = []
        self._region = None
        self._region_sig = None
        self._acc = np.zeros((self.h, self.w, 4), np.float32)
        self._out = np.zeros((self.h, self.w, 4), np.uint8)
        self._cur_layer = 0
        self._acc_layer = -1
        self._base_ready = False

    def set_dynamics(self, scatter=0.0, size_jit=0.0, alpha_jit=0.0, angle_jit=0.0, taper=0.0):
        self._lib.pg_set_dynamics.argtypes = [ctypes.c_void_p] + [ctypes.c_float] * 5
        self._lib.pg_set_dynamics(self._ctx, float(scatter), float(size_jit), float(alpha_jit),
                                  float(angle_jit), float(taper))

    def clear_rect(self, idx, x0, y0, x1, y1):
        self._lib.pg_clear_rect.argtypes = [ctypes.c_void_p] + [ctypes.c_int] * 5
        self._lib.pg_clear_rect(self._ctx, int(idx), int(x0), int(y0), int(x1), int(y1))

    def set_color_dynamics(self, amount=0.0):
        """逐 stamp 色彩微差（GPU）：把抖动桶打包进 alpha 的低 8 位，靠 InterlockedMax 保序归并。"""
        fn = getattr(self._lib, "pg_set_color_dynamics", None)
        if fn is None:
            if amount and float(amount) > 0.0:
                raise RuntimeError("paint_gpu.dll 不含 pg_set_color_dynamics，请重新编译原生库")
            return None
        fn.argtypes = [ctypes.c_void_p, ctypes.c_float]
        fn.restype = None
        fn(self._ctx, ctypes.c_float(float(amount)))

    def set_wet(self, amount=0.0):
        self._lib.pg_set_wet.argtypes = [ctypes.c_void_p, ctypes.c_float]
        self._lib.pg_set_wet(self._ctx, float(amount))

    def close(self):
        if getattr(self, "_ctx", None):
            self._lib.pg_destroy(self._ctx)
            self._ctx = None

    def __del__(self):
        self.close()

    def add_layer(self, blend="normal"):
        bid = paint_native.BLENDS.get(blend, 0) if isinstance(blend, str) else int(blend)
        idx = self._lib.pg_add_layer(self._ctx, bid)
        self._blends.append(bid)
        return idx

    def layer_count(self):
        return self._lib.pg_layer_count(self._ctx)

    def clear_layer(self, idx, rgb=(0, 0, 0), a=1.0):
        r, g, b = rgb
        self._lib.pg_clear_layer(self._ctx, int(idx), r / 255.0, g / 255.0, b / 255.0, float(a))

    def set_region(self, region):
        self._region = None if region is None else np.ascontiguousarray(region, np.int32)
        self._region_sig = None

    def _apply_region(self, region):
        sig = None if region is None else (region.__array_interface__["data"][0], region.size)
        if sig != self._region_sig:
            if region is None:
                self._lib.pg_set_region(self._ctx, None, 0)
            else:
                arr = np.ascontiguousarray(region, np.int32)
                self._lib.pg_set_region(self._ctx, arr.ctypes.data_as(ctypes.POINTER(ctypes.c_int)), 1)
                self._region = arr
            self._region_sig = sig
        return self._region

    def add_tip(self, tip):
        arr = np.ascontiguousarray(tip, np.float32)
        if arr.ndim != 2:
            raise ValueError("tip must be 2-D")
        idx = self._lib.pg_add_tip(self._ctx, arr.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
                                   arr.shape[1], arr.shape[0])
        if idx < 0:
            raise RuntimeError("pg_add_tip failed")
        return 100 + idx

    def tip_count(self):
        return len(getattr(self, "_tips", []))

    def stroke(self, layer, pts, size, opacity, rgb, preset="round_soft",
               hardness=0.55, spacing=1.0, region_id=0, region=None, lut=None):
        """单笔（与 Painter.stroke 同签名）：转成一批调用，保证两条后端走同一条路径。"""
        pts = np.ascontiguousarray(np.asarray(pts, np.float32).reshape(-1, 3))
        r, g_, b = rgb
        params = np.array([[float(size), float(opacity),
                            r / 255.0, g_ / 255.0, b / 255.0,
                            float(hardness), float(spacing), float(paint_native._pid(preset))]], np.float32)
        return self.stroke_batch_raw(layer, pts.ravel(), np.array([0], np.int32), params,
                                     np.array([int(region_id)], np.int32), nthreads=1, region=region)

    def stroke_gradient(self, layer, pts, size, opacity, paint, preset="round_soft", hardness=0.55):
        pts = np.ascontiguousarray(np.asarray(pts, np.float32).reshape(-1, 3))
        stops = paint.get("stops") or []
        arr = np.ascontiguousarray([[float(st[0])] + _stop_rgb(st[1]) + [float(st[2]) if len(st) > 2 else 1.0] for st in stops], np.float32)
        if len(arr) < 2:
            raise ValueError("gradient needs two stops")
        xs, ys = pts[:, 0], pts[:, 1]
        minx, maxx, miny, maxy = float(xs.min()), float(xs.max()), float(ys.min()), float(ys.max())
        units = paint.get("units", "objectBoundingBox")
        def coord(k, d):
            v = paint.get(k, d)
            pct = isinstance(v, str) and v.endswith("%")
            q = float(v[:-1]) / 100.0 if pct else float(v)
            if units == "objectBoundingBox" or pct:
                return (minx + (maxx-minx)*q) if k in ("x1", "x2", "cx", "fx") else (miny + (maxy-miny)*q)
            return q
        x1, y1, x2, y2 = coord("x1", 0.0), coord("y1", 0.0), coord("x2", 1.0), coord("y2", 0.0)
        tr = _grad_xform(paint, (minx, miny, maxx, maxy)) if paint.get("type", "linear") == "linear" else None
        if tr:
            x1, y1, x2, y2 = tr
        tr = _grad_xform(paint, (minx, miny, maxx, maxy)) if paint.get("type", "linear") == "linear" else None
        if tr:
            x1, y1, x2, y2 = tr
        sp = paint_native._spread(paint)
        if paint.get("type") == "radial":
            mat = _radial_matrix(paint, (minx, miny, maxx, maxy))
            if mat:
                cx, cy, rr, fx, fy = paint_native._radial_raw(paint)
                self._lib.pg_set_stroke_gradient_radial_xform(arr.ctypes.data_as(ctypes.POINTER(ctypes.c_float)), len(arr),
                                                              cx, cy, rr, fx, fy, *mat, sp)
            else:
                cx, cy = coord("cx", 0.5), coord("cy", 0.5)
                rv = paint.get("r", 0.5)
                rq = (float(rv[:-1]) / 100.0) if isinstance(rv, str) and rv.endswith("%") else float(rv)
                rr = (maxx - minx) * rq if units == "objectBoundingBox" else rq
                fx, fy = coord("fx", "50%"), coord("fy", "50%")
                self._lib.pg_set_stroke_gradient_radial(arr.ctypes.data_as(ctypes.POINTER(ctypes.c_float)), len(arr),
                                                        cx, cy, max(1e-6, rr), fx, fy, sp)
        elif sp:
            self._lib.pg_set_stroke_gradient_spread(arr.ctypes.data_as(ctypes.POINTER(ctypes.c_float)), len(arr), x1, y1, x2, y2, sp)
        else:
            self._lib.pg_set_stroke_gradient(arr.ctypes.data_as(ctypes.POINTER(ctypes.c_float)), len(arr), x1, y1, x2, y2)
        try:
            params = np.array([[float(size), float(opacity), 1.0, 1.0, 1.0, float(hardness), 1.0,
                                float(paint_native._pid(preset))]], np.float32)
            return self.stroke_batch_raw(layer, pts.ravel(), np.array([0], np.int32), params,
                                         np.array([0], np.int32), nthreads=1)
        finally:
            self._lib.pg_set_stroke_gradient(None, 0, 0, 0, 0, 0)

    def stroke_batch(self, layer, strokes, nthreads=8, region=None):
        """批量笔触（与 Painter.stroke_batch 同签名、同参数字段）。"""
        if not strokes:
            return 0
        pts_parts, offs, params, rids = [], [], [], []
        cur = 0
        for st in strokes:
            a = np.ascontiguousarray(st["points"], np.float32)
            if a.ndim == 1:
                a = a.reshape(-1, 3)
            pts_parts.append(a.ravel())
            offs.append(cur)
            cur += a.size
            col = st.get("color", (255, 255, 255))
            params.extend([float(st["size"]), float(st.get("opacity", 1.0)),
                           col[0] / 255.0, col[1] / 255.0, col[2] / 255.0,
                           float(st.get("hardness", 0.55)), float(st.get("spacing", 1.0)),
                           float(paint_native._pid(st.get("preset", "round_soft")))])
            rids.append(int(st.get("region_id", 0)))
        pts = np.ascontiguousarray(np.concatenate(pts_parts), np.float32)
        offs.append(cur)
        offs = np.ascontiguousarray(offs[:-1], np.int32)
        params = np.ascontiguousarray(params, np.float32)
        rids = np.ascontiguousarray(rids, np.int32)
        self.stroke_batch_raw(layer, pts, offs, params, rids, nthreads=nthreads, region=region)
        return len(strokes)

    def fill_polygon(self, idx, pts, rgb, opacity=1.0):
        pts = np.ascontiguousarray(np.asarray(pts, np.float32).reshape(-1, 2))
        r, g, b = rgb
        return self._lib.pg_fill_polygon(self._ctx, int(idx), pts.ctypes.data_as(ctypes.POINTER(ctypes.c_float)), len(pts),
                                         r / 255.0, g / 255.0, b / 255.0, float(opacity))

    def fill_gradient(self, idx, pts, paint, opacity=1.0):
        pts = np.ascontiguousarray(np.asarray(pts, np.float32).reshape(-1, 2))
        stops = paint.get("stops") or []
        arr = np.ascontiguousarray([[float(st[0])] + _stop_rgb(st[1]) + [float(st[2]) if len(st) > 2 else 1.0] for st in stops], np.float32)
        if len(arr) < 2:
            raise ValueError("gradient needs two stops")
        xs, ys = pts[:, 0], pts[:, 1]
        minx, maxx, miny, maxy = float(xs.min()), float(xs.max()), float(ys.min()), float(ys.max())
        units = paint.get("units", "objectBoundingBox")
        def coord(k, d):
            v = paint.get(k, d)
            pct = isinstance(v, str) and v.endswith("%")
            q = float(v[:-1]) / 100.0 if pct else float(v)
            if units == "objectBoundingBox" or pct:
                return (minx + (maxx - minx) * q) if k in ("x1", "x2", "cx", "fx") else (miny + (maxy - miny) * q)
            return q
        x1, y1, x2, y2 = coord("x1", 0.0), coord("y1", 0.0), coord("x2", 1.0), coord("y2", 0.0)
        tr = _grad_xform(paint, (minx, miny, maxx, maxy)) if paint.get("type", "linear") == "linear" else None
        if tr:
            x1, y1, x2, y2 = tr
        tr = _grad_xform(paint, (minx, miny, maxx, maxy)) if paint.get("type", "linear") == "linear" else None
        if tr:
            x1, y1, x2, y2 = tr
        sp = paint_native._spread(paint)
        if paint.get("type") == "radial":
            mat = _radial_matrix(paint, (minx, miny, maxx, maxy))
            if mat:
                cx, cy, rr, fx, fy = paint_native._radial_raw(paint)
                return self._lib.pg_fill_radial_xform(self._ctx, int(idx), pts.ctypes.data_as(ctypes.POINTER(ctypes.c_float)), len(pts),
                                                       arr.ctypes.data_as(ctypes.POINTER(ctypes.c_float)), len(arr),
                                                       cx, cy, rr, fx, fy, *mat, float(opacity), sp)
            cx, cy = coord("cx", 0.5), coord("cy", 0.5)
            rv = paint.get("r", 0.5)
            rq = (float(rv[:-1]) / 100.0) if isinstance(rv, str) and rv.endswith("%") else float(rv)
            rr = (maxx - minx) * rq if paint.get("units", "objectBoundingBox") == "objectBoundingBox" else rq
            rr = max(1e-6, rr)
            fx, fy = coord("fx", "50%"), coord("fy", "50%")
            return self._lib.pg_fill_radial_polygon_spread(self._ctx, int(idx), pts.ctypes.data_as(ctypes.POINTER(ctypes.c_float)), len(pts),
                                                           arr.ctypes.data_as(ctypes.POINTER(ctypes.c_float)), len(arr),
                                                           cx, cy, rr, fx, fy, float(opacity), sp)
        if sp:
            return self._lib.pg_fill_gradient_polygon_spread(self._ctx, int(idx), pts.ctypes.data_as(ctypes.POINTER(ctypes.c_float)), len(pts),
                                                             arr.ctypes.data_as(ctypes.POINTER(ctypes.c_float)), len(arr), x1, y1, x2, y2, float(opacity), sp)
        if paint.get("type") == "radial":
            cx, cy = coord("cx", 0.5), coord("cy", 0.5)
            rv = paint.get("r", 0.5)
            rq = (float(rv[:-1]) / 100.0) if isinstance(rv, str) and rv.endswith("%") else float(rv)
            rr = (maxx - minx) * rq if paint.get("units", "objectBoundingBox") == "objectBoundingBox" else rq
            rr = max(1e-6, rr)
            fx, fy = coord("fx", "50%"), coord("fy", "50%")
            return self._lib.pg_fill_radial_polygon_spread(self._ctx, int(idx), pts.ctypes.data_as(ctypes.POINTER(ctypes.c_float)), len(pts),
                                                           arr.ctypes.data_as(ctypes.POINTER(ctypes.c_float)), len(arr),
                                                           cx, cy, rr, fx, fy, float(opacity), 0)
        return self._lib.pg_fill_gradient_polygon(self._ctx, int(idx), pts.ctypes.data_as(ctypes.POINTER(ctypes.c_float)), len(pts),
                                                  arr.ctypes.data_as(ctypes.POINTER(ctypes.c_float)), len(arr), x1, y1, x2, y2, float(opacity))

    def apply_mask(self, idx, alpha):
        alpha = np.ascontiguousarray(alpha, dtype=np.uint8)
        if alpha.ndim != 2:
            raise ValueError("mask must be HxW")
        return self._lib.pg_apply_layer_mask(self._ctx, int(idx), alpha.ctypes.data_as(ctypes.POINTER(ctypes.c_ubyte)),
                                              alpha.shape[1], alpha.shape[0])

    def apply_mask_rect(self, idx, alpha, x0, y0, x1, y1):
        alpha = np.ascontiguousarray(alpha, dtype=np.uint8)
        if alpha.ndim != 2:
            raise ValueError("mask must be HxW")
        return self._lib.pg_apply_layer_mask_rect(self._ctx, int(idx), alpha.ctypes.data_as(ctypes.POINTER(ctypes.c_ubyte)),
                                                  alpha.shape[1], alpha.shape[0], int(x0), int(y0), int(x1), int(y1))

    def set_layer_rgba(self, idx, rgba):
        rgba = np.ascontiguousarray(rgba, dtype=np.uint8)
        if rgba.ndim != 3 or rgba.shape[2] != 4:
            raise ValueError("rgba must be HxWx4")
        return self._lib.pg_set_layer_rgba(self._ctx, int(idx), rgba.ctypes.data_as(ctypes.POINTER(ctypes.c_ubyte)))

    def layer_rgba(self, idx):
        """读回单层 RGBA8（与 Painter.layer_rgba 对齐：float 读回后四舍五入到 0..255）。"""
        out = np.empty((self.h, self.w, 4), np.float32)
        self._lib.pg_read_layer(self._ctx, int(idx), out.ctypes.data_as(ctypes.POINTER(ctypes.c_float)))
        return (np.clip(out, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8)

    load_brush = staticmethod(paint_native.Painter.load_brush)

    def stroke_batch_raw(self, layer, pts, offs, params, rids, nthreads=8, region=None):
        if int(layer) != self._cur_layer:
            self._cur_layer = int(layer)
        reg = region if region is not None else self._region
        self._apply_region(reg)
        pts = np.ascontiguousarray(pts, np.float32)
        offs = np.ascontiguousarray(offs, np.int32)
        params = np.ascontiguousarray(params, np.float32)
        rids = np.ascontiguousarray(rids, np.int32)
        offs2 = np.empty(offs.size + 1, np.int32)
        offs2[:-1] = offs
        offs2[-1] = pts.size
        return self._lib.pg_stroke_batch(
            self._ctx, int(layer), pts.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
            offs2.ctypes.data_as(ctypes.POINTER(ctypes.c_int)), int(offs.size),
            params.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
            rids.ctypes.data_as(ctypes.POINTER(ctypes.c_int)))

    def composite(self):
        """全量合成 + GPU 侧 RGBA8 打包回读（安全路径）。"""
        lib = self._lib
        lib.pg_comp_begin(self._ctx, 0)
        for i, bid in enumerate(self._blends):
            lib.pg_composite_layer(self._ctx, i, bid)
        lib.pg_read_rgba8(self._ctx, self._out.ctypes.data_as(ctypes.POINTER(ctypes.c_ubyte)))
        return self._out

    def composite_incremental(self):
        """增量合成（实验）：基线缓存 + 只重算当前层。"""
        lib = self._lib
        k = self._cur_layer
        if k > len(self._blends) - 1:
            k = len(self._blends) - 1
        if k != self._acc_layer or not self._base_ready:
            if self._acc_layer < 0 or k < self._acc_layer:
                lib.pg_comp_begin(self._ctx, 0)
                for j in range(k):
                    lib.pg_composite_layer(self._ctx, j, self._blends[j])
            else:
                lib.pg_comp_restore(self._ctx)
                lib.pg_composite_layer(self._ctx, self._acc_layer, self._blends[self._acc_layer])
            lib.pg_comp_checkpoint(self._ctx)
            self._acc_layer = k
            self._base_ready = True
        lib.pg_comp_restore(self._ctx)
        lib.pg_composite_layer(self._ctx, k, self._blends[k])
        lib.pg_read_rgba8(self._ctx, self._out.ctypes.data_as(ctypes.POINTER(ctypes.c_ubyte)))
        return self._out

    def prof(self):
        return self._lib.pg_prof().decode()
