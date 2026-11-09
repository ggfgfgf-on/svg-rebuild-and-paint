"""ctypes wrapper for plan_svg_core.dll: binary plan <-> SVG."""
from __future__ import annotations
import ctypes
import json
import struct
from pathlib import Path

_DLL = Path(__file__).resolve().parent / "native" / "plan_svg_core.dll"


def available():
    return _DLL.exists()


def _load():
    lib = ctypes.CDLL(str(_DLL))
    for n in ("psvg_plan_to_svg", "psvg_svg_to_plan", "psvg_plan_file_to_svg", "psvg_svg_file_to_plan"):
        f = getattr(lib, n)
        f.restype = ctypes.c_void_p
        if n.endswith("_file_to_svg") or n.endswith("_file_to_plan"):
            f.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
        else:
            f.argtypes = [ctypes.c_char_p, ctypes.c_int]
    for n in ("psvg_bin_to_svg", "psvg_svg_to_bin"):
        f = getattr(lib, n)
        f.restype = ctypes.c_void_p
        f.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
    lib.psvg_free.argtypes = [ctypes.c_void_p]
    lib.psvg_free.restype = None
    return lib


_LIB = _load() if available() else None


def _free(ptr):
    _LIB.psvg_free(ptr)


def _err(ptr, n=256):
    s = ctypes.string_at(ptr, n)
    _free(ptr)
    if s.startswith(b"ERR:"):
        raise ValueError(s[4:].decode("utf-8", "replace"))
    return s


def _call_text(fn, text: str) -> str:
    if _LIB is None:
        raise RuntimeError("plan_svg_core.dll not found")
    b = text.encode("utf-8")
    ptr = fn(ctypes.c_char_p(b), len(b))
    if not ptr:
        raise RuntimeError("native bridge returned null")
    s = _err(ptr, 0 if False else 2 * 1024 * 1024)
    return s.decode("utf-8", "replace")


def pack_plan(doc) -> bytes:
    layers = doc.get("layers") or []
    order, blend = [], {}
    for l in layers:
        n = str(l.get("name", "layer"))
        if n not in blend:
            order.append(n)
            blend[n] = str(l.get("blend", "normal"))
    by = {}
    for s in doc.get("strokes") or []:
        n = str(s.get("layer", "layer"))
        if n not in blend:
            order.append(n); blend[n] = "normal"
        by.setdefault(n, []).append(s)
    out = bytearray(b"PSVB")
    out += struct.pack("<IIiii", 1, int(doc["canvas"][0]), int(doc["canvas"][1]), len(order), len(doc.get("strokes") or []))
    for n in order:
        b = blend[n].encode("utf-8"); out += struct.pack("<H", len(b)) + b
        nb = n.encode("utf-8"); out += struct.pack("<H", len(nb)) + nb
    idx = {n: i for i, n in enumerate(order)}
    for s in doc.get("strokes") or []:
        n = str(s.get("layer", "layer"))
        out += struct.pack("<H", idx[n])
        pb = str(s.get("preset", "round_soft")).encode("utf-8"); out += struct.pack("<H", len(pb)) + pb
        out += struct.pack("<fff", float(s.get("size", 8.0)), float(s.get("opacity", 1.0)), float(s.get("hardness", 0.7)))
        c = s.get("color") or [217, 180, 90]
        out += struct.pack("<fff", float(c[0]), float(c[1]), float(c[2]))
        pts = s.get("points") or []
        out += struct.pack("<H", len(pts))
        for q in pts:
            out += struct.pack("<fff", float(q[0]), float(q[1]), float(q[2] if len(q) > 2 else 1.0))
        t = str(s.get("tag", "")).encode("utf-8"); out += struct.pack("<H", len(t)) + t
    return bytes(out)


def unpack_plan(data: bytes):
    p = 4
    ver, w, h, lc, sc = struct.unpack_from("<IIiii", data, p); p += 20
    blends, names = [], []
    for _ in range(lc):
        n = struct.unpack_from("<H", data, p)[0]; p += 2
        blends.append(data[p:p+n].decode("utf-8")); p += n
        n = struct.unpack_from("<H", data, p)[0]; p += 2
        names.append(data[p:p+n].decode("utf-8")); p += n
    strokes = []
    for _ in range(sc):
        li = struct.unpack_from("<H", data, p)[0]; p += 2
        n = struct.unpack_from("<H", data, p)[0]; p += 2
        preset = data[p:p+n].decode("utf-8"); p += n
        size, op, hard = struct.unpack_from("<fff", data, p); p += 12
        r, g, b = struct.unpack_from("<fff", data, p); p += 12
        pc = struct.unpack_from("<H", data, p)[0]; p += 2
        pts = []
        for _ in range(pc):
            x, y, pr = struct.unpack_from("<fff", data, p); p += 12
            pts.append([x, y, pr])
        n = struct.unpack_from("<H", data, p)[0]; p += 2
        tag = data[p:p+n].decode("utf-8"); p += n
        st = {"layer": names[li], "preset": preset, "size": size, "opacity": op,
              "hardness": hard, "color": [r, g, b], "points": pts}
        if tag: st["tag"] = tag
        strokes.append(st)
    return {"canvas": [w, h], "layers": [{"name": n, "blend": b} for n, b in zip(names, blends)],
            "strokes": strokes, "count": len(strokes)}


def plan_to_svg(doc) -> str:
    data = pack_plan(doc)
    n = ctypes.c_int(0)
    ptr = _LIB.psvg_bin_to_svg(ctypes.c_char_p(data), len(data), ctypes.byref(n))
    if not ptr:
        raise RuntimeError("native bin_to_svg returned null")
    try:
        raw = ctypes.string_at(ptr, n.value if n.value else None)
    finally:
        _free(ptr)
    if raw.startswith(b"ERR:"):
        raise ValueError(raw[4:].decode("utf-8", "replace"))
    return raw.decode("utf-8")


def svg_to_plan(svg_text: str):
    b = svg_text.encode("utf-8")
    n = ctypes.c_int(0)
    ptr = _LIB.psvg_svg_to_bin(ctypes.c_char_p(b), len(b), ctypes.byref(n))
    if not ptr:
        raise RuntimeError("native svg_to_bin returned null")
    try:
        raw = ctypes.string_at(ptr, n.value if n.value else None)
    finally:
        _free(ptr)
    if raw.startswith(b"ERR:"):
        raise ValueError(raw[4:].decode("utf-8", "replace"))
    return unpack_plan(raw)


def _call_paths(fn, a: str, b: str) -> str:
    if _LIB is None:
        raise RuntimeError("plan_svg_core.dll not found")
    ptr = fn(a.encode("utf-8"), b.encode("utf-8"))
    if not ptr:
        raise RuntimeError("native bridge returned null")
    s = _err(ptr, 4096)
    return s.decode("utf-8", "replace")


def plan_file_to_svg(plan_path: str, svg_path: str) -> str:
    return _call_paths(_LIB.psvg_plan_file_to_svg, plan_path, svg_path)


def svg_file_to_plan(svg_path: str, plan_path: str) -> str:
    return _call_paths(_LIB.psvg_svg_file_to_plan, svg_path, plan_path)
