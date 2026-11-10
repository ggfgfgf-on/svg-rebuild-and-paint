"""ctypes wrapper for plan_svg_core.dll: binary plan <-> SVG."""
from __future__ import annotations
import ctypes
import json
import struct
from pathlib import Path

try:
    import plan_svg_ext as _ext
except Exception:
    _ext = None

_DLL = Path(__file__).resolve().parent / "native" / "plan_svg_core.dll"


def available():
    return _DLL.exists()


def _load():
    lib = ctypes.CDLL(str(_DLL))
    for n in ("psvg_plan_to_svg", "psvg_svg_to_plan", "psvg_svg_to_plan_base", "psvg_plan_file_to_svg", "psvg_svg_file_to_plan", "psvg_plan_file_to_bin", "psvg_bin_file_to_plan"):
        f = getattr(lib, n)
        f.restype = ctypes.c_void_p
        if n in ("psvg_plan_file_to_svg", "psvg_svg_file_to_plan", "psvg_plan_file_to_bin", "psvg_bin_file_to_plan"):
            f.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
        elif n == "psvg_svg_to_plan_base":
            f.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p]
        else:
            f.argtypes = [ctypes.c_char_p, ctypes.c_int]
    for n in ("psvg_bin_to_svg", "psvg_svg_to_bin", "psvg_plan_json_to_bin", "psvg_bin_to_plan_json", "psvg_bin_to_svg_stamps"):
        f = getattr(lib, n)
        f.restype = ctypes.c_void_p
        f.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
    lib.psvg_free.argtypes = [ctypes.c_void_p]
    lib.psvg_free.restype = None
    lib.psvg_clip_polygons.restype = ctypes.c_void_p
    lib.psvg_clip_polygons.argtypes = [ctypes.POINTER(ctypes.c_double), ctypes.c_int, ctypes.POINTER(ctypes.c_int),
                                        ctypes.POINTER(ctypes.c_double), ctypes.c_int, ctypes.POINTER(ctypes.c_int),
                                        ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
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


def _pack_plan_py(doc) -> bytes:
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


def _unpack_plan_py(data: bytes):
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


_TOP_KNOWN = {"canvas", "layers", "strokes", "count"}
_LAYER_KNOWN = {"name", "blend"}
_STROKE_KNOWN = {"layer", "preset", "size", "opacity", "hardness", "color", "points", "tag"}


def _extra_blob(doc):
    extra = {}
    top = {k: v for k, v in doc.items() if k not in _TOP_KNOWN}
    if top:
        extra["top"] = top
    ls = []
    for i, l in enumerate(doc.get("layers") or []):
        e = {k: v for k, v in l.items() if k not in _LAYER_KNOWN}
        if e:
            ls.append({"i": i, "extra": e})
    if ls:
        extra["layers"] = ls
    ss = []
    for i, s in enumerate(doc.get("strokes") or []):
        e = {k: v for k, v in s.items() if k not in _STROKE_KNOWN}
        if e.get("region_id", None) == 0:
            e.pop("region_id", None)
        if e:
            ss.append({"i": i, "extra": e})
    if ss:
        extra["strokes"] = ss
    return extra


def _merge_extra(doc, extra):
    if extra.get("top"):
        doc.update(extra["top"])
    for e in extra.get("layers") or []:
        i = e.get("i")
        if isinstance(i, int) and 0 <= i < len(doc.get("layers") or []):
            doc["layers"][i].update(e.get("extra") or {})
    for e in extra.get("strokes") or []:
        i = e.get("i")
        if isinstance(i, int) and 0 <= i < len(doc.get("strokes") or []):
            doc["strokes"][i].update(e.get("extra") or {})
    return doc


def _attach_extra(core: bytes, doc) -> bytes:
    extra = _extra_blob(doc)
    if not extra:
        return core
    payload = json.dumps(extra, separators=(",", ":")).encode("utf-8")
    b = bytearray(core)
    b[4:8] = struct.pack("<I", 2)
    b += payload
    b += struct.pack("<I", len(payload))
    return bytes(b)


def pack_plan(doc) -> bytes:
    if _ext is not None:
        return _attach_extra(_ext.pack_plan(doc), doc)
    if _LIB is not None:
        b = json.dumps(doc, separators=(",", ":")).encode("utf-8")
        n = ctypes.c_int(0)
        ptr = _LIB.psvg_plan_json_to_bin(ctypes.c_char_p(b), len(b), ctypes.byref(n))
        if ptr:
            try:
                raw = ctypes.string_at(ptr, n.value)
            finally:
                _free(ptr)
            if not raw.startswith(b"ERR:"):
                return raw
    return _attach_extra(_pack_plan_py(doc), doc)


def unpack_plan(data: bytes):
    extra = None
    core = data
    if len(data) >= 4 and data[4:8] == struct.pack("<I", 2):
        n = struct.unpack_from("<I", data, len(data) - 4)[0]
        core = data[:len(data) - 4 - n]
        extra = json.loads(data[len(data) - 4 - n:len(data) - 4].decode("utf-8"))
        core = bytearray(core); core[4:8] = struct.pack("<I", 1); core = bytes(core)
    if _ext is not None:
        doc = _ext.unpack_plan(core)
        return _merge_extra(doc, extra) if extra else doc
    if _LIB is not None:
        n = ctypes.c_int(0)
        ptr = _LIB.psvg_bin_to_plan_json(ctypes.c_char_p(data), len(data), ctypes.byref(n))
        if ptr:
            try:
                raw = ctypes.string_at(ptr, n.value)
            finally:
                _free(ptr)
            if not raw.startswith(b"ERR:"):
                return json.loads(raw.decode("utf-8"))
    doc = _unpack_plan_py(core)
    return _merge_extra(doc, extra) if extra else doc



def plan_to_svg(doc) -> str:
    if any(str(s.get("preset", "")) in ("image", "text") or s.get("holes") or s.get("fill_rule") or s.get("pattern") for s in doc.get("strokes", [])):
        import json as _json
        raw = _json.dumps(doc, ensure_ascii=False).encode("utf-8")
        ptr = _LIB.psvg_plan_to_svg(raw, len(raw))
        if not ptr:
            raise RuntimeError("native plan_to_svg returned null")
        try:
            text = ctypes.string_at(ptr).decode("utf-8")
        finally:
            _free(ptr)
        if text.startswith("ERR:"):
            raise ValueError(text[4:])
        return text
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


def clip_polygons(subject, clip, evenodd=False):
    import json as _json
    import numpy as _np
    def pack(polys):
        flat=[]; offs=[0]
        for p in polys:
            for q in p:
                flat.extend([float(q[0]), float(q[1])])
            offs.append(len(flat)//2)
        return _np.asarray(flat, _np.float64), _np.asarray(offs, _np.int32)
    sf, so = pack(subject); cf, co = pack(clip)
    n=ctypes.c_int(0)
    p=_LIB.psvg_clip_polygons(sf.ctypes.data_as(ctypes.POINTER(ctypes.c_double)), len(so)-1,
                              so.ctypes.data_as(ctypes.POINTER(ctypes.c_int)),
                              cf.ctypes.data_as(ctypes.POINTER(ctypes.c_double)), len(co)-1,
                              co.ctypes.data_as(ctypes.POINTER(ctypes.c_int)), 1 if evenodd else 0, ctypes.byref(n))
    if not p: return []
    try:
        raw=ctypes.string_at(p,n.value).decode("utf-8")
    finally:
        _free(p)
    if raw.startswith("ERR:"): raise ValueError(raw[4:])
    return _json.loads(raw)


def _needs_py(svg_text: str) -> bool:
    try:
        import plan_svg
        return plan_svg.needs_python_parser(svg_text)
    except Exception:
        return any(k in svg_text for k in ("data-center=", "data-stroke=", "transform=", "<style", "<use", "url(#"))


def _py_svg_to_plan(svg_text: str):
    import plan_svg
    return plan_svg._svg_to_plan_py(svg_text)


def svg_to_plan(svg_text: str, base_dir: str | None = None):
    if base_dir and hasattr(_LIB, "psvg_svg_to_plan_base"):
        b = svg_text.encode("utf-8")
        p = _LIB.psvg_svg_to_plan_base(ctypes.c_char_p(b), len(b), ctypes.c_char_p(str(base_dir).encode("utf-8")))
        if p:
            try:
                raw = ctypes.string_at(p)
            finally:
                _free(p)
            if raw.startswith(b"ERR:"):
                raise ValueError(raw[4:].decode("utf-8", "replace"))
            return json.loads(raw.decode("utf-8"))
    if "<text" in svg_text or "<pattern" in svg_text or "<filter" in svg_text:
        b = svg_text.encode("utf-8")
        p = _LIB.psvg_svg_to_plan(ctypes.c_char_p(b), len(b))
        if p:
            try:
                raw = ctypes.string_at(p)
            finally:
                _free(p)
            if raw.startswith(b"ERR:"):
                raise ValueError(raw[4:].decode("utf-8", "replace"))
            return json.loads(raw.decode("utf-8"))
    if _needs_py(svg_text):
        return _py_svg_to_plan(svg_text)
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
    try:
        s = ctypes.string_at(ptr)
    finally:
        _free(ptr)
    if s.startswith(b"ERR:"):
        raise ValueError(s[4:].decode("utf-8", "replace"))
    return s.decode("utf-8", "replace")


def plan_file_to_svg(plan_path: str, svg_path: str) -> str:
    return _call_paths(_LIB.psvg_plan_file_to_svg, plan_path, svg_path)


def svg_file_to_plan(svg_path: str, plan_path: str) -> str:
    try:
        text = Path(svg_path).read_text(encoding="utf-8")
    except Exception:
        text = ""
    if text and _needs_py(text):
        doc = _py_svg_to_plan(text)
        Path(plan_path).write_text(json.dumps(doc, separators=(",", ":")), encoding="utf-8")
        return plan_path
    return _call_paths(_LIB.psvg_svg_file_to_plan, svg_path, plan_path)


def pack_plan_file(plan_path: str, bin_path: str) -> str:
    return _call_paths(_LIB.psvg_plan_file_to_bin, plan_path, bin_path)


def unpack_plan_file(bin_path: str, plan_path: str) -> str:
    return _call_paths(_LIB.psvg_bin_file_to_plan, bin_path, plan_path)


def plan_to_svg_stamps(doc) -> str:
    data = pack_plan(doc)
    n = ctypes.c_int(0)
    ptr = _LIB.psvg_bin_to_svg_stamps(ctypes.c_char_p(data), len(data), ctypes.byref(n))
    if not ptr:
        raise RuntimeError("native stamps returned null")
    try:
        raw = ctypes.string_at(ptr, n.value)
    finally:
        _free(ptr)
    if raw.startswith(b"ERR:"):
        raise ValueError(raw[4:].decode("utf-8", "replace"))
    return raw.decode("utf-8")
