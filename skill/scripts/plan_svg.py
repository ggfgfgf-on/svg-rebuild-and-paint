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

import hashlib
import json
import math
import re
import sys
import time
from pathlib import Path
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape, quoteattr

sys.path.insert(0, str(Path(__file__).resolve().parent))
import plan_io

try:
    import plan_svg_native as _native
except Exception:
    _native = None


def _esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _f(v):
    return ("%.3f" % float(v)).rstrip("0").rstrip(".")


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


def plan_to_svg(doc, *, filters=False, title=None):
    """Return an SVG string. One <path> per stroke, one <g> per layer."""
    if _native is not None and _native.available() and not filters:
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

    head = ['<?xml version="1.0" encoding="UTF-8"?>',
            '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d" '
            'shape-rendering="geometricPrecision">' % (w, h, w, h)]
    if title:
        head.append("<title>%s</title>" % escape(str(title)))
    if fids:
        head.append("<defs>%s</defs>" % _filter_defs(fids))
    out = ["".join(head)]
    for li, name in enumerate(order):
        blend = blends.get(name, "normal")
        style = "isolation:isolate;mix-blend-mode:%s" % blend if blend != "normal" else "isolation:isolate"
        out.append('<g id=%s data-name=%s data-blend=%s style=%s>'
                   % (quoteattr("L%d" % li), quoteattr(name), quoteattr(blend), quoteattr(style)))
        for s in by_layer.get(name, []):
            pts = s.get("points") or []
            if not pts:
                continue
            size = float(s.get("size", 8.0))
            op = max(0.0, min(1.0, float(s.get("opacity", 1.0))))
            hard = max(0.0, min(1.0, float(s.get("hardness", 0.7))))
            preset = str(s.get("preset", "round_soft"))
            if preset == "fill":
                out.append('<path d="%s Z" fill="%s" fill-opacity="%s" fill-rule="nonzero" data-layer="%s" data-preset="fill" data-color="%s"%s/>' %
                           (_path_d(pts), _rgb(s.get("color")), _f(op), _esc(name), _rgb_text(s.get("color")),
                            (' data-tag="%s"' % _esc(s["tag"])) if s.get("tag") else ""))
                continue
            cap = "butt" if preset in ("flat", "bristle") else "round"
            attrs = ['d="%s"' % _path_d(pts),
                     'fill="none"',
                     'stroke="%s"' % _rgb(s.get("color")),
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
    if not s:
        return list(default)
    if s.startswith("#"):
        h = s[1:]
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        return [int(h[i:i+2], 16) for i in (0, 2, 4)]
    return [float(x) for x in s.replace(";", ",").split(",")[:3]]



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
        body.append('<g data-name=%s data-blend=%s>' % (quoteattr(name), quoteattr(blends[name])))
        for st in by.get(name, []):
            pts = st.get("points") or []
            if not pts:
                continue
            size = float(st.get("size", 8.0)); base = size * 0.5
            col = _rgb(st.get("color")); op0 = max(0.0, min(1.0, float(st.get("opacity", 1.0))))
            preset = str(st.get("preset", "round_soft")); hard = float(st.get("hardness", 0.7))
            ps = [(float(p[0]), float(p[1]), float(p[2] if len(p) > 2 else 1.0)) for p in pts]
            body.append('<g data-stroke="1" data-layer=%s data-preset=%s data-size="%s" data-opacity="%s" data-hardness="%s" data-color="%s" data-center="%s" data-center-hash="%s"%s>' % (
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
                    op = max(0.0, min(1.0, op0 * pa)); gid = grad_id(col, hard)
                    fill = ("url(#%s)" % gid) if gid else col
                    if preset in ("flat", "bristle"):
                        body.append('<ellipse cx="%s" cy="%s" rx="%s" ry="%s" fill="%s" fill-opacity="%s" transform="rotate(%s %s %s)"/>' % (_f(x), _f(y), _f(r), _f(max(0.8, r * 0.35)), fill, _f(op), _f(ang), _f(x), _f(y)))
                    else:
                        body.append('<circle cx="%s" cy="%s" r="%s" fill="%s" fill-opacity="%s"/>' % (_f(x), _f(y), _f(r), fill, _f(op)))
            body.append("</g>")
        body.append("</g>")
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d" data-bridge="plan_svg_stamps" shape-rendering="geometricPrecision">' % (w, h, w, h)]
    if title:
        out.append("<title>%s</title>" % escape(str(title)))
    if defs:
        out.append("<defs>%s</defs>" % "\n".join(defs))
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
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d" data-bridge="plan_svg_outline" shape-rendering="geometricPrecision">' % (w, h, w, h)]
    if title:
        out.append("<title>%s</title>" % escape(str(title)))
    for name in order:
        out.append('<g data-name=%s data-blend=%s%s>' % (quoteattr(name), quoteattr(blends[name]), ' filter="url(#soft)"' if soft else ""))
        for st in by.get(name, []):
            pts = st.get("points") or []
            if not pts:
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
                    parts.append("M " + " L ".join("%s %s" % (_f(x), _f(y)) for x, y in seg) + " Z")
                d = " ".join(parts)
            out.append('<path d="%s" fill="%s" fill-opacity="%s" fill-rule="nonzero" data-layer=%s data-preset=%s data-size="%s" data-opacity="%s" data-hardness="%s" data-color="%s" data-center="%s" data-d-hash="%s"%s/>' %
                       (d, _rgb(st.get("color")), _f(st.get("opacity", 1.0)), quoteattr(name), quoteattr(str(st.get("preset", "round_soft"))),
                        _f(size), _f(st.get("opacity", 1.0)), _f(st.get("hardness", 0.7)), _rgb(st.get("color")), _center_attr(ps), _d_hash(d),
                        (' data-tag=' + quoteattr(str(st["tag"]))) if st.get("tag") else ""))
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


def _clip_geoms(root):
    out = {}
    for el in root.iter():
        if _local(el.tag) in ("clipPath", "mask") and el.get("id"):
            polys = []
            for ch in list(el):
                tag = _local(ch.tag)
                if tag == "path":
                    for sub in _parse_d_subpaths(ch.get("d", "")):
                        if len(sub) >= 3: polys.append(sub)
                elif tag in ("rect", "circle", "ellipse", "polygon"):
                    q = _shape_points(tag, ch.attrib)
                    if len(q) >= 3: polys.append(q)
            if polys:
                out[el.get("id")] = polys
    return out

def _load_css(root):
    css = {}
    for el in root.iter():
        if _local(el.tag) == "style":
            for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", el.text or ""):
                d = _style_decls(body)
                for one in sel.split(","):
                    one = one.strip()
                    if one:
                        css.setdefault(one, {}).update(d)
    return css


def _selector_match(sel, ch):
    if sel.endswith("*"):
        return True
    if sel.startswith("."):
        return sel[1:] in (ch.get("class") or "").split()
    if sel.startswith("#"):
        return ch.get("id") == sel[1:]
    return _local(ch.tag) == sel if hasattr(ch, "tag") else False


def _resolved_attrs(ch, css, grads):
    a = dict(ch.attrib)
    for sel, d in css.items():
        if _selector_match(sel, a):
            for k, v in d.items():
                a.setdefault(k, v)
    for k, v in _style_decls(a.get("style", "")).items():
        a[k] = v
    for key in ("fill", "stroke"):
        m = re.match(r"url\(#([^)]+)\)", str(a.get(key, "")))
        if m and m.group(1) in grads:
            a[key] = grads[m.group(1)]
    return a


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
    if "data-center=" in svg_text or "data-stroke=" in svg_text:
        return True
    if "transform=" in svg_text or "<style" in svg_text or "<use" in svg_text:
        return True
    for dm in re.finditer(r'd="([^"]*)"', svg_text):
        if re.search(r"[Aa]", dm.group(1)) or len(re.findall(r"[Mm]", dm.group(1))) > 1:
            return True
    if re.search(r"<(circle|ellipse|rect|polygon|polyline)\b", svg_text):
        return True
    return "url(#" in svg_text or "<mask" in svg_text or "<clipPath" in svg_text


def _svg_to_plan_py(svg_text):
    """Parse bridge SVG (and simple authored SVG) back into a plan dict."""
    root = ET.fromstring(svg_text)
    vb = root.attrib.get("viewBox", "0 0 1200 900").replace(",", " ").split()
    w, h = (int(float(vb[2])), int(float(vb[3]))) if len(vb) >= 4 else (1200, 900)
    layers, strokes = [], []
    css = _load_css(root)
    grads = _gradient_colors(root)
    clips = _clip_geoms(root)
    by_id = {el.get("id"): el for el in root.iter() if el.get("id")}

    def add_layer(name, blend):
        if name not in [x["name"] for x in layers]:
            layers.append({"name": name, "blend": blend or "normal"})

    def emit(a, name, pts):
        key = None
        for attr in ("clip-path", "mask"):
            m = re.match(r"url\(#([^)]+)\)", str(a.get(attr, "")))
            if m:
                key = m.group(1); break
        polys = clips.get(key) if key else None
        if not polys:
            strokes.append(_stroke_from_attrs(a, name, pts)); return
        if a.get("data-preset") == "fill" or (a.get("fill") and a.get("fill") != "none" and not a.get("stroke")):
            for poly in polys:
                q = _clip_poly([(p[0], p[1]) for p in pts], [(p[0], p[1]) for p in poly])
                if len(q) >= 3:
                    st = _stroke_from_attrs(a, name, [[x, y, 1.0] for x, y in q])
                    st["preset"] = "fill"; st.pop("size", None); st.pop("hardness", None)
                    strokes.append(st)
            return
        for poly in polys:
            for i in range(1, len(pts)):
                seg = _clip_line((pts[i-1][0], pts[i-1][1]), (pts[i][0], pts[i][1]), [(p[0], p[1]) for p in poly])
                if seg:
                    strokes.append(_stroke_from_attrs(a, name, [[seg[0][0], seg[0][1], pts[i-1][2] if len(pts[i-1]) > 2 else 1.0],
                                                                [seg[1][0], seg[1][1], pts[i][2] if len(pts[i]) > 2 else 1.0]]))

    def visit(ch, inherited_layer=None, parent=(1, 0, 0, 1, 0, 0), active=None):
        active = active or set()
        tag = _local(ch.tag)
        a = _resolved_attrs(ch, css, grads)
        if tag == "use":
            href = a.get("{http://www.w3.org/1999/xlink}href") or a.get("href") or ""
            if href.startswith("#") and href[1:] not in active and href[1:] in by_id:
                tgt = by_id[href[1:]]
                m = _mat_mul(parent, _parse_transform(a.get("transform")))
                m = _mat_mul(m, _mat(1, 0, 0, 1, float(a.get("x", 0) or 0), float(a.get("y", 0) or 0)))
                visit(tgt, inherited_layer, m, active | {href[1:]})
            return
        if tag == "g":
            name = a.get("data-name") or a.get("id") or inherited_layer or "layer"
            blend = a.get("data-blend") or "normal"
            add_layer(name, blend)
            m = _mat_mul(parent, _parse_transform(a.get("transform")))
            if a.get("data-stroke") or (a.get("data-center") and not a.get("data-name")):
                ln = a.get("data-layer") or inherited_layer or "layer"
                add_layer(ln, "normal")
                pts = _center_pts(a.get("data-center"))
                if pts:
                    pts = [[*_mat_apply(m, p[0], p[1]), p[2] if len(p) > 2 else 1.0] for p in pts]
                    emit(a, ln, pts)
                return
            for sub in list(ch):
                visit(sub, name, m, active)
            return
        if tag == "path":
            name = a.get("data-layer") or inherited_layer or "layer"
            add_layer(name, "normal")
            center = a.get("data-center")
            dval = a.get("d", "")
            if center and a.get("data-d-hash") and _d_hash(dval) != a.get("data-d-hash"):
                center = ""
            pts = _center_pts(center)
            m = _mat_mul(parent, _parse_transform(a.get("transform")))
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
        if tag in ("circle", "ellipse", "rect", "polygon", "polyline"):
            name = a.get("data-layer") or inherited_layer or "layer"
            add_layer(name, "normal")
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
    return {"canvas": [w, h], "layers": layers, "strokes": strokes}


def svg_to_plan(svg_text):
    if _native is not None and _native.available() and not needs_python_parser(svg_text):
        return _native.svg_to_plan(svg_text)
    return _svg_to_plan_py(svg_text)


def load_plan_svg(path):
    return svg_to_plan(Path(path).read_text(encoding="utf-8"))


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
