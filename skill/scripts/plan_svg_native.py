"""ctypes wrapper for plan_svg_core.dll (Windows)."""
from __future__ import annotations
import ctypes
import json
from pathlib import Path

_DLL = Path(__file__).resolve().parent / "native" / "plan_svg_core.dll"


def available():
    return _DLL.exists()


def _load():
    lib = ctypes.CDLL(str(_DLL))
    lib.psvg_plan_to_svg.argtypes = [ctypes.c_char_p, ctypes.c_int]
    lib.psvg_plan_to_svg.restype = ctypes.c_void_p
    lib.psvg_svg_to_plan.argtypes = [ctypes.c_char_p, ctypes.c_int]
    lib.psvg_svg_to_plan.restype = ctypes.c_void_p
    lib.psvg_free.argtypes = [ctypes.c_void_p]
    lib.psvg_free.restype = None
    return lib


_LIB = _load() if available() else None


def _call(fn, text: str) -> str:
    if _LIB is None:
        raise RuntimeError("plan_svg_core.dll not found")
    b = text.encode("utf-8")
    ptr = fn(ctypes.c_char_p(b), ctypes.c_int(len(b)))
    if not ptr:
        raise RuntimeError("native bridge returned null")
    try:
        out = ctypes.string_at(ptr).decode("utf-8")
    finally:
        _LIB.psvg_free(ptr)
    if out.startswith("ERR:"):
        raise ValueError(out[4:])
    return out


def plan_to_svg(doc) -> str:
    return _call(_LIB.psvg_plan_to_svg, json.dumps(doc, separators=(",", ":")))


def svg_to_plan(svg_text: str):
    return json.loads(_call(_LIB.psvg_svg_to_plan, svg_text))
