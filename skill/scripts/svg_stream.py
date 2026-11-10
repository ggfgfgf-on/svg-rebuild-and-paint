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
    mat_stack = [(1.0, 0.0, 0.0, 1.0, 0.0, 0.0)]
    skip_tag = None
    order = []

    def add_layer(name, blend="normal"):
        if name not in order:
            order.append(name)
            layers.append({"name": name, "blend": blend or "normal"})
        return name

    def tpts(q):
        m = mat_stack[-1]
        return [[_ps._mat_apply(m, float(p[0]), float(p[1]))[0], _ps._mat_apply(m, float(p[0]), float(p[1]))[1]] for p in q]

    def paint_for(ref):
        m = re.match(r"url\(#([^)]+)\)", str(ref or ""))
        if not m:
            return None
        node = defs_map.get(m.group(1))
        if not node or node["tag"] not in ("linearGradient", "radialGradient"):
            return None
        st = []
        for ch in node.get("children", []):
            if ch["tag"] != "stop":
                continue
            ca = ch["attrs"]
            off = str(ca.get("offset", "0")).strip()
            try:
                off = float(off[:-1]) / 100.0 if off.endswith("%") else float(off)
            except Exception:
                off = 0.0
            a = max(0.0, min(1.0, float(ca.get("stop-opacity", 1.0))))
            st.append([off, list(_ps._color(ca.get("stop-color", "#000"), (0, 0, 0))), a])
        if not st:
            return None
        p = {"type": "linear" if node["tag"] == "linearGradient" else "radial",
             "units": node["attrs"].get("gradientUnits", "objectBoundingBox"), "stops": st,
             "source": {"gradient": {"tag": node["tag"], "attrs": dict(node["attrs"])}}}
        keys = ("x1", "y1", "x2", "y2") if p["type"] == "linear" else ("cx", "cy", "r")
        for k in keys:
            p[k] = node["attrs"].get(k, "0%" if p["type"] == "linear" and k in ("x1", "y1") else ("100%" if k == "x2" else "50%"))
        return p

    def node_xml(node):
        tag = node["tag"]
        attrs = "".join(' %s="%s"' % (k, html.escape(str(v), quote=True)) for k, v in node["attrs"].items())
        kids = "".join(node_xml(ch) for ch in node.get("children", []))
        return "<%s%s>%s</%s>" % (tag, attrs, kids, tag)

    def pattern_for(ref):
        m = re.match(r"url\(#([^)]+)\)", str(ref or ""))
        if not m:
            return None
        node = defs_map.get(m.group(1))
        if not node or node["tag"] != "pattern":
            return None
        def num(k, d):
            try:
                v = str(node["attrs"].get(k, d))
                return float(v[:-1]) / 100.0 if v.endswith("%") else float(v)
            except Exception:
                return float(d)
        units = node["attrs"].get("patternUnits", "objectBoundingBox")
        fw, fh = num("width", 0.1), num("height", 0.1)
        pw, ph = (max(8, int(fw * 256)), max(8, int(fh * 256))) if units == "objectBoundingBox" else (max(1, int(fw)), max(1, int(fh)))
        children = "".join(node_xml(ch) for ch in node.get("children", []))
        extra = "".join(node_xml(n) for n in {id(v): v for v in defs_map.values()}.values()
                        if n["tag"] in ("linearGradient", "radialGradient"))
        mini = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %s %s" width="%s" height="%s"><defs>%s</defs>%s</svg>'
                % (pw, ph, pw, ph, extra, children))
        try:
            d = _ps.svg_to_plan(mini)
            import paint_plan as _pp
            from PIL import Image
            import io, base64
            img, _ = _pp.render_plan(d)
            buf = io.BytesIO(); Image.fromarray(img, "RGBA").save(buf, format="PNG")
            return {"encoding": "png-base64", "data": base64.b64encode(buf.getvalue()).decode("ascii"),
                    "width": fw if units == "objectBoundingBox" else pw,
                    "height": fh if units == "objectBoundingBox" else ph,
                    "units": units}
        except Exception:
            return None

    css_rules = []
    style_buf = []
    in_style = False

    def _spec(sel):
        return sel.count("#") * 100 + sel.count(".") * 10 + (1 if sel and sel[0] not in ".#*" else 0)

    def parse_css(text):
        try:
            text = _ps._css_media_text(text or "")
        except Exception:
            pass
        for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", text or ""):
            props = {}
            for part in body.split(";"):
                if ":" in part:
                    k, v = part.split(":", 1)
                    props[k.strip()] = v.strip()
            if props:
                for one in sel.split(","):
                    one = one.strip()
                    if one:
                        css_rules.append((_spec(one), len(css_rules), one, props))

    def apply_css(tag, a):
        a = dict(a)
        for _, _, sel, props in sorted(css_rules):
            ok = False
            if sel == "*" or sel == tag:
                ok = True
            elif sel.startswith(".") and sel[1:] in str(a.get("class", "")).split():
                ok = True
            elif sel.startswith("#") and sel[1:] == a.get("id"):
                ok = True
            if ok:
                a.update(props)
        inline = a.get("style")
        if inline:
            for part in inline.split(";"):
                if ":" in part:
                    k, v = part.split(":", 1)
                    a[k.strip()] = v.strip()
        return a

    def filter_for(tag, a):
        fref = a.get("filter")
        if not fref:
            return None
        m = re.match(r"url\(#([^)]+)\)", str(fref))
        if not m:
            return None
        node = defs_map.get(m.group(1))
        if not node or node["tag"] != "filter":
            return None
        attrs = "".join(' %s="%s"' % (k, html.escape(str(v), quote=True)) for k, v in a.items())
        shape = "<%s%s/>" % (tag, attrs)
        mini = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %s %s"><defs>%s</defs>%s</svg>'
                % (canvas[0], canvas[1], node_xml(node), shape))
        try:
            d = _ps.svg_to_plan(mini)
            import paint_plan as _pp
            from PIL import Image
            import io, base64
            img, _ = _pp.render_plan(d)
            buf = io.BytesIO(); Image.fromarray(img, "RGBA").save(buf, format="PNG")
            return {"encoding": "png-base64", "data": base64.b64encode(buf.getvalue()).decode("ascii"),
                    "width": canvas[0], "height": canvas[1]}
        except Exception:
            return None

    def emit_shape(tag, a):
        layer = layer_stack[-1] if layer_stack else "layer"
        add_layer(layer)
        fimg = filter_for(tag, a)
        if fimg:
            strokes.append({"layer": layer, "preset": "image", "image": fimg,
                            "x": 0, "y": 0, "width": canvas[0], "height": canvas[1], "opacity": 1.0})
            return
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
                q = tpts(q)
                st = {"layer": layer, "preset": "fill",
                      "opacity": float(a.get("fill-opacity", a.get("opacity", 1.0)) or 1.0),
                      "color": _ps._color(fill, (0, 0, 0)),
                      "points": [[float(p[0]), float(p[1]), 1.0] for p in q],
                      "fill_rule": a.get("fill-rule", "nonzero")}
                pt = paint_for(fill)
                if pt:
                    st["paint"] = pt
                pat = pattern_for(fill)
                if pat:
                    st["pattern"] = pat
                strokes.append(st)
            return
        if tag == "path":
            subs = _ps._parse_d_subpaths(a.get("d", ""))
        else:
            subs = [_ps._shape_points(tag, a)] if _ps._shape_points(tag, a) else []
        for q in subs:
            if len(q) < 2:
                continue
            q = tpts(q)
            st = {"layer": layer, "preset": data_preset or ("flat" if tag == "line" else "round_soft"),
                  "size": float(a.get("stroke-width", a.get("data-size", 1.0)) or 1.0),
                  "opacity": float(a.get("stroke-opacity", a.get("opacity", 1.0)) or 1.0),
                  "hardness": float(a.get("data-hardness", 0.7) or 0.7),
                  "color": _ps._color(stroke or fill, (0, 0, 0)),
                  "points": [[float(p[0]), float(p[1]), 1.0] for p in q]}
            pt = paint_for(stroke)
            if pt:
                st["paint"] = pt
            if a.get("data-tag"):
                st["tag"] = a["data-tag"]
            strokes.append(st)

    defs_stack = []
    defs_map = {}
    use_node = None

    def emit_node(node):
        tag = node["tag"]
        a = node["attrs"]
        mat_stack.append(_ps._mat_mul(mat_stack[-1], _ps._parse_transform(a.get("transform"))))
        try:
            if tag == "g":
                for child in node.get("children", []):
                    emit_node(child)
            elif tag in ("rect", "circle", "ellipse", "polygon", "polyline", "line", "path"):
                emit_shape(tag, a)
        finally:
            if len(mat_stack) > 1:
                mat_stack.pop()

    def capture_node(tag, a):
        node = {"tag": tag, "attrs": a, "children": []}
        if defs_stack:
            defs_stack[-1]["children"].append(node)
        defs_stack.append(node)
        if a.get("id"):
            defs_map[a["id"]] = node

    in_defs = False

    for chunk in chunks:
        for ev in tok.feed(chunk):
            kind = ev[0]
            if kind == "text":
                if in_style:
                    style_buf.append(ev[1])
                continue
            if kind == "start":
                _, tag, a = ev
                if skip_tag is not None:
                    continue
                if in_defs:
                    capture_node(tag, a)
                    continue
                if tag == "defs":
                    in_defs = True
                    defs_stack = []
                    continue
                if tag == "use":
                    href = a.get("href") or a.get("{http://www.w3.org/1999/xlink}href") or ""
                    if href.startswith("#") and href[1:] in defs_map:
                        mat_stack.append(_ps._mat_mul(mat_stack[-1], _ps._parse_transform(a.get("transform"))))
                        mat_stack.append(_ps._mat_mul(mat_stack[-1], (1,0,0,1,_length(a,"x",0),_length(a,"y",0))))
                        try:
                            emit_node(defs_map[href[1:]])
                        finally:
                            if len(mat_stack) > 2:
                                mat_stack.pop()
                                mat_stack.pop()
                    continue
                if tag == "svg":
                    canvas = list(_canvas(a))
                    continue
                if tag == "g":
                    name = a.get("data-name") or a.get("id") or "layer"
                    add_layer(name, a.get("data-blend", "normal"))
                    layer_stack.append(name)
                    mat_stack.append(_ps._mat_mul(mat_stack[-1], _ps._parse_transform(a.get("transform"))))
                    continue
                if tag == "style":
                    in_style = True
                    style_buf = []
                    skip_tag = tag
                    continue
                if tag in ("defs", "clipPath", "mask", "linearGradient", "radialGradient", "pattern", "filter", "symbol"):
                    skip_tag = tag
                    continue
                if tag in ("rect", "circle", "ellipse", "polygon", "polyline", "line", "path"):
                    emit_shape(tag, apply_css(tag, a))
            elif kind == "end":
                tag = ev[1]
                if in_defs:
                    if tag == "defs":
                        in_defs = False
                        defs_stack = []
                    elif defs_stack:
                        defs_stack.pop()
                    continue
                if tag == skip_tag:
                    if tag == "style":
                        parse_css("".join(style_buf))
                        in_style = False
                    skip_tag = None
                    continue
                if tag == "g" and layer_stack:
                    layer_stack.pop()
                    if len(mat_stack) > 1:
                        mat_stack.pop()
    for ev in tok.close():
        pass
    if not layers:
        layers = [{"name": "layer", "blend": "normal"}]
    return {"canvas": canvas, "layers": layers, "strokes": strokes}
