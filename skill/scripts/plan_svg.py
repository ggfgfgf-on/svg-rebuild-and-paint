"""plan.json <-> SVG bridge.

Each plan stroke becomes one <path> element with data-* metadata, so the SVG
stays editable at stroke/layer level and can be parsed back losslessly for the
fields the painting engine understands.

Fidelity notes:
- hard/round/flat strokes are represented by a stroked path; pressure is kept
  in data-p for round-trip, not applied per point in SVG.
- softness becomes a quantized SVG filter; textures are not reproduced.
- arbitrary SVG paths are sampled; curves are approximated.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import zlib
import math
import re
import sys
import time
from pathlib import Path
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape, quoteattr

sys.path.insert(0, str(Path(__file__).resolve().parent))
import plan_io
import color_convert

try:
    import plan_svg_native as _native
except Exception:
    _native = None


def _esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _f(v):
    return ("%.4f" % float(v)).rstrip("0").rstrip(".")


def _rgb(color):
    if isinstance(color, str) and color.startswith("#"):
        h = color.lstrip("#")
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        if len(h) == 6:
            return "#" + h.lower()
    c = [max(0, min(255, int(round(float(x))))) for x in (color or (217, 180, 90))[:3]]
    return "#%02x%02x%02x" % tuple(c)


def _rgb_text(color):
    c = [max(0, min(255, float(x))) for x in (color or (217, 180, 90))[:3]]
    return ",".join(_f(x) for x in c)


def _path_d(points):
    pts = [[float(p[0]), float(p[1])] for p in points]
    if not pts:
        return "M 0 0"
    out = ["M %s %s" % (_f(pts[0][0]), _f(pts[0][1]))]
    if len(pts) == 1:
        out.append("L %s %s" % (_f(pts[0][0] + 0.01), _f(pts[0][1])))
    else:
        for x, y in pts[1:]:
            out.append("L %s %s" % (_f(x), _f(y)))
    return " ".join(out)


def _fc(v):
    return ("%.2f" % float(v)).rstrip("0").rstrip(".")


def _path_d_compact(points, holes=None):
    def one(pts):
        clean = []
        for p in pts:
            q = (float(p[0]), float(p[1]))
            if not clean or abs(q[0]-clean[-1][0]) > 1e-9 or abs(q[1]-clean[-1][1]) > 1e-9:
                clean.append(q)
        if not clean:
            return ""
        d = "M %s %s" % (_fc(clean[0][0]), _fc(clean[0][1]))
        for x, y in clean[1:]:
            d += " l %s %s" % (_fc(x - clean[0][0]), _fc(y - clean[0][1]))
            clean[0] = (x, y)
        return d + " Z"
    out = one(points)
    for h in holes or []:
        if len(h) >= 3:
            out += " " + one(h)
    return out


def _path_d_holes(points, holes):
    d = _path_d(points) + " Z"
    for h in holes or []:
        if len(h) >= 3:
            d += " " + _path_d(h) + " Z"
    return d


def _p_text(points):
    return ",".join(_f(p[2] if len(p) > 2 else 1.0) for p in points)


def _qhard(h):
    h = max(0.0, min(1.0, float(h)))
    return round(h * 20.0) / 20.0


def _filter_id(h):
    q = _qhard(h)
    if q >= 0.95:
        return None
    return "soft_%d" % int(round(q * 100))


def _filter_defs(ids):
    out = []
    for q in sorted(ids):
        blur = max(0.35, (1.0 - q) * 5.0)
        out.append('<filter id="%s" x="-30%%" y="-30%%" width="160%%" height="160%%">'
                   '<feGaussianBlur stdDeviation="%s"/></filter>' % ("soft_%d" % int(round(q * 100)), _f(blur)))
    return "".join(out)



def _paint_def(paint, gid):
    kind = paint.get("type", "linear")
    units = paint.get("units", "objectBoundingBox")
    stops = []
    for st in paint.get("stops") or []:
        if len(st) < 2:
            continue
        off, col = st[0], st[1]
        op = st[2] if len(st) > 2 else 1.0
        stops.append('<stop offset="%s" stop-color="%s" stop-opacity="%s"/>' %
                     (_f(max(0.0, min(1.0, float(off)))), _rgb(col), _f(float(op))))
    if kind == "radial":
        attrs = 'cx="%s" cy="%s" r="%s"' % (paint.get("cx", "50%"), paint.get("cy", "50%"), paint.get("r", "50%"))
        if "fx" in paint or "fy" in paint:
            attrs += ' fx="%s" fy="%s"' % (paint.get("fx", "50%"), paint.get("fy", "50%"))
        tag = "radialGradient"
    else:
        attrs = 'x1="%s" y1="%s" x2="%s" y2="%s"' % (paint.get("x1", "0%"), paint.get("y1", "0%"),
                                                       paint.get("x2", "100%"), paint.get("y2", "0%"))
        tag = "linearGradient"
    src = ((paint.get("source") or {}).get("gradient") or {}).get("attrs") or {}
    extra = ""
    for k in ("gradientTransform", "spreadMethod", "fx", "fy"):
        v = src.get(k, paint.get(k))
        if v is not None and k not in attrs:
            extra += ' %s="%s"' % (k, v)
    if kind == "radial" and "fx" not in attrs and "fx" in src:
        extra += ' fx="%s" fy="%s"' % (src.get("fx", "50%"), src.get("fy", "50%"))
    return '<%s id="%s" gradientUnits="%s" %s%s>%s</%s>' % (tag, gid, units, attrs, extra, "".join(stops), tag)

def _mask_png_b64(m):
    import numpy as np
    if m.get("encoding") == "png-base64":
        return m.get("data", "")
    if m.get("encoding") == "zlib-base64":
        from PIL import Image
        w, h = int(m.get("width", 0)), int(m.get("height", 0))
        raw = zlib.decompress(base64.b64decode(m["data"]))
        im = Image.frombytes("L", (w, h), raw)
        buf = io.BytesIO(); im.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode("ascii")
    return ""

def _mask_def(m, gid, w, h):
    units = m.get("mask_units", "userSpaceOnUse")
    content = m.get("content_units", "userSpaceOnUse")
    if m.get("svg"):
        at = m.get("attrs") or {}
        x = at.get("x", "0"); y = at.get("y", "0")
        mw = at.get("width", str(w)); mh = at.get("height", str(h))
        return ('<mask id="%s" maskUnits="%s" maskContentUnits="%s" x="%s" y="%s" width="%s" height="%s">%s</mask>'
                % (gid, units, content, x, y, mw, mh, m.get("svg")))
    mx, my = int(m.get("x", 0)), int(m.get("y", 0))
    mw, mh = int(m.get("width", w)), int(m.get("height", h))
    return ('<mask id="%s" maskUnits="%s" x="0" y="0" width="%d" height="%d">'
            '<image href="data:image/png;base64,%s" x="%d" y="%d" width="%d" height="%d"/></mask>'
            % (gid, units, w, h, _mask_png_b64(m), mx, my, mw, mh))


def plan_to_svg(doc, *, filters=False, title=None):
    """Return an SVG string. One <path> per stroke, one <g> per layer."""
    if (_native is not None and _native.available() and not filters
            and not any(isinstance(x.get("paint"), dict) or isinstance(x.get("mask"), dict) or x.get("runs") or x.get("runs") for x in doc.get("strokes", []))
            and not any(isinstance(x.get("mask"), dict) for x in doc.get("layers", []))):
        return _native.plan_to_svg(doc)
    w, h = [int(v) for v in doc["canvas"]]
    layers = doc.get("layers") or []
    order = []
    blends = {}
    for layer in layers:
        name = str(layer.get("name", "layer"))
        if name not in order:
            order.append(name)
            blends[name] = str(layer.get("blend", "normal"))
    for s in doc.get("strokes", []):
        name = str(s.get("layer", "layer"))
        if name not in order:
            order.append(name)
            blends[name] = "normal"
    by_layer = {name: [] for name in order}
    for s in doc.get("strokes", []):
        by_layer[str(s.get("layer", "layer"))].append(s)

    fids = set()
    if filters:
        for s in doc.get("strokes", []):
            fid = _filter_id(s.get("hardness", 0.7))
            if fid:
                fids.add(_qhard(s.get("hardness", 0.7)))

    paint_defs = []
    paint_gid = {}
    pattern_defs = []
    pattern_gid = {}
    for st in doc.get("strokes") or []:
        ptn = st.get("pattern")
        if isinstance(ptn, dict) and ptn.get("data"):
            gid = "pat%d" % len(pattern_defs)
            pattern_gid[id(st)] = gid
            pattern_defs.append('<pattern id="%s" width="%s" height="%s" patternUnits="userSpaceOnUse"><image href="data:image/png;base64,%s" width="%s" height="%s"/></pattern>' % (
                gid, _f(ptn.get("width", 32)), _f(ptn.get("height", 32)), ptn.get("data"), _f(ptn.get("width", 32)), _f(ptn.get("height", 32))))
    mask_defs = []
    mask_gid = {}
    for i, layer in enumerate(layers):
        m = layer.get("mask")
        if isinstance(m, dict) and m.get("encoding") in ("png-base64", "zlib-base64"):
            gid = "mask%d" % i
            mask_gid[i] = gid
            mask_defs.append(_mask_def(m, gid, w, h))
    stroke_mask_gid = {}
    for st in doc.get("strokes", []):
        m = st.get("mask")
        if isinstance(m, dict) and m.get("encoding") in ("png-base64", "zlib-base64"):
            gid = "smask%d" % len(stroke_mask_gid)
            stroke_mask_gid[id(st)] = gid
            mask_defs.append(_mask_def(m, gid, w, h))
    for st in doc.get("strokes", []):
        if isinstance(st.get("paint"), dict):
            gid = "paint%d" % len(paint_defs)
            paint_gid[id(st)] = gid
            paint_defs.append(_paint_def(st["paint"], gid))
    head = ['<?xml version="1.0" encoding="UTF-8"?>',
            '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d" '
            'shape-rendering="geometricPrecision">' % (w, h, w, h)]
    if title:
        head.append("<title>%s</title>" % escape(str(title)))
    if fids or paint_defs or pattern_defs or mask_defs:
        head.append("<defs>%s</defs>" % "".join(paint_defs + pattern_defs + mask_defs + ([_filter_defs(fids)] if fids else [])))
    out = ["".join(head)]
    for li, name in enumerate(order):
        blend = blends.get(name, "normal")
        style = "isolation:isolate;mix-blend-mode:%s" % blend if blend != "normal" else "isolation:isolate"
        mref = (' mask="url(#%s)"' % mask_gid[li]) if li in mask_gid else ""
        out.append('<g id=%s data-name=%s data-blend=%s style=%s%s>'
                   % (quoteattr("L%d" % li), quoteattr(name), quoteattr(blend), quoteattr(style), mref))
        for s in by_layer.get(name, []):
            if str(s.get("preset", "")) == "text":
                fam = s.get("font_family", "Arial"); fs = s.get("font_size", 16)
                col = _rgb(s.get("color")); op = max(0.0, min(1.0, float(s.get("opacity", 1.0))))
                tr = s.get("transform")
                trs = (' transform="matrix(%s)"' % " ".join(_f(float(v)) for v in tr)) if isinstance(tr, (list, tuple)) and len(tr) == 6 else ""
                runs = s.get("runs")
                if isinstance(runs, list) and runs:
                    inner = "".join('<tspan fill="%s" font-size="%s">%s</tspan>' % (
                        _rgb(r.get("color")), _f(r.get("font_size", fs)), escape(str(r.get("text", "")))) for r in runs)
                else:
                    inner = escape(str(s.get("text", "")))
                out.append('<text x="%s" y="%s" font-family="%s" font-size="%s" fill="%s" fill-opacity="%s" text-anchor="%s" data-layer="%s" data-preset="text"%s>%s</text>' % (
                    _f(s.get("x", 0)), _f(s.get("y", 0)), _esc(fam), _f(fs), col, _f(op),
                    _esc(s.get("text_anchor", "start")), _esc(name), trs, inner))
                continue
            if str(s.get("preset", "")) == "image" and isinstance(s.get("image"), dict):
                im = s["image"]
                tr = s.get("transform")
                trs = (' transform="matrix(%s)"' % " ".join(_f(float(v)) for v in tr)) if isinstance(tr, (list, tuple)) and len(tr) == 6 else ""
                out.append('<image href="data:%s;base64,%s" x="%s" y="%s" width="%s" height="%s" opacity="%s"%s/>' % (
                    _esc(im.get("mime", "image/png")), im.get("data", ""), _f(s.get("x", 0)), _f(s.get("y", 0)),
                    _f(s.get("width", 1)), _f(s.get("height", 1)), _f(s.get("opacity", 1.0)), trs))
                continue
            pts = s.get("points") or []
            if not pts:
                continue
            size = float(s.get("size", 8.0))
            op = max(0.0, min(1.0, float(s.get("opacity", 1.0))))
            hard = max(0.0, min(1.0, float(s.get("hardness", 0.7))))
            preset = str(s.get("preset", "round_soft"))
            gid = paint_gid.get(id(s))
            paint_ref = ("url(#%s)" % pattern_gid[id(s)]) if id(s) in pattern_gid else (("url(#%s)" % gid) if gid else _rgb(s.get("color")))
            if preset == "image" and isinstance(s.get("image"), dict):
                im = s["image"]
                out.append('<image href="data:%s;base64,%s" x="%s" y="%s" width="%s" height="%s" opacity="%s"/>' % (
                    _esc(im.get("mime", "image/png")), im.get("data", ""), _f(s.get("x", 0)), _f(s.get("y", 0)),
                    _f(s.get("width", 1)), _f(s.get("height", 1)), _f(s.get("opacity", 1.0))))
                continue
            if preset == "fill":
                out.append('<path d="%s" fill="%s" fill-opacity="%s" fill-rule="%s" data-layer="%s" data-preset="fill" data-color="%s"%s/>' %
                           (_path_d_holes(pts, s.get("holes") or []), paint_ref, _f(op), _esc(s.get("fill_rule", "nonzero")), _esc(name), _rgb_text(s.get("color")),
                            (' data-tag="%s"' % _esc(s["tag"])) if s.get("tag") else ""))
                continue
            cap = "butt" if preset in ("flat", "bristle") else "round"
            attrs = ['d="%s"' % _path_d(pts),
                     'fill="none"',
                     'stroke="%s"' % paint_ref,
                     'stroke-width="%s"' % _f(size),
                     'stroke-opacity="%s"' % _f(op),
                     'stroke-linecap="%s"' % cap,
                     'stroke-linejoin="round"',
                     'data-layer="%s"' % _esc(name),
                     'data-size="%s"' % _f(size),
                     'data-opacity="%s"' % _f(op),
                     'data-hardness="%s"' % _f(hard),
                     'data-preset="%s"' % _esc(preset),
                     'data-color="%s"' % _rgb_text(s.get("color")),
                     'data-p="%s"' % _p_text(pts)]
            if s.get("tag"):
                attrs.append('data-tag="%s"' % _esc(s["tag"]))
            fid = _filter_id(hard) if filters else None
            if fid:
                attrs.append('filter="url(#%s)"' % fid)
            sg = stroke_mask_gid.get(id(s))
            if sg:
                out.append('<g mask="url(#%s)"><path %s/></g>' % (sg, " ".join(attrs)))
            else:
                out.append("<path %s/>" % " ".join(attrs))
        out.append("</g>")
    out.append("</svg>")
    return "\n".join(out) + "\n"


_NUM = re.compile(r"[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?")


def _tokens(d):
    return re.findall(r"[MmLlHhVvCcSsQqTtAaZz]|[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?", d)


def _parse_d_subpaths(d):
    parts = [x for x in re.split(r"(?=[Mm])", str(d or "")) if x.strip()]
    out = []
    for part in parts:
        try:
            out.append(_parse_d(part))
        except Exception:
            continue
    return out or [[]]

def _mat(a, b, c, d, e, f):
    return (a, b, c, d, e, f)

def _mat_mul(m, n):
    return (m[0]*n[0]+m[2]*n[1], m[1]*n[0]+m[3]*n[1],
            m[0]*n[2]+m[2]*n[3], m[1]*n[2]+m[3]*n[3],
            m[0]*n[4]+m[2]*n[5]+m[4], m[1]*n[4]+m[3]*n[5]+m[5])

def _mat_apply(m, x, y):
    return (m[0]*x + m[2]*y + m[4], m[1]*x + m[3]*y + m[5])

def _parse_transform(text):
    m = _mat(1, 0, 0, 1, 0, 0)
    for name, body in re.findall(r"([A-Za-z]+)\s*\(([^)]*)\)", text or ""):
        v = [float(x) for x in re.findall(r"[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?", body)]
        if name == "matrix" and len(v) >= 6:
            t = _mat(*v[:6])
        elif name == "translate" and v:
            t = _mat(1, 0, 0, 1, v[0], v[1] if len(v) > 1 else 0)
        elif name == "scale" and v:
            t = _mat(v[0], 0, 0, v[1] if len(v) > 1 else v[0], 0, 0)
        elif name == "rotate" and v:
            a = math.radians(v[0]); ca, sa = math.cos(a), math.sin(a)
            t = _mat(ca, sa, -sa, ca, 0, 0)
            if len(v) >= 3:
                t = _mat_mul(_mat_mul(_mat(1, 0, 0, 1, v[1], v[2]), t), _mat(1, 0, 0, 1, -v[1], -v[2]))
        else:
            continue
        m = _mat_mul(m, t)
    return m


def _arc_pts(p0, p1, rx, ry, phi, laf, sf, n=16):
    rx, ry = abs(rx), abs(ry)
    if rx < 1e-9 or ry < 1e-9:
        return [p1]
    ph = math.radians(phi); cp, sp = math.cos(ph), math.sin(ph)
    dx, dy = (p0[0]-p1[0])/2.0, (p0[1]-p1[1])/2.0
    x1 = cp*dx + sp*dy; y1 = -sp*dx + cp*dy
    lam = (x1*x1)/(rx*rx) + (y1*y1)/(ry*ry)
    if lam > 1:
        q = math.sqrt(lam); rx *= q; ry *= q
    num = max(0.0, rx*rx*ry*ry - rx*rx*y1*y1 - ry*ry*x1*x1)
    den = rx*rx*y1*y1 + ry*ry*x1*x1
    co = math.sqrt(num/den) if den else 0.0
    if laf == sf:
        co = -co
    cxp = co * rx*y1/ry; cyp = -co * ry*x1/rx
    cx = cp*cxp - sp*cyp + (p0[0]+p1[0])/2.0
    cy = sp*cxp + cp*cyp + (p0[1]+p1[1])/2.0
    def ang(ux, uy, vx, vy):
        return math.atan2(ux*vy-uy*vx, ux*vx+uy*vy)
    t1 = ang(1, 0, (x1-cxp)/rx, (y1-cyp)/ry)
    dt = ang((x1-cxp)/rx, (y1-cyp)/ry, (-x1-cxp)/rx, (-y1-cyp)/ry)
    if not sf and dt > 0:
        dt -= 2*math.pi
    elif sf and dt < 0:
        dt += 2*math.pi
    out = []
    for k in range(1, n+1):
        t = t1 + dt*k/n
        out.append([cx + rx*cp*math.cos(t) - ry*sp*math.sin(t),
                    cy + rx*sp*math.cos(t) + ry*cp*math.sin(t)])
    return out


def _parse_d(d):
    """Sample a path into [[x,y],...]; supports M/L/H/V/C/S/Q/T/Z, not A."""
    if d.count("M") == 1 and not re.search(r"[CcSsQqTtAaHhVvZz]", d):
        vals = [float(x) for x in _NUM.findall(d)]
        if len(vals) >= 2:
            return [[vals[i], vals[i + 1]] for i in range(0, len(vals) - 1, 2)]
    toks = _tokens(d)
    i = 0
    cmd = None
    cur = [0.0, 0.0]
    start = [0.0, 0.0]
    pts = []
    prev_ctrl = None
    prev_cmd = None

    def nums(n):
        nonlocal i
        vals = []
        for _ in range(n):
            while i < len(toks) and toks[i] not in "MmLlHhVvCcSsQqTtAaZz":
                vals.append(float(toks[i])); i += 1
            if len(vals) < n:
                raise ValueError("bad path: expected %d numbers" % n)
        return vals

    def add(p):
        if not pts or abs(pts[-1][0] - p[0]) > 1e-9 or abs(pts[-1][1] - p[1]) > 1e-9:
            pts.append([float(p[0]), float(p[1])])

    while i < len(toks):
        if toks[i] in "MmLlHhVvCcSsQqTtAaZz":
            cmd = toks[i]; i += 1
        if cmd is None:
            raise ValueError("bad path: command expected")
        rel = cmd.islower()
        C = cmd.upper()
        if C == "Z":
            cur = list(start); add(cur); prev_ctrl = None; prev_cmd = C; continue
        if C == "M":
            x, y = nums(2)
            if rel:
                cur[0] += x; cur[1] += y
            else:
                cur = [x, y]
            start = list(cur); add(cur); prev_ctrl = None; prev_cmd = C
            cmd = "l" if rel else "L"
        elif C == "L":
            x, y = nums(2)
            if rel:
                cur[0] += x; cur[1] += y
            else:
                cur = [x, y]
            add(cur); prev_ctrl = None; prev_cmd = C
        elif C == "H":
            x = nums(1)[0]
            cur[0] = cur[0] + x if rel else x
            add(cur); prev_ctrl = None; prev_cmd = C
        elif C == "V":
            y = nums(1)[0]
            cur[1] = cur[1] + y if rel else y
            add(cur); prev_ctrl = None; prev_cmd = C
        elif C == "C":
            x1, y1, x2, y2, x, y = nums(6)
            if rel:
                x1 += cur[0]; y1 += cur[1]; x2 += cur[0]; y2 += cur[1]; x += cur[0]; y += cur[1]
            p0 = list(cur)
            for k in range(1, 9):
                t = k / 8.0; u = 1 - t
                add([u*u*u*p0[0] + 3*u*u*t*x1 + 3*u*t*t*x2 + t*t*t*x,
                     u*u*u*p0[1] + 3*u*u*t*y1 + 3*u*t*t*y2 + t*t*t*y])
            cur = [x, y]; prev_ctrl = [x2, y2]; prev_cmd = C
        elif C == "S":
            x2, y2, x, y = nums(4)
            if rel:
                x2 += cur[0]; y2 += cur[1]; x += cur[0]; y += cur[1]
            x1, y1 = (2*cur[0]-prev_ctrl[0], 2*cur[1]-prev_ctrl[1]) if prev_cmd in ("C", "S") and prev_ctrl else cur
            p0 = list(cur)
            for k in range(1, 9):
                t = k / 8.0; u = 1 - t
                add([u*u*u*p0[0] + 3*u*u*t*x1 + 3*u*t*t*x2 + t*t*t*x,
                     u*u*u*p0[1] + 3*u*u*t*y1 + 3*u*t*t*y2 + t*t*t*y])
            cur = [x, y]; prev_ctrl = [x2, y2]; prev_cmd = C
        elif C == "Q":
            x1, y1, x, y = nums(4)
            if rel:
                x1 += cur[0]; y1 += cur[1]; x += cur[0]; y += cur[1]
            p0 = list(cur)
            for k in range(1, 9):
                t = k / 8.0; u = 1 - t
                add([u*u*p0[0] + 2*u*t*x1 + t*t*x, u*u*p0[1] + 2*u*t*y1 + t*t*y])
            cur = [x, y]; prev_ctrl = [x1, y1]; prev_cmd = C
        elif C == "A":
            rx, ry, rot, laf, sf, x, y = nums(7)
            if rel:
                x += cur[0]; y += cur[1]
            p0 = list(cur)
            for q in _arc_pts(p0, [x, y], rx, ry, rot, int(laf), int(sf)):
                add(q)
            cur = [x, y]; prev_ctrl = None; prev_cmd = C
        elif C == "T":
            x, y = nums(2)
            if rel:
                x += cur[0]; y += cur[1]
            x1, y1 = (2*cur[0]-prev_ctrl[0], 2*cur[1]-prev_ctrl[1]) if prev_cmd in ("Q", "T") and prev_ctrl else cur
            p0 = list(cur)
            for k in range(1, 9):
                t = k / 8.0; u = 1 - t
                add([u*u*p0[0] + 2*u*t*x1 + t*t*x, u*u*p0[1] + 2*u*t*y1 + t*t*y])
            cur = [x, y]; prev_ctrl = [x1, y1]; prev_cmd = C
        else:
            raise ValueError("unsupported path command %s" % cmd)
    return pts or [[0.0, 0.0]]


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _floats(s, default):
    if not s:
        return list(default)
    return [float(x) for x in s.replace(";", ",").split(",")]


def _color(s, default=(217, 180, 90)):
    try:
        return color_convert.to_srgb(s)
    except Exception:
        return list(default)



def plan_to_svg_stamps(doc, title=None):
    """Stamp-level SVG with approximate brush tip gradients/shapes (debug/visual reference)."""
    w, h = [int(v) for v in doc["canvas"]]
    layers = doc.get("layers") or []
    order, blends = [], {}
    for l in layers:
        n = str(l.get("name", "layer"))
        if n not in blends:
            order.append(n); blends[n] = str(l.get("blend", "normal"))
    by = {}
    for s in doc.get("strokes") or []:
        n = str(s.get("layer", "layer"))
        if n not in blends:
            order.append(n); blends[n] = "normal"
        by.setdefault(n, []).append(s)
    paint_defs = []
    paint_gid = {}
    mask_defs = []
    layer_mask = {}
    stroke_mask = {}
    for li, l in enumerate(layers):
        m = l.get("mask")
        if isinstance(m, dict) and m.get("encoding") in ("png-base64", "zlib-base64"):
            gid = "lmask%d" % li
            layer_mask[str(l.get("name", "layer"))] = gid
            mask_defs.append(_mask_def(m, gid, w, h))
    for st in doc.get("strokes") or []:
        m = st.get("mask")
        if isinstance(m, dict) and m.get("encoding") in ("png-base64", "zlib-base64"):
            gid = "smask%d" % len(stroke_mask)
            stroke_mask[id(st)] = gid
            mask_defs.append(_mask_def(m, gid, w, h))
        paint = st.get("paint")
        if isinstance(paint, dict):
            pts = st.get("points") or []
            if pts:
                xs = [float(q[0]) for q in pts]; ys = [float(q[1]) for q in pts]
                minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
                units0 = paint.get("units", "objectBoundingBox")
                def cv(k, d):
                    v = paint.get(k, d)
                    pct = isinstance(v, str) and v.endswith("%")
                    q = float(v[:-1]) / 100.0 if pct else float(v)
                    if units0 == "objectBoundingBox" or pct:
                        return minx + (maxx-minx)*q if k in ("x1","x2","cx","fx") else miny + (maxy-miny)*q
                    return q
                pp = dict(paint); pp["units"] = "userSpaceOnUse"
                if paint.get("type") == "radial":
                    pp["cx"] = cv("cx", 0.5); pp["cy"] = cv("cy", 0.5); pp["r"] = max(1e-6, cv("r", 0.5))
                    pp["fx"] = cv("fx", "50%"); pp["fy"] = cv("fy", "50%")
                else:
                    pp["x1"] = cv("x1", 0.0); pp["y1"] = cv("y1", 0.0)
                    pp["x2"] = cv("x2", 1.0); pp["y2"] = cv("y2", 0.0)
                gid = "paint%d" % len(paint_defs)
                paint_gid[id(st)] = gid
                paint_defs.append(_paint_def(pp, gid))
    for st in doc.get("strokes") or []:
        ptn = st.get("pattern")
        if isinstance(ptn, dict) and ptn.get("data"):
            gid = "pat%d" % len(pattern_defs)
            pattern_gid[id(st)] = gid
            pattern_defs.append('<pattern id="%s" width="%s" height="%s" patternUnits="userSpaceOnUse"><image href="data:image/png;base64,%s" width="%s" height="%s"/></pattern>' % (
                gid, _f(ptn.get("width", 32)), _f(ptn.get("height", 32)), ptn.get("data"), _f(ptn.get("width", 32)), _f(ptn.get("height", 32))))
    defs = []; used = {}; body = []
    def grad_id(col, hard):
        q = max(0.0, min(1.0, hard)); q = round(q * 4.0) / 4.0
        if q >= 0.95:
            return None
        key = (col, q)
        if key in used:
            return used[key]
        gid = "t%d" % len(used); used[key] = gid
        mid = 0.55 if q <= 0.25 else (0.7 if q <= 0.5 else 0.82)
        defs.append('<radialGradient id="%s"><stop offset="0" stop-color="%s" stop-opacity="1"/><stop offset="%s" stop-color="%s" stop-opacity="0.55"/><stop offset="1" stop-color="%s" stop-opacity="0"/></radialGradient>' % (gid, col, _f(mid), col, col))
        return gid
    for name in order:
        lm = layer_mask.get(name)
        body.append('<g data-name=%s data-blend=%s%s>' % (quoteattr(name), quoteattr(blends[name]), (' mask="url(#%s)"' % lm) if lm else ""))
        for st in by.get(name, []):
            pts = st.get("points") or []
            if not pts:
                continue
            size = float(st.get("size", 8.0)); base = size * 0.5
            col = _rgb(st.get("color")); op0 = max(0.0, min(1.0, float(st.get("opacity", 1.0))))
            preset = str(st.get("preset", "round_soft")); hard = float(st.get("hardness", 0.7))
            ps = [(float(p[0]), float(p[1]), float(p[2] if len(p) > 2 else 1.0)) for p in pts]
            sm = stroke_mask.get(id(st))
            pre = ('<g mask="url(#%s)">' % sm) if sm else ""
            post = "</g>" if sm else ""
            if preset == "fill":
                d0 = _path_d_compact(pts, st.get("holes") or [])
                pg = pattern_gid.get(id(st)) or paint_gid.get(id(st))
                body.append(pre + '<path d="%s" fill="%s" fill-opacity="%s" fill-rule="%s" data-layer=%s data-preset="fill" data-color="%s"/>' % (
                    d0, ("url(#%s)" % pg) if pg else col, _f(op0), st.get("fill_rule", "nonzero"), quoteattr(name), col) + post)
                continue
            body.append(pre + '<g data-stroke="1" data-layer=%s data-preset=%s data-size="%s" data-opacity="%s" data-hardness="%s" data-color="%s" data-center="%s" data-center-hash="%s"%s>' % (
                quoteattr(name), quoteattr(preset), _f(size), _f(op0), _f(hard), col, _center_attr(ps), _center_hash(ps),
                (' data-tag=' + quoteattr(str(st["tag"]))) if st.get("tag") else ""))
            for i in range(1, len(pts)):
                x0, y0, p0 = float(pts[i-1][0]), float(pts[i-1][1]), float(pts[i-1][2] if len(pts[i-1]) > 2 else 1.0)
                x1, y1, p1 = float(pts[i][0]), float(pts[i][1]), float(pts[i][2] if len(pts[i]) > 2 else 1.0)
                dx, dy = x1-x0, y1-y0; ln = math.hypot(dx, dy); ang = math.degrees(math.atan2(dy, dx))
                step = max(0.6, base * 0.30); steps = max(1, int(math.floor(ln / step)))
                for k in range(steps + 1):
                    t = k / float(steps); x = x0 + dx*t; y = y0 + dy*t
                    press = p0 + (p1-p0)*t; pr = 0.30 + 0.70*press; pa = 0.15 + 0.85*press
                    r = base * pr
                    if r <= 0:
                        continue
                    op = max(0.0, min(1.0, op0 * pa)); pg = paint_gid.get(id(st))
                    if pg:
                        fill = "url(#%s)" % pg
                    else:
                        gid = grad_id(col, hard)
                        fill = ("url(#%s)" % gid) if gid else col
                    if preset in ("flat", "bristle"):
                        body.append('<ellipse cx="%s" cy="%s" rx="%s" ry="%s" fill="%s" fill-opacity="%s" transform="rotate(%s %s %s)"/>' % (_f(x), _f(y), _f(r), _f(max(0.8, r * 0.35)), fill, _f(op), _f(ang), _f(x), _f(y)))
                    else:
                        body.append('<circle cx="%s" cy="%s" r="%s" fill="%s" fill-opacity="%s"/>' % (_f(x), _f(y), _f(r), fill, _f(op)))
            body.append(post + "</g>")
        body.append("</g>")
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d" data-bridge="plan_svg_stamps" shape-rendering="geometricPrecision">' % (w, h, w, h)]
    if title:
        out.append("<title>%s</title>" % escape(str(title)))
    if defs or paint_defs or mask_defs:
        out.append("<defs>%s</defs>" % "\n".join(defs + paint_defs + mask_defs))
    out.extend(body); out.append("</svg>")
    return "\n".join(out) + "\n"


def _capsule(p0, p1, r0, r1, steps=8):
    x0, y0 = p0; x1, y1 = p1
    dx, dy = x1 - x0, y1 - y0
    ln = math.hypot(dx, dy)
    if ln < 1e-9:
        a = math.atan2(dy, dx)
        ux, uy = 1.0, 0.0
    else:
        ux, uy = dx / ln, dy / ln
    nx, ny = -uy, ux
    left0 = (x0 + nx*r0, y0 + ny*r0); left1 = (x1 + nx*r1, y1 + ny*r1)
    right1 = (x1 - nx*r1, y1 - ny*r1); right0 = (x0 - nx*r0, y0 - ny*r0)
    a = math.atan2(uy, ux)
    pts = [left0, left1]
    for k in range(1, steps):
        t = a + math.pi/2 - math.pi * (k / steps)
        pts.append((x1 + math.cos(t)*r1, y1 + math.sin(t)*r1))
    pts += [right1, right0]
    for k in range(1, steps):
        t = a - math.pi/2 - math.pi * (k / steps)
        pts.append((x0 + math.cos(t)*r0, y0 + math.sin(t)*r0))
    return pts

def _d_hash(d):
    return hashlib.md5(str(d).encode("utf-8")).hexdigest()[:12]

def _center_hash(ps):
    h = 1469598103934665603
    for b in _center_attr(ps).encode("utf-8"):
        h ^= b; h = (h * 1099511628211) & 0xFFFFFFFFFFFFFFFF
    return "%016x" % h

def _center_attr(ps):
    return ";".join("%s,%s,%s" % (_f(p[0]), _f(p[1]), _f(p[2] if len(p) > 2 else 1.0)) for p in ps)

def plan_to_svg_outline(doc, title=None, texture=False, soft=False):
    """One path per stroke: capsule subpaths unioned by nonzero fill."""
    w, h = [int(v) for v in doc["canvas"]]
    layers = doc.get("layers") or []
    order, blends = [], {}
    for l in layers:
        n = str(l.get("name", "layer"))
        if n not in blends:
            order.append(n); blends[n] = str(l.get("blend", "normal"))
    by = {}
    for s in doc.get("strokes") or []:
        n = str(s.get("layer", "layer"))
        if n not in blends:
            order.append(n); blends[n] = "normal"
        by.setdefault(n, []).append(s)
    paint_defs = []
    paint_gid = {}
    mask_defs = []
    layer_mask = {}
    stroke_mask = {}
    for li, l in enumerate(layers):
        m = l.get("mask")
        if isinstance(m, dict) and m.get("encoding") in ("png-base64", "zlib-base64"):
            gid = "lmask%d" % li
            layer_mask[str(l.get("name", "layer"))] = gid
            mask_defs.append(_mask_def(m, gid, w, h))
    for st in doc.get("strokes") or []:
        if isinstance(st.get("paint"), dict):
            gid = "paint%d" % len(paint_defs)
            paint_gid[id(st)] = gid
            paint_defs.append(_paint_def(st["paint"], gid))
        m = st.get("mask")
        if isinstance(m, dict) and m.get("encoding") in ("png-base64", "zlib-base64"):
            gid = "smask%d" % len(stroke_mask)
            stroke_mask[id(st)] = gid
            mask_defs.append(_mask_def(m, gid, w, h))
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d" data-bridge="plan_svg_outline" shape-rendering="geometricPrecision">' % (w, h, w, h)]
    if title:
        out.append("<title>%s</title>" % escape(str(title)))
    if paint_defs or mask_defs:
        out.append("<defs>%s</defs>" % "".join(paint_defs + mask_defs))
    for name in order:
        lm = layer_mask.get(name)
        out.append('<g data-name=%s data-blend=%s%s%s>' % (quoteattr(name), quoteattr(blends[name]), ' filter="url(#soft)"' if soft else "", (' mask="url(#%s)"' % lm) if lm else ""))
        for st in by.get(name, []):
            pts = st.get("points") or []
            if not pts:
                continue
            preset = str(st.get("preset", "round_soft"))
            pgid = paint_gid.get(id(st))
            paint_ref = ("url(#%s)" % pgid) if pgid else _rgb(st.get("color"))
            sm = stroke_mask.get(id(st))
            pre = ('<g mask="url(#%s)">' % sm) if sm else ""
            post = "</g>" if sm else ""
            if preset == "fill":
                d0 = _path_d_holes(pts, st.get("holes") or [])
                out.append(pre + '<path d="%s" fill="%s" fill-opacity="%s" fill-rule="%s" data-layer=%s data-preset="fill" data-color="%s"/>' % (
                    d0, paint_ref, _f(st.get("opacity", 1.0)), st.get("fill_rule", "nonzero"), quoteattr(name), _rgb(st.get("color"))) + post)
                continue
            size = float(st.get("size", 8.0)); base = size * 0.5
            ps = [(float(p[0]), float(p[1]), float(p[2] if len(p) > 2 else 1.0)) for p in pts]
            radii = [max(0.3, base * (0.30 + 0.70 * p[2])) for p in ps]
            if len(ps) == 1:
                x, y = ps[0][0], ps[0][1]; r = radii[0]
                d = "M %s %s m -%s 0 a %s %s 0 1 0 %s 0 a %s %s 0 1 0 -%s 0 Z" % (_f(x), _f(y), _f(r), _f(r), _f(r), _f(2*r), _f(r), _f(r), _f(2*r))
            else:
                parts = []
                for i in range(1, len(ps)):
                    seg = _capsule((ps[i-1][0], ps[i-1][1]), (ps[i][0], ps[i][1]), radii[i-1], radii[i])
                    parts.append(_path_d_compact(seg))
                d = " ".join(p for p in parts if p)
            out.append(pre + '<path d="%s" fill="%s" fill-opacity="%s" fill-rule="nonzero" data-layer=%s data-preset=%s data-size="%s" data-opacity="%s" data-hardness="%s" data-color="%s" data-center="%s" data-d-hash="%s"%s/>' %
                       (d, paint_ref, _f(st.get("opacity", 1.0)), quoteattr(name), quoteattr(preset),
                        _f(size), _f(st.get("opacity", 1.0)), _f(st.get("hardness", 0.7)), _rgb(st.get("color")), _center_attr(ps), _d_hash(d),
                        (' data-tag=' + quoteattr(str(st["tag"]))) if st.get("tag") else "") + post)
        out.append("</g>")
    out.append("</svg>")
    return "\n".join(out) + "\n"


def _center_pts(text):
    out = []
    for chunk in str(text or "").replace(";", " ").split():
        q = [float(v) for v in chunk.split(",")[:3]]
        if len(q) >= 2:
            out.append(q + ([1.0] if len(q) == 2 else []))
    return out

def _shape_points(tag, a):
    def fl(k, d=0.0):
        try:
            return float(a.get(k, d))
        except Exception:
            return d
    if tag == "circle":
        cx, cy, r = fl("cx"), fl("cy"), fl("r")
        return [[cx + r*math.cos(2*math.pi*k/32), cy + r*math.sin(2*math.pi*k/32)] for k in range(32)] + [[cx+r, cy]]
    if tag == "ellipse":
        cx, cy, rx, ry = fl("cx"), fl("cy"), fl("rx"), fl("ry")
        return [[cx + rx*math.cos(2*math.pi*k/32), cy + ry*math.sin(2*math.pi*k/32)] for k in range(32)] + [[cx+rx, cy]]
    if tag == "line":
        return [[fl("x1"), fl("y1")], [fl("x2"), fl("y2")]]
    if tag == "rect":
        x, y, w, h = fl("x"), fl("y"), fl("width"), fl("height")
        return [[x, y], [x+w, y], [x+w, y+h], [x, y+h], [x, y]]
    if tag in ("polygon", "polyline"):
        v = [float(x) for x in _NUM.findall(a.get("points", ""))]
        pts = [[v[i], v[i+1]] for i in range(0, len(v)-1, 2)]
        if tag == "polygon" and pts:
            pts.append(pts[0])
        return pts
    return []

def _stroke_from_attrs(ch, name, pts):
    a = ch if isinstance(ch, dict) else ch.attrib
    st = {"layer": name, "preset": a.get("data-preset", "round_soft"),
          "size": float(a.get("data-size", a.get("stroke-width", 8.0))),
          "opacity": max(0.0, min(1.0, float(a.get("data-opacity", a.get("stroke-opacity", 1.0))))),
          "color": _color(a.get("data-color") or a.get("stroke") or a.get("fill"), (217, 180, 90)),
          "hardness": max(0.0, min(1.0, float(a.get("data-hardness", 0.7)))),
          "points": [[float(p[0]), float(p[1]), float(p[2] if len(p) > 2 else 1.0)] for p in pts]}
    if a.get("data-tag"):
        st["tag"] = str(a["data-tag"])
    if a.get("_paint"):
        st["paint"] = a["_paint"]
    return st

def _style_decls(text):
    out = {}
    for part in str(text or "").split(";"):
        if ":" in part:
            k, v = part.split(":", 1)
            out[k.strip()] = v.strip()
    return out


def _clip_poly(subject, clip):
    if len(subject) < 3 or len(clip) < 3:
        return []
    area = sum(clip[i][0]*clip[(i+1) % len(clip)][1] - clip[(i+1) % len(clip)][0]*clip[i][1] for i in range(len(clip)))
    poly = list(clip) if area > 0 else list(reversed(clip))
    out = list(subject)
    for i in range(len(poly)):
        a, b = poly[i], poly[(i+1) % len(poly)]
        ex, ey = b[0]-a[0], b[1]-a[1]
        def inside(p):
            return ex*(p[1]-a[1]) - ey*(p[0]-a[0]) >= -1e-9
        def cross(p, q):
            dx, dy = q[0]-p[0], q[1]-p[1]
            den = ex*dy - ey*dx
            if abs(den) < 1e-12:
                return p
            t = (ex*(a[1]-p[1]) - ey*(a[0]-p[0])) / den
            return [p[0] + dx*t, p[1] + dy*t]
        inp = out; out = []
        if not inp:
            break
        for j in range(len(inp)):
            cur, prev = inp[j], inp[j-1]
            if inside(cur):
                if not inside(prev):
                    out.append(cross(prev, cur))
                out.append(cur)
            elif inside(prev):
                out.append(cross(prev, cur))
    return out


def _clip_line(p0, p1, clip):
    area = sum(clip[i][0]*clip[(i+1) % len(clip)][1] - clip[(i+1) % len(clip)][0]*clip[i][1] for i in range(len(clip)))
    poly = clip if area > 0 else list(reversed(clip))
    t0, t1 = 0.0, 1.0
    dx, dy = p1[0]-p0[0], p1[1]-p0[1]
    for i in range(len(poly)):
        a, b = poly[i], poly[(i+1) % len(poly)]
        ex, ey = b[0]-a[0], b[1]-a[1]
        num = ex*(p0[1]-a[1]) - ey*(p0[0]-a[0])
        den = ex*dy - ey*dx
        if abs(den) < 1e-12:
            if num < 0:
                return None
            continue
        t = -num/den
        if den > 0:
            if t > t1: return None
            if t > t0: t0 = t
        else:
            if t < t0: return None
            if t < t1: t1 = t
    if t0 > t1:
        return None
    return [[p0[0]+dx*t0, p0[1]+dy*t0], [p0[0]+dx*t1, p0[1]+dy*t1]]



def _is_convex(poly):
    pts = [(float(x), float(y)) for x, y in poly]
    if len(pts) > 3 and pts[0] == pts[-1]:
        pts.pop()
    n = len(pts)
    sign = 0
    for i in range(n):
        a, b, c = pts[i], pts[(i+1) % n], pts[(i+2) % n]
        cr = (b[0]-a[0])*(c[1]-a[1]) - (b[1]-a[1])*(c[0]-a[0])
        if abs(cr) < 1e-12:
            continue
        s = 1 if cr > 0 else -1
        if sign and s != sign:
            return False
        sign = s
    return True

def _triangulate(poly):
    pts = [(float(x), float(y)) for x, y in poly]
    if len(pts) > 3 and pts[0] == pts[-1]:
        pts.pop()
    n = len(pts)
    if n < 3:
        return []
    area = sum(pts[i][0]*pts[(i+1) % n][1] - pts[(i+1) % n][0]*pts[i][1] for i in range(n))
    if area < 0:
        pts.reverse()
    idx = list(range(n))
    tris = []
    guard = 0
    while len(idx) > 3 and guard < n*n:
        guard += 1
        ear = False
        for k in range(len(idx)):
            i0, i1, i2 = idx[k-1], idx[k], idx[(k+1) % len(idx)]
            a, b, c = pts[i0], pts[i1], pts[i2]
            cross = (b[0]-a[0])*(c[1]-a[1]) - (b[1]-a[1])*(c[0]-a[0])
            if cross <= 1e-12:
                continue
            inside = False
            for j in idx:
                if j in (i0, i1, i2):
                    continue
                q = pts[j]
                d1 = (b[0]-a[0])*(q[1]-a[1]) - (b[1]-a[1])*(q[0]-a[0])
                d2 = (c[0]-b[0])*(q[1]-b[1]) - (c[1]-b[1])*(q[0]-b[0])
                d3 = (a[0]-c[0])*(q[1]-c[1]) - (a[1]-c[1])*(q[0]-c[0])
                if d1 >= 0 and d2 >= 0 and d3 >= 0:
                    inside = True
                    break
            if not inside:
                tris.append([a, b, c])
                idx.pop(k)
                ear = True
                break
        if not ear:
            break
    if len(idx) == 3:
        tris.append([pts[i] for i in idx])
    return tris

def _clip_line_evenodd(p0, p1, polys):
    ts = [0.0, 1.0]
    dx, dy = p1[0]-p0[0], p1[1]-p0[1]
    for poly in polys:
        for i in range(len(poly)):
            a, b = poly[i], poly[(i+1) % len(poly)]
            ex, ey = b[0]-a[0], b[1]-a[1]
            den = ex*dy - ey*dx
            if abs(den) < 1e-12:
                continue
            t = (ex*(a[1]-p0[1]) - ey*(a[0]-p0[0])) / den
            if 1e-9 < t < 1-1e-9:
                ts.append(t)
    ts = sorted(set(round(t, 9) for t in ts))
    out = []
    def inside(x, y):
        c = False
        for poly in polys:
            for i in range(len(poly)):
                a, b = poly[i], poly[(i+1) % len(poly)]
                if ((a[1] > y) != (b[1] > y)) and (x < (b[0]-a[0])*(y-a[1])/(b[1]-a[1]) + a[0]):
                    c = not c
        return c
    for i in range(1, len(ts)):
        t0, t1 = ts[i-1], ts[i]
        tm = (t0+t1)/2
        if inside(p0[0]+dx*tm, p0[1]+dy*tm):
            out.append([[p0[0]+dx*t0, p0[1]+dy*t0], [p0[0]+dx*t1, p0[1]+dy*t1]])
    return out


def _clip_geoms(root):
    out = {}
    for el in root.iter():
        if _local(el.tag) not in ("clipPath", "mask") or not el.get("id"):
            continue
        polys = []
        def walk(node, parent):
            for ch in list(node):
                tag = _local(ch.tag)
                m = _mat_mul(parent, _parse_transform(ch.get("transform")))
                if tag in ("g",):
                    walk(ch, m)
                elif tag == "path":
                    for sub in _parse_d_subpaths(ch.get("d", "")):
                        if len(sub) >= 3:
                            polys.append([[*_mat_apply(m, q[0], q[1])] for q in sub])
                elif tag in ("rect", "circle", "ellipse", "polygon"):
                    q = _shape_points(tag, ch.attrib)
                    if len(q) > 1 and q[0] == q[-1]:
                        q = q[:-1]
                    if len(q) >= 3:
                        polys.append([[*_mat_apply(m, qq[0], qq[1])] for qq in q])
        walk(el, _parse_transform(el.get("transform")))
        if polys:
            rule = el.get("clip-rule") or (list(el)[0].get("clip-rule") if list(el) else "") or "nonzero"
            out[el.get("id")] = {"polys": polys, "evenodd": str(rule) == "evenodd",
                                 "units": el.get("clipPathUnits", "userSpaceOnUse")}
    return out

def _css_media_text(text):
    out=[]; i=0
    while True:
        m=re.search(r"@media[^{]*\{", text[i:])
        if not m: out.append(text[i:]); break
        start=i+m.start(); brace=i+m.end()-1
        depth=0; j=brace
        while j < len(text):
            if text[j]=='{': depth+=1
            elif text[j]=='}':
                depth-=1
                if depth==0: break
            j+=1
        out.append(text[i:start]); out.append(text[brace+1:j]); i=j+1
    return "".join(out)


def _load_css(root, base_dir=None):
    rules = []
    texts = []
    for el in root.iter():
        if _local(el.tag) == "style":
            texts.append(el.text or "")
        elif _local(el.tag) == "link" and "stylesheet" in str(el.get("rel", "")):
            href = el.get("href") or ""
            if not href:
                continue
            if "://" in href or href.startswith("data:"):
                raise ValueError("external stylesheet is not allowed: %s" % href)
            if base_dir is None:
                raise ValueError("external stylesheet requires base_dir: %s" % href)
            path = (Path(base_dir) / href).resolve()
            try:
                path.relative_to(Path(base_dir).resolve())
            except Exception:
                raise ValueError("stylesheet escapes base_dir: %s" % href)
            texts.append(path.read_text(encoding="utf-8"))
    texts = [_css_media_text(t) for t in texts]
    variables = {}
    for text in texts:
        for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", text or ""):
            if sel.strip() not in (":root", "html", "*"):
                continue
            for part in body.split(";"):
                if ":" in part:
                    k, v = part.split(":", 1)
                    k = k.strip()
                    if k.startswith("--"):
                        variables[k] = v.strip()
    def expand(v):
        for _ in range(8):
            m = re.search(r"var\((--[\w-]+)(?:\s*,\s*([^)]*))?\)", v)
            if not m:
                break
            name, fallback = m.group(1), m.group(2)
            rep = variables.get(name, fallback if fallback is not None else "")
            v = v[:m.start()] + rep + v[m.end():]
        return v
    order = 0
    for text in texts:
        for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", text or ""):
            if sel.strip().startswith("@"):
                continue
            decls = {}
            important = set()
            for part in body.split(";"):
                if ":" not in part:
                    continue
                k, v = part.split(":", 1)
                k = k.strip(); v = expand(v.strip())
                if v.endswith("!important"):
                    important.add(k); v = v[:-10].strip()
                decls[k] = v
            for one in sel.split(","):
                one = one.strip()
                if not one:
                    continue
                spec = one.count("#") * 100 + one.count(".") * 10 + sum(1 for c in one if c.isalpha())
                order += 1
                rules.append({"sel": one, "decl": decls, "imp": important, "spec": spec, "order": order})
    return rules
def _selector_match(sel, ch, pseudo_mode="ignore"):
    import re as _re
    m = _re.search(r":([a-z-]+)", sel)
    if m:
        want = m.group(1)
        if pseudo_mode == "ignore":
            pass
        elif pseudo_mode == want:
            sel = sel[:m.start()] + sel[m.end():]
        else:
            return False
    if sel.endswith("*"):
        return True
    if sel.startswith("."):
        return sel[1:] in (ch.get("class") or "").split()
    if sel.startswith("#"):
        return ch.get("id") == sel[1:]
    return _local(ch.tag) == sel if hasattr(ch, "tag") else False


def _resolved_attrs(ch, css, grads, paints=None, inh=None, pseudo_mode="ignore"):
    a = dict(ch.attrib)
    for rule in sorted(css or [], key=lambda r: (bool(r["imp"]), r["spec"], r["order"])):
        if _selector_match(rule["sel"], a, pseudo_mode):
            for k, v in rule["decl"].items():
                if k in rule["imp"] or k not in a:
                    a[k] = v
    for k, v in _style_decls(a.get("style", "")).items():
        a[k] = v
    if inh:
        for k, v in inh.items():
            a.setdefault(k, v)
    for key in ("fill", "stroke"):
        m = re.match(r"url\(#([^)]+)\)", str(a.get(key, "")))
        if not m:
            continue
        gid = m.group(1)
        if gid in grads:
            a[key] = grads[gid]
        if paints and gid in paints:
            a["_paint"] = paints[gid]
    return a


def _mask_png(im):
    from PIL import Image
    import numpy as np
    arr = np.asarray(im, dtype=np.uint8)
    ys, xs = np.nonzero(arr)
    if len(xs):
        x0, y0, x1, y1 = int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1
        im = Image.fromarray(arr[y0:y1, x0:x1])
    else:
        x0 = y0 = 0
        im = Image.new("L", (1, 1), 0)
    w, h = im.size
    data = np.asarray(im, dtype=np.uint8).tobytes()
    return {"encoding": "zlib-base64", "data": base64.b64encode(zlib.compress(data, 6)).decode("ascii"),
            "width": w, "height": h, "x": x0, "y": y0}

def _load_profiles(root, base_dir=None):
    import re as _re
    color_convert.clear_profiles()
    for el in root.iter():
        if _local(el.tag) == "color-profile" and el.get("name"):
            href = el.get("href") or el.get("{http://www.w3.org/1999/xlink}href") or ""
            if not href or base_dir is None:
                continue
            path = (Path(base_dir) / href).resolve()
            try:
                path.relative_to(Path(base_dir).resolve())
            except Exception:
                raise ValueError("ICC profile escapes base_dir: %s" % href)
            color_convert.register_profile(el.get("name"), path.read_bytes())
        if _local(el.tag) == "style":
            for name, body in _re.findall(r"@color-profile\s+([\w-]+)\s*\{([^}]*)\}", el.text or ""):
                m = _re.search(r"url\((?:'|\")?([^)'\"]+)", body)
                if not m or base_dir is None:
                    continue
                path = (Path(base_dir) / m.group(1)).resolve()
                try:
                    path.relative_to(Path(base_dir).resolve())
                except Exception:
                    raise ValueError("ICC profile escapes base_dir: %s" % m.group(1))
                color_convert.register_profile(name, path.read_bytes())

def _mask_elements(root):
    return {el.get("id"): el for el in root.iter()
            if _local(el.tag) == "mask" and el.get("id")}


def _raster_mask_element(el, w, h, bbox=None, content_units="userSpaceOnUse", root=None):
    from PIL import Image, ImageDraw
    im = Image.new("L", (w, h), 0)
    alpha_mode = str(el.get("mask-type", "")).lower() == "alpha"
    def lum(ch):
        if alpha_mode:
            a = max(0.0, min(1.0, float(ch.get("fill-opacity", ch.get("opacity", 1.0)))))
            return int(round(a * 255))
        fill = str(ch.get("fill", "#fff")).lower()
        if fill.startswith("url("):
            return 255
        if fill in ("white", "#fff", "#ffffff"):
            return 255
        if fill in ("black", "#000", "#000000"):
            return 0
        c = _color(fill, (255, 255, 255))
        a = max(0.0, min(1.0, float(ch.get("fill-opacity", ch.get("opacity", 1.0)))))
        return int(round((0.299*c[0] + 0.587*c[1] + 0.114*c[2]) * a))
    gpaints = _gradient_paints(root if root is not None else el)
    base = _mat(1, 0, 0, 1, 0, 0)
    if content_units == "objectBoundingBox" and bbox:
        bx, by, bw, bh = bbox
        base = _mat(bw, 0, 0, bh, bx, by)
    base = _mat_mul(base, _parse_transform(el.get("transform")))
    def draw_node(ch, parent):
        nonlocal im
        tag = _local(ch.tag)
        m = _mat_mul(parent, _parse_transform(ch.get("transform")))
        if tag == "image":
            href = ch.get("href") or ch.get("{http://www.w3.org/1999/xlink}href") or ""
            if str(href).startswith("data:image/png;base64,"):
                from PIL import ImageChops
                img0 = Image.open(io.BytesIO(base64.b64decode(str(href).split(",", 1)[1])))
                q = img0.convert("RGBA").getchannel("A") if alpha_mode else img0.convert("L")
                if q.size != (w, h):
                    q = q.resize((w, h), Image.BILINEAR)
                im = ImageChops.lighter(im, q)
            return
        if tag == "g":
            for q in list(ch):
                draw_node(q, m)
            return
        tmp = Image.new("L", (w, h), 0)
        td = ImageDraw.Draw(tmp)
        if tag in ("rect", "circle", "ellipse", "polygon", "polyline", "line"):
            q = _shape_points(tag, ch.attrib)
            if len(q) >= 2:
                td.polygon([tuple(_mat_apply(m, x, y)) for x, y in q], fill=255)
        elif tag == "path":
            for pts in _parse_d_subpaths(ch.get("d", "")):
                if len(pts) >= 3:
                    td.polygon([tuple(_mat_apply(m, q[0], q[1])) for q in pts], fill=255)
        else:
            return
        import numpy as np
        arr = np.asarray(tmp, dtype=np.float32) / 255.0
        fill = str(ch.get("fill", "#fff")).lower()
        m2 = re.match(r"url\(#([^)]+)\)", fill)
        if m2 and m2.group(1) in gpaints:
            paint = gpaints[m2.group(1)]
            ys, xs = np.nonzero(arr > 0)
            if len(xs):
                x0, x1 = xs.min(), xs.max(); y0, y1 = ys.min(), ys.max()
                def cv(k, d):
                    v = paint.get(k, d)
                    if isinstance(v, str) and v.endswith("%"):
                        q = float(v[:-1]) / 100.0
                        return x0 + (x1-x0)*q if k in ("x1", "x2", "cx", "fx") else y0 + (y1-y0)*q
                    return float(v)
                if paint.get("type") == "radial":
                    cx, cy, r = cv("cx", 0.5), cv("cy", 0.5), max(1e-6, cv("r", 0.5))
                    t = np.sqrt((xs-cx)**2 + (ys-cy)**2) / r
                else:
                    x1g, y1g, x2g, y2g = cv("x1", 0), cv("y1", 0), cv("x2", 1), cv("y2", 0)
                    gx, gy = x2g-x1g, y2g-y1g; gl = gx*gx + gy*gy
                    t = ((xs-x1g)*gx + (ys-y1g)*gy) / gl if gl > 1e-12 else np.zeros(len(xs))
                t = np.clip(t, 0, 1)
                stops = paint.get("stops") or []
                off = np.array([q[0] for q in stops], dtype=np.float32)
                lumv = np.array([(0.299*q[1][0] + 0.587*q[1][1] + 0.114*q[1][2]) / 255.0 * (q[2] if len(q) > 2 else 1.0) for q in stops], dtype=np.float32)
                arr[ys, xs] *= np.interp(t, off, lumv)
        else:
            arr *= lum(ch) / 255.0
        cur = np.asarray(im, dtype=np.float32) / 255.0
        im = Image.fromarray(np.clip(np.maximum(cur, arr) * 255, 0, 255).astype(np.uint8))
    for ch in list(el):
        draw_node(ch, base)
    units_mask = el.get("maskUnits", "objectBoundingBox")
    if bbox:
        bx, by, bw, bh = bbox
        def rv(k, d):
            v = el.get(k, d)
            if isinstance(v, str) and v.endswith("%"):
                q = float(v[:-1]) / 100.0
                return (bx + bw*q) if k in ("x", "width") else (by + bh*q)
            return float(v)
        if units_mask == "objectBoundingBox":
            rx, ry = rv("x", "-10%"), rv("y", "-10%")
            rw, rh = rv("width", "120%"), rv("height", "120%")
        else:
            rx, ry = rv("x", "0"), rv("y", "0")
            rw, rh = rv("width", str(w)), rv("height", str(h))
        from PIL import ImageChops
        region = Image.new("L", (w, h), 0)
        ImageDraw.Draw(region).rectangle([rx, ry, rx+rw, ry+rh], fill=255)
        im = ImageChops.multiply(im, region)
    p = _mask_png(im); p["luminance"] = True
    p["content_units"] = content_units
    p["mask_units"] = units_mask
    return p


def _rasterize_filtered(root, ch, w, h, base_dir=None):
    try:
        clone = ET.fromstring(ET.tostring(ch, encoding="unicode"))
        fref = clone.attrib.pop("filter", None)
        if not fref:
            return None
        clone.attrib.pop("id", None)
        xml = ET.tostring(clone, encoding="unicode")
        xml = xml.replace("ns0:", "").replace(' xmlns:ns0="http://www.w3.org/2000/svg"', "")
        mini = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %s %s" width="%s" height="%s">%s</svg>'
                % (w, h, w, h, xml))
        d = svg_to_plan(mini, base_dir=base_dir)
        import paint_plan as _pp
        img, _ = _pp.render_plan(d)
        from PIL import Image, ImageFilter
        im = Image.fromarray(img, "RGBA")
        fid = re.search(r"url\(#([^)]+)\)", fref)
        if fid:
            fel = next((e for e in root.iter() if _local(e.tag) == "filter" and e.get("id") == fid.group(1)), None)
            if fel is not None:
                for e in fel.iter():
                    t = _local(e.tag)
                    if t == "feGaussianBlur":
                        try:
                            sd = [float(x) for x in re.split(r"[\s,]+", str(e.get("stdDeviation", "0")).strip()) if x]
                            rad = sum(sd) / max(1, len(sd)) if sd else 0.0
                            if rad > 0:
                                im = im.filter(ImageFilter.GaussianBlur(radius=rad))
                        except Exception:
                            pass
                    elif t == "feOffset":
                        try:
                            dx = float(e.get("dx", 0)); dy = float(e.get("dy", 0))
                            shifted = Image.new("RGBA", im.size, (0, 0, 0, 0))
                            shifted.paste(im, (int(round(dx)), int(round(dy))), im)
                            im = shifted
                        except Exception:
                            pass
                    elif t == "feDropShadow":
                        try:
                            dx = float(e.get("dx", 0)); dy = float(e.get("dy", 0))
                            rad = float(e.get("stdDeviation", 2))
                            a = im.getchannel("A").filter(ImageFilter.GaussianBlur(radius=max(0.0, rad)))
                            shadow = Image.new("RGBA", im.size, (0, 0, 0, 0))
                            shadow.putalpha(a)
                            layer = Image.new("RGBA", im.size, (0, 0, 0, 0))
                            layer.paste(shadow, (int(round(dx)), int(round(dy))), shadow)
                            im = Image.alpha_composite(layer, im)
                        except Exception:
                            pass
        buf = io.BytesIO(); im.save(buf, format="PNG")
        return {"encoding": "png-base64", "data": base64.b64encode(buf.getvalue()).decode("ascii"),
                "width": w, "height": h}
    except Exception:
        return None


def _pattern_tiles(root, w, h, base_dir=None):
    out = {}
    for el in root.iter():
        if _local(el.tag) != "pattern" or not el.get("id"):
            continue
        def _len(v, d):
            try:
                v = str(v)
                return float(v[:-1]) / 100.0 * (w if v.endswith("%") else 1.0) if v.endswith("%") else float(v[:-2] if v.endswith("px") else v)
            except Exception:
                return d
        units = el.get("patternUnits", "objectBoundingBox")
        def _frac(v, d):
            try:
                v = str(v)
                return float(v[:-1]) / 100.0 if v.endswith("%") else float(v[:-2] if v.endswith("px") else v)
            except Exception:
                return d
        if units == "objectBoundingBox":
            fw, fh = _frac(el.get("width", "0.1"), 0.1), _frac(el.get("height", "0.1"), 0.1)
            pw, ph = max(8.0, fw * 256.0), max(8.0, fh * 256.0)
            stored_w, stored_h = fw, fh
        else:
            pw, ph = _len(el.get("width", "32"), 32.0), _len(el.get("height", "32"), 32.0)
            stored_w, stored_h = pw, ph
        if pw <= 0 or ph <= 0:
            continue
        children = "".join(ET.tostring(ch, encoding="unicode") for ch in list(el))
        children = children.replace("ns0:", "").replace(' xmlns:ns0="http://www.w3.org/2000/svg"', "")
        if not children:
            continue
        mini = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %s %s" width="%s" height="%s">%s</svg>'
                % (pw, ph, pw, ph, children))
        try:
            d = svg_to_plan(mini, base_dir=base_dir)
            import paint_plan as _pp
            img, _ = _pp.render_plan(d)
            from PIL import Image
            buf = io.BytesIO()
            Image.fromarray(img, "RGBA").save(buf, format="PNG")
            out[el.get("id")] = {"encoding": "png-base64", "data": base64.b64encode(buf.getvalue()).decode("ascii"),
                                 "width": stored_w, "height": stored_h,
                                 "units": units}
        except Exception:
            continue
    return out


def _gradient_paints(root):
    out = {}
    for el in root.iter():
        tag = _local(el.tag)
        if tag not in ("linearGradient", "radialGradient") or not el.get("id"):
            continue
        stops = []
        for st in list(el):
            if _local(st.tag) != "stop":
                continue
            off = str(st.get("offset", "0")).strip()
            try:
                off = float(off[:-1]) / 100.0 if off.endswith("%") else float(off)
            except Exception:
                off = 0.0
            stops.append([off, _color(st.get("stop-color", "#000"), (0, 0, 0)),
                          max(0.0, min(1.0, float(st.get("stop-opacity", 1.0))))])
        if not stops:
            continue
        p = {"type": "linear" if tag == "linearGradient" else "radial",
             "units": el.get("gradientUnits", "objectBoundingBox"), "stops": stops}
        if tag == "linearGradient":
            for k in ("x1", "y1", "x2", "y2"):
                p[k] = el.get(k, "0%" if k in ("x1", "y1") else "100%")
        else:
            for k in ("cx", "cy", "r", "fx", "fy"):
                p[k] = el.get(k, "50%" if k in ("cx", "cy") else "50%")
        p["source"] = {"gradient": {"tag": tag, "attrs": dict(el.attrib),
                                   "stops": [dict(st.attrib) for st in list(el) if _local(st.tag) == "stop"]}}
        out[el.get("id")] = p
    return out

def _gradient_colors(root):
    out = {}
    for el in root.iter():
        if _local(el.tag) in ("linearGradient", "radialGradient") and el.get("id"):
            cols = []
            for st in list(el):
                if _local(st.tag) == "stop":
                    cols.append(_color(st.get("stop-color", "#000"), (0, 0, 0)))
            if cols:
                out[el.get("id")] = "#%02x%02x%02x" % tuple(int(sum(c[i] for c in cols) / len(cols)) for i in range(3))
    return out


def needs_python_parser(svg_text):
    if "linearGradient" in svg_text or "radialGradient" in svg_text:
        return True
    if any(k in svg_text for k in ("color(", "lab(", "lch(", "oklab(", "oklch(", "icc-color(", "device-cmyk(")):
        return True
    if "<image" in svg_text:
        return True
    if "<style" in svg_text or "<link" in svg_text or re.search(r'href="[^"]+\.svg#', svg_text):
        return True
    if 'fill-rule="evenodd"' in svg_text:
        return True
    if "<mask" in svg_text or "mask=\"url(" in svg_text or "<clipPath" in svg_text or "clip-path=" in svg_text:
        return True
    if "<line" in svg_text or re.search(r"<rect[^>]*\brx\s*=", svg_text):
        return True
    return False

def _svg_to_plan_py(svg_text, base_dir=None, pseudo_mode="ignore", allow_external=False, max_depth=8):
    """Parse bridge SVG (and simple authored SVG) back into a plan dict."""
    root = ET.fromstring(svg_text)
    _load_profiles(root, base_dir)
    vb = root.attrib.get("viewBox", "0 0 1200 900").replace(",", " ").split()
    w, h = (int(float(vb[2])), int(float(vb[3]))) if len(vb) >= 4 else (1200, 900)
    layers, strokes = [], []
    css = _load_css(root, base_dir)
    grads = _gradient_colors(root)
    paints = _gradient_paints(root)
    masks = _mask_elements(root)
    patterns = _pattern_tiles(root, w, h, base_dir)
    clips = _clip_geoms(root)
    by_id = {el.get("id"): el for el in root.iter() if el.get("id")}

    def add_layer(name, blend):
        if name not in [x["name"] for x in layers]:
            layers.append({"name": name, "blend": blend or "normal"})
        return next(x for x in layers if x["name"] == name)

    def set_mask(name, mid):
        if mid in masks:
            add_layer(name, "normal")["_mask_id"] = mid

    def emit(a, name, pts):
        key = None
        for attr in ("clip-path",):
            m = re.match(r"url\(#([^)]+)\)", str(a.get(attr, "")))
            if m:
                key = m.group(1); break
        entry = clips.get(key) if key else None
        polys = entry["polys"] if entry else None
        evenodd = bool(entry and entry.get("evenodd"))
        if entry and entry.get("units") == "objectBoundingBox" and polys and pts:
            xs0 = [float(p[0]) for p in pts]; ys0 = [float(p[1]) for p in pts]
            bx, by = min(xs0), min(ys0); bw = max(1e-9, max(xs0) - bx); bh = max(1e-9, max(ys0) - by)
            polys = [[[bx + q[0] * bw, by + q[1] * bh] for q in poly] for poly in polys]
        if (not polys and str(a.get("fill-rule", "")) == "evenodd" and a.get("d")
                and (a.get("data-preset") == "fill" or (a.get("fill") and a.get("fill") != "none" and not a.get("stroke")))):
            subs = [q for q in _parse_d_subpaths(a.get("d")) if len(q) >= 3]
            if len(subs) > 1:
                def area(q):
                    return abs(sum(q[i][0]*q[(i+1) % len(q)][1] - q[(i+1) % len(q)][0]*q[i][1] for i in range(len(q))))
                def inside(pt, q):
                    c = False
                    for i in range(len(q)):
                        u, v = q[i], q[(i+1) % len(q)]
                        if ((u[1] > pt[1]) != (v[1] > pt[1])) and (pt[0] < (v[0]-u[0])*(pt[1]-u[1])/(v[1]-u[1]) + u[0]):
                            c = not c
                    return c
                subs.sort(key=area, reverse=True)
                outer, holes = subs[0], []
                for q in subs[1:]:
                    if area(q) < area(outer) and inside(q[0], outer):
                        holes.append(q)
                st = _stroke_from_attrs(a, name, [[x, y, 1.0] for x, y in outer])
                st["preset"] = "fill"; st.pop("size", None); st.pop("hardness", None)
                st["holes"] = holes; st["fill_rule"] = "evenodd"; st["_evenodd"] = True
                strokes.append(st); return
        if not polys:
            pm = re.match(r"url\(#([^)]+)\)", str(a.get("fill", "")))
            if pm and pm.group(1) in patterns and not a.get("stroke"):
                st = _stroke_from_attrs(a, name, pts)
                st["preset"] = "fill"; st.pop("size", None); st.pop("hardness", None)
                st["pattern"] = patterns[pm.group(1)]
                strokes.append(st); return
            strokes.append(_stroke_from_attrs(a, name, pts)); return
        if a.get("data-preset") == "fill" or (a.get("fill") and a.get("fill") != "none" and not a.get("stroke")):
            pm = re.match(r"url\(#([^)]+)\)", str(a.get("fill", "")))
            if pm and pm.group(1) in patterns:
                st = _stroke_from_attrs(a, name, pts)
                st["preset"] = "fill"; st.pop("size", None); st.pop("hardness", None)
                st["pattern"] = patterns[pm.group(1)]
                strokes.append(st); return
            if evenodd and _native is not None and _native.available():
                try:
                    out = _native.clip_polygons([[(p[0], p[1]) for p in pts]], [[(q[0], q[1]) for q in poly] for poly in polys], True)
                    out = [q for q in out if len(q) >= 3]
                    if out:
                        def area(q):
                            return abs(sum(q[i][0]*q[(i+1) % len(q)][1] - q[(i+1) % len(q)][0]*q[i][1] for i in range(len(q))))
                        def inside(pt, q):
                            c = False
                            for i in range(len(q)):
                                a, b = q[i], q[(i+1) % len(q)]
                                if ((a[1] > pt[1]) != (b[1] > pt[1])) and (pt[0] < (b[0]-a[0])*(pt[1]-a[1])/(b[1]-a[1]) + a[0]):
                                    c = not c
                            return c
                        out.sort(key=area, reverse=True)
                        groups = []
                        for q in out:
                            owner = None
                            for g in groups:
                                if area(q) < area(g[0]) and inside(q[0], g[0]):
                                    owner = g; break
                            if owner is None:
                                groups.append([q, []])
                            else:
                                owner[1].append(q)
                        for outer, holes in groups:
                            st = _stroke_from_attrs(a, name, [[x, y, 1.0] for x, y in outer])
                            st["preset"] = "fill"; st.pop("size", None); st.pop("hardness", None)
                            st["holes"] = holes; st["fill_rule"] = "evenodd"; st["_evenodd"] = True
                            strokes.append(st)
                    return
                except Exception:
                    pass
            for poly in polys:
                for tri in ([poly] if _is_convex(poly) else (_triangulate(poly) or [poly])):
                    q = _clip_poly([(p[0], p[1]) for p in pts], [(p[0], p[1]) for p in tri])
                    if len(q) >= 3:
                        st = _stroke_from_attrs(a, name, [[x, y, 1.0] for x, y in q])
                        st["preset"] = "fill"; st.pop("size", None); st.pop("hardness", None)
                        strokes.append(st)
            return
        if evenodd:
            for i in range(1, len(pts)):
                for seg in _clip_line_evenodd((pts[i-1][0], pts[i-1][1]), (pts[i][0], pts[i][1]), polys):
                    strokes.append(_stroke_from_attrs(a, name, [[seg[0][0], seg[0][1], pts[i-1][2] if len(pts[i-1]) > 2 else 1.0],
                                                                [seg[1][0], seg[1][1], pts[i][2] if len(pts[i]) > 2 else 1.0]]))
            return
        for poly in polys:
            for tri in (_triangulate(poly) or [poly]):
                for i in range(1, len(pts)):
                    seg = _clip_line((pts[i-1][0], pts[i-1][1]), (pts[i][0], pts[i][1]), [(p[0], p[1]) for p in tri])
                    if seg and math.hypot(seg[1][0]-seg[0][0], seg[1][1]-seg[0][1]) > 1e-6:
                        strokes.append(_stroke_from_attrs(a, name, [[seg[0][0], seg[0][1], pts[i-1][2] if len(pts[i-1]) > 2 else 1.0],
                                                                    [seg[1][0], seg[1][1], pts[i][2] if len(pts[i]) > 2 else 1.0]]))

    canvas_w, canvas_h = w, h
    def visit(ch, inherited_layer=None, parent=(1, 0, 0, 1, 0, 0), active=None, inh=None):
        active = active or set()
        inh = inh or {}
        tag = _local(ch.tag)
        a = _resolved_attrs(ch, css, grads, paints, inh, pseudo_mode)
        if tag == "use":
            href = a.get("{http://www.w3.org/1999/xlink}href") or a.get("href") or ""
            if href.startswith("#"):
                key = href[1:]
                if key not in active and key in by_id:
                    tgt = by_id[key]
                    m = _mat_mul(parent, _parse_transform(a.get("transform")))
                    m = _mat_mul(m, _mat(1, 0, 0, 1, float(a.get("x", 0) or 0), float(a.get("y", 0) or 0)))
                    visit(tgt, inherited_layer, m, active | {key}, inh)
                return
            if href:
                if "://" in href:
                    raise ValueError("external network use is not allowed: %s" % href)
                if len(active) >= max_depth:
                    raise ValueError("external use depth exceeds %d" % max_depth)
                if base_dir is None:
                    raise ValueError("external use requires base_dir: %s" % href)
                if "#" not in href:
                    raise ValueError("external use needs #id: %s" % href)
                rel, ident = href.split("#", 1)
                path = (Path(base_dir) / rel).resolve()
                try:
                    path.relative_to(Path(base_dir).resolve())
                except Exception:
                    raise ValueError("use escapes base_dir: %s" % href)
                key = (str(path), ident)
                if key in active:
                    return
                ext = ET.fromstring(path.read_text(encoding="utf-8"))
                target = next((e for e in ext.iter() if e.get("id") == ident), None)
                if target is not None:
                    m = _mat_mul(parent, _parse_transform(a.get("transform")))
                    m = _mat_mul(m, _mat(1, 0, 0, 1, float(a.get("x", 0) or 0), float(a.get("y", 0) or 0)))
                    visit(target, inherited_layer, m, active | {key}, inh)
            return
        if tag == "g":
            name = a.get("data-name") or a.get("id") or inherited_layer or "layer"
            blend = a.get("data-blend") or "normal"
            add_layer(name, blend)
            mm = re.match(r"url\(#([^)]+)\)", str(a.get("mask", "")))
            if mm:
                set_mask(name, mm.group(1))
            m = _mat_mul(parent, _parse_transform(a.get("transform")))
            my = dict(inh)
            for k in ("fill", "stroke", "stroke-width", "opacity", "fill-opacity"):
                if a.get(k):
                    my[k] = a[k]
            if a.get("data-stroke") or (a.get("data-center") and not a.get("data-name")):
                ln = a.get("data-layer") or inherited_layer or "layer"
                add_layer(ln, "normal")
                pts = _center_pts(a.get("data-center"))
                if pts:
                    pts = [[*_mat_apply(m, p[0], p[1]), p[2] if len(p) > 2 else 1.0] for p in pts]
                    emit(a, ln, pts)
                return
            for sub in list(ch):
                visit(sub, name, m, active, my)
            return
        fref = a.get("filter")
        if fref:
            im = _rasterize_filtered(root, ch, canvas_w, canvas_h, base_dir)
            if im:
                name = a.get("data-layer") or inherited_layer or "layer"
                add_layer(name, "normal")
                strokes.append({"layer": name, "preset": "image", "image": im,
                                "x": 0, "y": 0, "width": canvas_w, "height": canvas_h, "opacity": 1.0})
                return
        if tag in ("text", "tspan"):
            def _num_attr(k, d=0.0):
                v = a.get(k)
                if v is None:
                    return d
                v = str(v).split()[0] if str(v).split() else str(v)
                try:
                    return float(v[:-2]) if v.endswith("px") else float(v)
                except Exception:
                    return d
            txt = "".join(ch.itertext()) if tag == "text" else (ch.text or "")
            if not txt:
                return
            name = a.get("data-layer") or inherited_layer or "layer"
            add_layer(name, "normal")
            x, y = _num_attr("x", 0.0), _num_attr("y", 0.0)
            fs = _num_attr("font-size", 16.0)
            fill = a.get("fill") or a.get("data-color") or "#000"
            if fill == "none":
                return
            fam = a.get("font-family") or "Arial"
            fam = fam.split(",")[0].strip().strip("'\"")
            anchor = a.get("text-anchor") or "start"
            op = max(0.0, min(1.0, float(a.get("opacity", a.get("fill-opacity", 1.0)) or 1.0)))
            m = _mat_mul(parent, _parse_transform(a.get("transform")))
            st = {"layer": name, "preset": "text", "text": txt,
                  "x": x, "y": y, "font_family": fam, "font_size": fs,
                  "color": _color(fill, (0, 0, 0)), "opacity": op,
                  "text_anchor": anchor}
            if a.get("font-weight"):
                st["font_weight"] = a.get("font-weight")
            runs = []
            if (ch.text or "").strip():
                runs.append({"text": ch.text, "color": _color(fill, (0, 0, 0)),
                             "font_size": fs, "font_family": fam})
            for child in list(ch):
                if _local(child.tag) != "tspan":
                    continue
                rt = child.text or ""
                if not rt:
                    continue
                rfill = child.get("fill") or fill
                try:
                    rfs = float(str(child.get("font-size", fs)).replace("px", ""))
                except Exception:
                    rfs = fs
                runs.append({"text": rt,
                             "color": _color(rfill, (0, 0, 0)),
                             "font_size": rfs,
                             "font_family": (child.get("font-family") or fam).split(",")[0].strip().strip("'\"")})
            if runs:
                st["runs"] = runs
            if not (abs(m[0]-1) < 1e-9 and abs(m[1]) < 1e-9 and abs(m[2]) < 1e-9
                    and abs(m[3]-1) < 1e-9 and abs(m[4]) < 1e-9 and abs(m[5]) < 1e-9):
                st["transform"] = [float(v) for v in m]
            strokes.append(st)
            return
        if tag == "image":
            href = a.get("href") or a.get("{http://www.w3.org/1999/xlink}href") or ""
            if not href:
                return
            mime = "image/png"
            if href.startswith("data:"):
                head, _, payload = href.partition(",")
                if ";base64" not in head:
                    return
                data = base64.b64decode(payload)
                mime = head[5:].split(";")[0] or "image/png"
            else:
                if base_dir is None:
                    raise ValueError("image href requires base_dir: %s" % href)
                path = (Path(base_dir) / href).resolve()
                try:
                    path.relative_to(Path(base_dir).resolve())
                except Exception:
                    raise ValueError("image escapes base_dir: %s" % href)
                data = path.read_bytes()
                mime = "image/jpeg" if path.suffix.lower() in (".jpg", ".jpeg") else "image/png"
            from PIL import Image
            try:
                im = Image.open(io.BytesIO(data))
                iw, ih = im.size
            except Exception:
                iw = ih = 0
            x = float(a.get("x", 0) or 0); y = float(a.get("y", 0) or 0)
            w = float(a.get("width", iw) or iw); h = float(a.get("height", ih) or ih)
            m = _mat_mul(parent, _parse_transform(a.get("transform")))
            ident = (abs(m[0]-1) < 1e-9 and abs(m[1]) < 1e-9 and abs(m[2]) < 1e-9
                     and abs(m[3]-1) < 1e-9 and abs(m[4]) < 1e-9 and abs(m[5]) < 1e-9)
            corners = [_mat_apply(m, x, y), _mat_apply(m, x + w, y),
                       _mat_apply(m, x, y + h), _mat_apply(m, x + w, y + h)]
            xs = [q[0] for q in corners]; ys = [q[1] for q in corners]
            name = a.get("data-layer") or inherited_layer or "layer"
            add_layer(name, "normal")
            op = max(0.0, min(1.0, float(a.get("opacity", 1.0) or 1.0)))
            st = {"layer": name, "preset": "image",
                  "image": {"encoding": "png-base64", "mime": mime,
                            "data": base64.b64encode(data).decode("ascii"),
                            "width": iw, "height": ih},
                  "x": x, "y": y, "width": w, "height": h, "opacity": op}
            if not ident:
                st["transform"] = [float(v) for v in m]
            strokes.append(st)
            return
        if tag == "path":
            name = a.get("data-layer") or inherited_layer or "layer"
            mm = re.match(r"url\(#([^)]+)\)", str(a.get("mask", "")))
            if mm and mm.group(1) in masks:
                name = name + "__masked%d" % len(layers)
            add_layer(name, "normal")
            if mm:
                set_mask(name, mm.group(1))
            center = a.get("data-center")
            dval = a.get("d", "")
            if center and a.get("data-d-hash") and _d_hash(dval) != a.get("data-d-hash"):
                center = ""
            pts = _center_pts(center)
            m = _mat_mul(parent, _parse_transform(a.get("transform")))
            if not pts and str(a.get("fill-rule", "")) == "evenodd" and (
                    a.get("data-preset") == "fill" or (a.get("fill") and a.get("fill") != "none" and not a.get("stroke"))):
                groups = [g for g in _parse_d_subpaths(dval) if len(g) >= 3]
                if len(groups) > 1:
                    first = [[*_mat_apply(m, p[0], p[1]), 1.0] for p in groups[0]]
                    emit(a, name, first)
                    return
            if not pts:
                for group in _parse_d_subpaths(dval):
                    if not group:
                        continue
                    sub = [[x, y, 1.0] for x, y in group]
                    pv = _floats(a.get("data-p"), [1.0] * len(sub))
                    if len(pv) == len(sub):
                        sub = [[p[0], p[1], pv[k]] for k, p in enumerate(sub)]
                    sub = [[*_mat_apply(m, p[0], p[1]), p[2] if len(p) > 2 else 1.0] for p in sub]
                    emit(a, name, sub)
                return
            pts = [[*_mat_apply(m, p[0], p[1]), p[2] if len(p) > 2 else 1.0] for p in pts]
            emit(a, name, pts)
            return
        if tag in ("circle", "ellipse", "rect", "polygon", "polyline", "line"):
            name = a.get("data-layer") or inherited_layer or "layer"
            mm = re.match(r"url\(#([^)]+)\)", str(a.get("mask", "")))
            if mm and mm.group(1) in masks:
                name = name + "__masked%d" % len(layers)
            add_layer(name, "normal")
            if mm:
                set_mask(name, mm.group(1))
            pts = _shape_points(tag, a)
            if pts:
                m = _mat_mul(parent, _parse_transform(a.get("transform")))
                pts = [[*_mat_apply(m, p[0], p[1]), 1.0] for p in pts]
                aa = dict(a)
                if aa.get("fill") and aa.get("fill") != "none" and not aa.get("stroke"):
                    aa["data-preset"] = "fill"
                else:
                    aa["data-size"] = aa.get("data-size", aa.get("stroke-width", 1.0))
                emit(aa, name, pts)

    for ch in list(root):
        visit(ch)
    for st in strokes:
        if st.pop("_evenodd", False):
            outer = [(float(p[0]), float(p[1])) for p in (st.get("points") or [])]
            holes = [[(float(p[0]), float(p[1])) for p in h] for h in (st.get("holes") or [])]
            if len(outer) >= 3:
                from PIL import Image, ImageDraw
                im = Image.new("L", (w, h), 0)
                ImageDraw.Draw(im).polygon(outer, fill=255)
                for hole in holes:
                    if len(hole) >= 3:
                        ImageDraw.Draw(im).polygon(hole, fill=0)
                st["mask"] = _mask_png(im)
    by = {}
    for st in strokes:
        by.setdefault(st["layer"], []).append(st.get("points") or [])
    for layer in layers:
        mid = layer.pop("_mask_id", None)
        if not mid or mid not in masks:
            continue
        pts = [p for group in by.get(layer["name"], []) for p in group]
        if pts:
            xs = [float(p[0]) for p in pts]; ys = [float(p[1]) for p in pts]
            bbox = (min(xs), min(ys), max(1.0, max(xs)-min(xs)), max(1.0, max(ys)-min(ys)))
        else:
            bbox = (0, 0, w, h)
        el = masks[mid]
        units = el.get("maskContentUnits", "userSpaceOnUse")
        mk = _raster_mask_element(el, w, h, bbox, units, root)
        try:
            mk["svg"] = "".join(ET.tostring(ch, encoding="unicode") for ch in list(el))
            mk["mask_units"] = el.get("maskUnits", "objectBoundingBox")
            mk["content_units"] = el.get("maskContentUnits", "userSpaceOnUse")
            mk["attrs"] = dict(el.attrib)
        except Exception:
            pass
        layer["mask"] = mk
    return {"canvas": [w, h], "layers": layers, "strokes": strokes}


def svg_to_plan(svg_text, base_dir=None, pseudo_mode="ignore", allow_external=False, max_depth=8):
    if _native is not None and _native.available() and not needs_python_parser(svg_text) and base_dir is None:
        return _native.svg_to_plan(svg_text)
    return _svg_to_plan_py(svg_text, base_dir=base_dir, pseudo_mode=pseudo_mode,
                           allow_external=allow_external, max_depth=max_depth)


def load_plan_svg(path):
    p = Path(path)
    return svg_to_plan(p.read_text(encoding="utf-8"), base_dir=p.parent)


def dump_plan_svg(plan_path, svg_path, *, filters=True):
    doc, w, h = plan_io.load_plan(plan_path)
    t0 = time.perf_counter()
    svg = plan_to_svg(doc, filters=filters, title=Path(plan_path).stem)
    Path(svg_path).parent.mkdir(parents=True, exist_ok=True)
    Path(svg_path).write_text(svg, encoding="utf-8")
    dt = time.perf_counter() - t0
    return {"plan": str(plan_path), "svg": str(svg_path), "strokes": len(doc.get("strokes", [])),
            "layers": len(doc.get("layers", [])), "svg_bytes": len(svg.encode("utf-8")),
            "svg_s": round(dt, 4)}


def load_svg_plan(svg_path, plan_path):
    t0 = time.perf_counter()
    doc = load_plan_svg(svg_path)
    dt = time.perf_counter() - t0
    Path(plan_path).parent.mkdir(parents=True, exist_ok=True)
    out = {"canvas": [int(doc["canvas"][0]), int(doc["canvas"][1])],
           "layers": doc["layers"], "strokes": doc["strokes"],
           "count": len(doc["strokes"])}
    Path(plan_path).write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")
    return {"svg": str(svg_path), "plan": str(plan_path), "strokes": len(doc["strokes"]),
            "layers": len(doc["layers"]), "parse_s": round(dt, 4),
            "total_s": round(time.perf_counter() - t0, 4)}
