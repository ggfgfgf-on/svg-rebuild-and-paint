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


def plan_to_svg(doc, *, filters=True, title=None):
    """Return an SVG string. One <path> per stroke, one <g> per layer."""
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
    return re.findall(r"[MmLlHhVvCcSsQqTtZz]|[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?", d)


def _parse_d(d):
    """Sample a path into [[x,y],...]; supports M/L/H/V/C/S/Q/T/Z, not A."""
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
            while i < len(toks) and toks[i] not in "MmLlHhVvCcSsQqTtZz":
                vals.append(float(toks[i])); i += 1
            if len(vals) < n:
                raise ValueError("bad path: expected %d numbers" % n)
        return vals

    def add(p):
        if not pts or abs(pts[-1][0] - p[0]) > 1e-9 or abs(pts[-1][1] - p[1]) > 1e-9:
            pts.append([float(p[0]), float(p[1])])

    while i < len(toks):
        if toks[i] in "MmLlHhVvCcSsQqTtZz":
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


def svg_to_plan(svg_text):
    """Parse bridge SVG (and simple authored SVG) back into a plan dict."""
    root = ET.fromstring(svg_text)
    vb = root.attrib.get("viewBox", "0 0 1200 900").replace(",", " ").split()
    w, h = (int(float(vb[2])), int(float(vb[3]))) if len(vb) >= 4 else (1200, 900)
    layers = []
    strokes = []

    def add_layer(name, blend):
        if name not in [x["name"] for x in layers]:
            layers.append({"name": name, "blend": blend or "normal"})

    def walk(node, inherited_layer=None):
        for ch in list(node):
            tag = _local(ch.tag)
            if tag == "g":
                name = ch.attrib.get("data-name") or ch.attrib.get("id") or inherited_layer or "layer"
                blend = ch.attrib.get("data-blend") or "normal"
                add_layer(name, blend)
                walk(ch, name)
            elif tag == "path":
                name = ch.attrib.get("data-layer") or inherited_layer or "layer"
                add_layer(name, "normal")
                try:
                    pts = _parse_d(ch.attrib.get("d", ""))
                except Exception:
                    continue
                pressure = _floats(ch.attrib.get("data-p"), [1.0] * len(pts))
                if len(pressure) != len(pts):
                    pressure = [1.0] * len(pts)
                size = float(ch.attrib.get("data-size", ch.attrib.get("stroke-width", 8.0)))
                op = float(ch.attrib.get("data-opacity", ch.attrib.get("stroke-opacity", 1.0)))
                hard = float(ch.attrib.get("data-hardness", 0.7))
                preset = ch.attrib.get("data-preset", "round_soft")
                color = _color(ch.attrib.get("data-color") or ch.attrib.get("stroke"), (217, 180, 90))
                st = {"layer": name, "preset": preset, "size": float(size),
                      "opacity": max(0.0, min(1.0, op)), "color": color,
                      "hardness": max(0.0, min(1.0, hard)),
                      "points": [[float(x), float(y), float(pressure[k] if k < len(pressure) else 1.0)]
                                 for k, (x, y) in enumerate(pts)]}
                if ch.attrib.get("data-tag"):
                    st["tag"] = str(ch.attrib["data-tag"])
                strokes.append(st)
            else:
                walk(ch, inherited_layer)

    walk(root)
    return {"canvas": [w, h], "layers": layers, "strokes": strokes}


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
