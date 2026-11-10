"""Incremental SVG tokenizer + basic streaming SVG -> plan builder.

This is a standalone streaming path. It intentionally does not build a DOM and
does not implement CSS/defs/use/pattern/filter; those still use plan_svg's DOM
parser. It covers groups and basic shape/path elements for large files.
"""
from __future__ import annotations
import html
import re
from pathlib import Path

import plan_svg as _ps

_TAG = re.compile(r"([A-Za-z_][\w:.-]*)")
_ATTR = re.compile(r"""([A-Za-z_][\w:.-]*)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))""")


class SVGTokenizer:
    def __init__(self):
        self.buf = ""

    def feed(self, chunk):
        self.buf += chunk
        while self.buf:
            lt = self.buf.find("<")
            if lt < 0:
                text = html.unescape(self.buf)
                self.buf = ""
                if text:
                    yield ("text", text)
                return
            if lt > 0:
                text = html.unescape(self.buf[:lt])
                self.buf = self.buf[lt:]
                if text:
                    yield ("text", text)
            if self.buf.startswith("<!--"):
                end = self.buf.find("-->")
                if end < 0:
                    return
                self.buf = self.buf[end + 3:]
                continue
            if self.buf.startswith("<![CDATA["):
                end = self.buf.find("]]>")
                if end < 0:
                    return
                yield ("text", self.buf[9:end])
                self.buf = self.buf[end + 3:]
                continue
            if self.buf.startswith("<?"):
                end = self.buf.find("?>")
                if end < 0:
                    return
                self.buf = self.buf[end + 2:]
                continue
            if self.buf.startswith("<!"):
                end = self._tag_end(self.buf, 2)
                if end < 0:
                    return
                self.buf = self.buf[end + 1:]
                continue
            end = self._tag_end(self.buf, 1)
            if end < 0:
                return
            raw = self.buf[1:end]
            self.buf = self.buf[end + 1:]
            if raw.startswith("/"):
                m = _TAG.match(raw[1:].strip())
                if m:
                    yield ("end", m.group(1))
                continue
            selfclose = raw.rstrip().endswith("/")
            if selfclose:
                raw = raw.rstrip()[:-1]
            m = _TAG.match(raw)
            if not m:
                continue
            tag = m.group(1)
            attrs = {}
            for am in _ATTR.finditer(raw[m.end():]):
                val = am.group(2) if am.group(2) is not None else (am.group(3) if am.group(3) is not None else am.group(4))
                attrs[am.group(1)] = html.unescape(val or "")
            yield ("start", tag, attrs)
            if selfclose:
                yield ("end", tag)

    @staticmethod
    def _tag_end(s, start):
        quote = None
        for i in range(start, len(s)):
            c = s[i]
            if quote:
                if c == quote:
                    quote = None
            elif c in "\"'":
                quote = c
            elif c == ">":
                return i
        return -1

    def close(self):
        if self.buf.strip():
            text = html.unescape(self.buf)
            self.buf = ""
            yield ("text", text)


def _length(attrs, key, default=0.0):
    try:
        v = str(attrs.get(key, default)).strip()
        return float(v[:-2]) if v.endswith("px") else float(v)
    except Exception:
        return float(default)


def _canvas(attrs):
    vb = str(attrs.get("viewBox", "")).replace(",", " ").split()
    if len(vb) >= 4:
        try:
            return int(float(vb[2])), int(float(vb[3]))
        except Exception:
            pass
    return int(_length(attrs, "width", 1200)), int(_length(attrs, "height", 900))


def stream_svg_to_plan(source, chunk_size=65536):
    """Parse a basic SVG from a path or text using the streaming tokenizer."""
    if hasattr(source, "read"):
        chunks = iter(lambda: source.read(chunk_size), "")
    elif isinstance(source, (str, Path)):
        text = str(source)
        if "<" not in text and Path(text).exists():
            f = open(text, "r", encoding="utf-8")
            chunks = iter(lambda: f.read(chunk_size), "")
        else:
            chunks = iter([text])
    else:
        chunks = source
    tok = SVGTokenizer()
    canvas = [1200, 900]
    layers = []
    strokes = []
    layer_stack = []
    skip_tag = None
    order = []

    def add_layer(name, blend="normal"):
        if name not in order:
            order.append(name)
            layers.append({"name": name, "blend": blend or "normal"})
        return name

    def emit_shape(tag, a):
        layer = layer_stack[-1] if layer_stack else "layer"
        add_layer(layer)
        fill = a.get("fill", "")
        stroke = a.get("stroke", "")
        data_preset = a.get("data-preset", "")
        if data_preset == "fill" or (fill and fill != "none" and not stroke):
            pts = _ps._shape_points(tag, a) if tag != "path" else None
            if tag == "path":
                subs = [q for q in _ps._parse_d_subpaths(a.get("d", "")) if len(q) >= 3]
            else:
                subs = [pts] if pts and len(pts) >= 3 else []
            for q in subs:
                if not q:
                    continue
                st = {"layer": layer, "preset": "fill",
                      "opacity": float(a.get("fill-opacity", a.get("opacity", 1.0)) or 1.0),
                      "color": _ps._color(fill, (0, 0, 0)),
                      "points": [[float(p[0]), float(p[1]), 1.0] for p in q],
                      "fill_rule": a.get("fill-rule", "nonzero")}
                strokes.append(st)
            return
        if tag == "path":
            subs = _ps._parse_d_subpaths(a.get("d", ""))
        else:
            subs = [_ps._shape_points(tag, a)] if _ps._shape_points(tag, a) else []
        for q in subs:
            if len(q) < 2:
                continue
            st = {"layer": layer, "preset": data_preset or ("flat" if tag == "line" else "round_soft"),
                  "size": float(a.get("stroke-width", a.get("data-size", 1.0)) or 1.0),
                  "opacity": float(a.get("stroke-opacity", a.get("opacity", 1.0)) or 1.0),
                  "hardness": float(a.get("data-hardness", 0.7) or 0.7),
                  "color": _ps._color(stroke or fill, (0, 0, 0)),
                  "points": [[float(p[0]), float(p[1]), 1.0] for p in q]}
            if a.get("data-tag"):
                st["tag"] = a["data-tag"]
            strokes.append(st)

    for chunk in chunks:
        for ev in tok.feed(chunk):
            kind = ev[0]
            if kind == "start":
                _, tag, a = ev
                if skip_tag is not None:
                    continue
                if tag == "svg":
                    canvas = list(_canvas(a))
                    continue
                if tag == "g":
                    name = a.get("data-name") or a.get("id") or "layer"
                    add_layer(name, a.get("data-blend", "normal"))
                    layer_stack.append(name)
                    continue
                if tag in ("defs", "style", "clipPath", "mask", "linearGradient", "radialGradient", "pattern", "filter", "symbol"):
                    skip_tag = tag
                    continue
                if tag in ("rect", "circle", "ellipse", "polygon", "polyline", "line", "path"):
                    emit_shape(tag, a)
            elif kind == "end":
                tag = ev[1]
                if tag == skip_tag:
                    skip_tag = None
                    continue
                if tag == "g" and layer_stack:
                    layer_stack.pop()
    for ev in tok.close():
        pass
    if not layers:
        layers = [{"name": "layer", "blend": "normal"}]
    return {"canvas": canvas, "layers": layers, "strokes": strokes}
