"""GPU compute 版 smudge / impasto（paint_fx.dll）。

与 brush_ext 的 CPU 版语义一致、全程 float32（无 8bit/16bit 中间量）。
"""
from __future__ import annotations
import ctypes
import os
from pathlib import Path
import numpy as np

_lib = None
_tried = False
_HERE = Path(__file__).resolve().parent / "native"


def load():
    global _lib, _tried
    if _lib is not None or _tried:
        return _lib
    _tried = True
    if os.environ.get("PAINT_FX", "1").lower() in ("0", "false", "off"):
        return None
    path = Path(os.environ.get("PAINT_FX_DLL") or (_HERE / "paint_fx.dll"))
    if not path.exists():
        return None
    try:
        lib = ctypes.CDLL(str(path))
    except OSError:
        return None
    vp = ctypes.c_void_p
    f32 = ctypes.POINTER(ctypes.c_float)
    lib.fx_create.restype = vp
    lib.fx_create.argtypes = [ctypes.c_int, ctypes.c_int]
    lib.fx_destroy.argtypes = [vp]
    lib.fx_upload.restype = ctypes.c_int
    lib.fx_upload.argtypes = [vp, f32]
    lib.fx_download.restype = ctypes.c_int
    lib.fx_download.argtypes = [vp, f32]
    lib.fx_smudge.restype = ctypes.c_int
    lib.fx_smudge.argtypes = [vp, f32, ctypes.c_int, ctypes.c_float, ctypes.c_float,
                              ctypes.c_float, ctypes.c_float]
    lib.fx_impasto.restype = ctypes.c_int
    lib.fx_impasto.argtypes = [vp, ctypes.c_float, ctypes.c_float, ctypes.c_float,
                               ctypes.c_float, ctypes.c_float, ctypes.c_float, ctypes.c_int]
    _lib = lib
    return _lib


class FxPainter:
    """一块 float32 RGBA 工作区：upload → smudge/impasto → download。"""

    def __init__(self, w, h):
        lib = load()
        if lib is None:
            raise RuntimeError("paint_fx.dll 不可用")
        self._lib = lib
        self.w, self.h = int(w), int(h)
        self._ctx = lib.fx_create(self.w, self.h)
        if not self._ctx:
            raise RuntimeError("fx_create failed")

    def _ptr(self, a):
        return a.ctypes.data_as(ctypes.POINTER(ctypes.c_float))

    def upload(self, rgba):
        a = np.ascontiguousarray(rgba, np.float32)
        assert a.shape == (self.h, self.w, 4), a.shape
        self._lib.fx_upload(self._ctx, self._ptr(a))
        return self

    def download(self):
        out = np.zeros((self.h, self.w, 4), np.float32)
        self._lib.fx_download(self._ctx, self._ptr(out))
        return out

    def smudge(self, path, radius=16.0, amount=0.6, hardness=0.5, step=0.0):
        p = np.ascontiguousarray(path, np.float32).reshape(-1, 3)
        self._lib.fx_smudge(self._ctx, self._ptr(p), p.shape[0], float(radius), float(amount),
                            float(hardness), float(step))
        return self

    def impasto(self, light=(-0.44, 0.90, 0.35), ambient=0.55, spec=0.6,
                shininess=24.0, combine="overlay"):
        lx, ly, lz = (float(v) for v in light)
        self._lib.fx_impasto(self._ctx, lx, ly, lz, float(ambient), float(spec),
                             float(shininess), 0 if combine == "overlay" else 1)
        return self

    def close(self):
        if getattr(self, "_ctx", None):
            self._lib.fx_destroy(self._ctx)
            self._ctx = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass


def available():
    try:
        return load() is not None
    except Exception:
        return False


_CACHE = {}


def _painter(w, h):
    """复用同一块设备/缓冲（每次重建 D3D 设备要 0.1s+，批量调用必须缓存）。"""
    key = (int(w), int(h))
    p = _CACHE.get(key)
    if p is None or getattr(p, "_ctx", None) is None:
        p = FxPainter(w, h)
        _CACHE[key] = p
    return p


def clear_cache():
    for p in list(_CACHE.values()):
        p.close()
    _CACHE.clear()


def smudge_rgba_gpu(rgba, path, radius=16.0, amount=0.6, hardness=0.5, step=0.0):
    a = np.ascontiguousarray(rgba, np.float32)
    h, w = a.shape[:2]
    p = _painter(w, h).upload(a)
    p.smudge(path, radius, amount, hardness, step)
    return p.download()


def impasto_rgba_gpu(rgba, light=(-0.44, 0.90, 0.35), height=1.0, spec=0.6,
                     shininess=24.0, ambient=0.55, combine="overlay"):
    a = np.ascontiguousarray(rgba, np.float32)
    if float(height) != 1.0:
        a = a.copy()
        a[..., 3] = np.clip(a[..., 3] * float(height), 0.0, 1.0)
    h, w = a.shape[:2]
    p = _painter(w, h).upload(a)
    p.impasto(light, ambient, spec, shininess, combine)
    return p.download()
