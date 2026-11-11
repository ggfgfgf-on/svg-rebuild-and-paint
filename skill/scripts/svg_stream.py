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
try:
    import plan_svg_native as _native
except Exception:
    _native = None

_TAG = re.compile(r"([A-Za-z_][\w:.-]*)")
_ATTR = re.compile(r"""([A-Za-z_][\w:.-]*)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))""")


class SVGTokenizer:
    def __init__(self):
        self.buf = ""
        self._big = None
        self._big_quote = None

    def _stash_big(self):
        self._big = [self.buf]
        self.buf = ""
        q = None
        for c in self._big[0]:
            if q:
                if c == q:
                    q = None
            elif c in "\"'":
                q = c
        self._big_quote = q

    def _scan_gt(self, chunk):
        q = self._big_quote
        for i, c in enumerate(chunk):
            if q:
                if c == q:
                    q = None
            elif c in "\"'":
                q = c
            elif c == ">":
                self._big_quote = q
                return i
        self._big_quote = q
        return -1

    def feed(self, chunk):
        if self._big is not None:
            end = self._scan_gt(chunk)
            self._big.append(chunk)
            if end < 0:
                return
            self.buf = "".join(self._big)
            self._big = None
            self._big_quote = None
        else:
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
                    self._stash_big(); return
                self.buf = self.buf[end + 3:]
                continue
            if self.buf.startswith("<![CDATA["):
                end = self.buf.find("]]>")
                if end < 0:
                    self._stash_big(); return
                yield ("text", self.buf[9:end])
                self.buf = self.buf[end + 3:]
                continue
            if self.buf.startswith("<?"):
                end = self.buf.find("?>")
                if end < 0:
                    self._stash_big(); return
                self.buf = self.buf[end + 2:]
                continue
            if self.buf.startswith("<!"):
                end = self._tag_end(self.buf, 2)
                if end < 0:
                    self._stash_big(); return
                self.buf = self.buf[end + 1:]
                continue
            end = self._tag_end(self.buf, 1)
            if end < 0:
                self._stash_big(); return
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


_PATH_TOK = re.compile(r"[MmLlHhVvCcSsQqTtAaZz]|[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?")
_ARITY = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6, "S": 4, "Q": 4, "T": 2, "A": 7, "Z": 0}


class _PathStreamParser:
    """Command-level incremental SVG path parser (stdout -> plan polylines)."""

    def __init__(self):
        self.buf = ""
        self.cmd = None
        self.nums = []
        self.cur = [0.0, 0.0]
        self.start = [0.0, 0.0]
        self.pts = []
        self.prev_ctrl = None
        self.prev_cmd = None
        self.out = []

    def feed(self, text):
        self.buf += text
        ms = list(_PATH_TOK.finditer(self.buf))
        if not ms:
            return
        safe = len(self.buf)
        last = ms[-1]
        if last.group() not in "MmLlHhVvCcSsQqTtAaZz" and last.end() == len(self.buf):
            safe = last.start()
        for m in ms:
            if m.start() >= safe:
                break
            self._tok(m.group())
        self.buf = self.buf[safe:]

    def finish(self):
        for m in _PATH_TOK.finditer(self.buf):
            self._tok(m.group())
        self.buf = ""
        self._flush()

    def _flush(self):
        if len(self.pts) >= 2:
            self.out.append(self.pts)
        self.pts = []
        self.prev_ctrl = None
        self.prev_cmd = None

    def _add(self, p):
        if not self.pts or abs(self.pts[-1][0] - p[0]) > 1e-9 or abs(self.pts[-1][1] - p[1]) > 1e-9:
            self.pts.append([float(p[0]), float(p[1])])

    def _tok(self, tok):
        if tok in "MmLlHhVvCcSsQqTtAaZz":
            if tok in "Zz":
                if self.pts:
                    self._add(self.start)
                    self._flush()
                self.cmd = None
                self.nums = []
                return
            if tok in "Mm":
                self._flush()
            self.cmd = tok
            self.nums = []
            return
        self.nums.append(float(tok))
        while self.cmd:
            n = _ARITY[self.cmd.upper()]
            if n == 0:
                break
            if len(self.nums) < n:
                break
            vals = self.nums[:n]
            del self.nums[:n]
            self._step(self.cmd, vals)
            if self.cmd.upper() == "M":
                self.cmd = "l" if self.cmd.islower() else "L"

    def _step(self, cmd, v):
        rel = cmd.islower()
        C = cmd.upper()
        cur = self.cur
        if C == "M":
            x, y = v
            if rel:
                x += cur[0]; y += cur[1]
            self.cur = [x, y]; self.start = [x, y]; self._add(self.cur)
            self.prev_ctrl = None; self.prev_cmd = C
        elif C == "L":
            x, y = v
            if rel:
                x += cur[0]; y += cur[1]
            self.cur = [x, y]; self._add(self.cur)
            self.prev_ctrl = None; self.prev_cmd = C
        elif C == "H":
            x = v[0] + cur[0] if rel else v[0]
            self.cur = [x, cur[1]]; self._add(self.cur)
            self.prev_ctrl = None; self.prev_cmd = C
        elif C == "V":
            y = v[0] + cur[1] if rel else v[0]
            self.cur = [cur[0], y]; self._add(self.cur)
            self.prev_ctrl = None; self.prev_cmd = C
        elif C == "C":
            x1, y1, x2, y2, x, y = v
            if rel:
                x1 += cur[0]; y1 += cur[1]; x2 += cur[0]; y2 += cur[1]; x += cur[0]; y += cur[1]
            p0 = list(cur)
            for k in range(1, 9):
                t = k / 8.0; u = 1 - t
                self._add([u*u*u*p0[0] + 3*u*u*t*x1 + 3*u*t*t*x2 + t*t*t*x,
                           u*u*u*p0[1] + 3*u*u*t*y1 + 3*u*t*t*y2 + t*t*t*y])
            self.cur = [x, y]; self.prev_ctrl = [x2, y2]; self.prev_cmd = C
        elif C == "S":
            x2, y2, x, y = v
            if rel:
                x2 += cur[0]; y2 += cur[1]; x += cur[0]; y += cur[1]
            x1, y1 = (2*cur[0]-self.prev_ctrl[0], 2*cur[1]-self.prev_ctrl[1]) if self.prev_cmd in ("C", "S") and self.prev_ctrl else cur
            p0 = list(cur)
            for k in range(1, 9):
                t = k / 8.0; u = 1 - t
                self._add([u*u*u*p0[0] + 3*u*u*t*x1 + 3*u*t*t*x2 + t*t*t*x,
                           u*u*u*p0[1] + 3*u*u*t*y1 + 3*u*t*t*y2 + t*t*t*y])
            self.cur = [x, y]; self.prev_ctrl = [x2, y2]; self.prev_cmd = C
        elif C == "Q":
            x1, y1, x, y = v
            if rel:
                x1 += cur[0]; y1 += cur[1]; x += cur[0]; y += cur[1]
            p0 = list(cur)
            for k in range(1, 9):
                t = k / 8.0; u = 1 - t
                self._add([u*u*p0[0] + 2*u*t*x1 + t*t*x, u*u*p0[1] + 2*u*t*y1 + t*t*y])
            self.cur = [x, y]; self.prev_ctrl = [x1, y1]; self.prev_cmd = C
        elif C == "T":
            x, y = v
            if rel:
                x += cur[0]; y += cur[1]
            x1, y1 = (2*cur[0]-self.prev_ctrl[0], 2*cur[1]-self.prev_ctrl[1]) if self.prev_cmd in ("Q", "T") and self.prev_ctrl else cur
            p0 = list(cur)
            for k in range(1, 9):
                t = k / 8.0; u = 1 - t
                self._add([u*u*p0[0] + 2*u*t*x1 + t*t*x, u*u*p0[1] + 2*u*t*y1 + t*t*y])
            self.cur = [x, y]; self.prev_ctrl = [x1, y1]; self.prev_cmd = C
        elif C == "A":
            rx, ry, rot, laf, sf, x, y = v
            if rel:
                x += cur[0]; y += cur[1]
            p0 = list(cur)
            for q in _ps._arc_pts(p0, [x, y], rx, ry, rot, int(laf), int(sf)):
                self._add(q)
            self.cur = [x, y]; self.prev_ctrl = None; self.prev_cmd = C


def _stream_simple_file(path, chunk_size=1 << 20):
    """Fast path for path/rect-only SVG files: mmap + chunked d parsing."""
    import mmap
    import os
    with open(path, "rb") as f:
        with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
            head_m = re.search(br"<svg\b[^>]*>", mm)
            if not head_m:
                return None
            head = head_m.group(0).decode("utf-8", "replace")
            vb = re.search(r'viewBox="([^"]+)"', head)
            if vb:
                nums = re.findall(r"[-+]?[0-9.]+", vb.group(1))
                w, h = (int(float(nums[2])), int(float(nums[3]))) if len(nums) >= 4 else (1200, 900)
            else:
                wm = re.search(r'width="([0-9.]+)"', head)
                hm = re.search(r'height="([0-9.]+)"', head)
                w = int(float(wm.group(1))) if wm else 1200
                h = int(float(hm.group(1))) if hm else 900
            if any(x in mm for x in (b"<defs", b"<g", b"<use", b"<mask", b"<clipPath",
                                      b"<pattern", b"<filter", b"<style", b"<image", b"<text")):
                return None
            strokes = []

            def add_fill(attrs, subpaths):
                fill = attrs.get("fill", "")
                if not fill or fill == "none":
                    return
                try:
                    op = float(attrs.get("fill-opacity", attrs.get("opacity", 1.0)) or 1.0)
                except Exception:
                    op = 1.0
                rule = attrs.get("fill-rule", "nonzero")
                color = _ps._color(fill, (0, 0, 0))
                good = [q for q in subpaths if len(q) >= 3]
                if not good:
                    return
                st = {"layer": "layer", "preset": "fill", "opacity": op,
                      "color": color,
                      "points": [[float(p[0]), float(p[1]), 1.0] for p in good[0]],
                      "fill_rule": rule}
                if len(good) > 1:
                    st["subpaths"] = [[[float(p[0]), float(p[1]), 1.0] for p in q] for q in good]
                strokes.append(st)

            for rm in re.finditer(br"<rect\b([^>]*)>", mm):
                raw = rm.group(1).decode("utf-8", "replace")
                attrs = dict(re.findall(r'([\w:-]+)="([^"]*)"', raw))
                try:
                    x = float(attrs.get("x", 0)); y = float(attrs.get("y", 0))
                    rw = float(attrs.get("width", 0)); rh = float(attrs.get("height", 0))
                except Exception:
                    continue
                if rw > 0 and rh > 0:
                    add_fill(attrs, [[[x, y], [x + rw, y], [x + rw, y + rh], [x, y + rh]]])

            for pm in re.finditer(br"<path\b", mm):
                start = pm.start()
                tag_end = mm.find(b">", start)
                if tag_end < 0:
                    continue
                dpos = mm.find(b'd="', start, tag_end)
                q = b'"'
                if dpos < 0:
                    dpos = mm.find(b"d='", start, tag_end)
                    q = b"'"
                if dpos < 0:
                    continue
                qstart = dpos + 3
                qend = mm.find(q, qstart, tag_end)
                if qend < 0:
                    continue
                pre = mm[start:qstart].decode("utf-8", "replace")
                post = mm[qend + 1:tag_end].decode("utf-8", "replace")
                attrs = dict(re.findall(r'([\w:-]+)="([^"]*)"', pre + " " + post))
                subs = None
                if _native is not None and _native.available():
                    try:
                        subs = _native.parse_d_subpaths(mm[qstart:qend].decode("ascii", "ignore"))
                    except Exception:
                        subs = None
                if subs is None:
                    ps = _PathStreamParser()
                    for i in range(qstart, qend, chunk_size):
                        ps.feed(mm[i:min(i + chunk_size, qend)].decode("ascii", "ignore"))
                    ps.finish()
                    subs = ps.out
                add_fill(attrs, subs)
            if not strokes:
                return None
            return {"canvas": [w, h], "layers": [{"name": "layer", "blend": "normal"}], "strokes": strokes}


def stream_svg_to_plan(source, chunk_size=65536):
    """Parse a basic SVG from a path or text using the streaming tokenizer."""
    if isinstance(source, (str, Path)) and "<" not in str(source) and Path(str(source)).exists():
        try:
            doc = _stream_simple_file(str(source))
            if doc:
                return doc
        except Exception:
            pass
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
    base_dir = None
    if isinstance(source, (str, Path)) and "<" not in str(source) and Path(str(source)).exists():
        base_dir = Path(str(source)).parent
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
        if p["type"] == "radial":
            for k in ("fx", "fy", "fr"):
                if k in node["attrs"]:
                    p[k] = node["attrs"][k]
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
    inherit_attrs = {}
    inherit_stack = []

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
                    k = k.strip(); v = v.strip()
                    imp = v.endswith("!important")
                    if imp:
                        v = v[:-10].strip()
                    props[k] = (v, imp)
            if props:
                for one in sel.split(","):
                    one = one.strip()
                    if not one:
                        continue
                    if any(p in one for p in (":hover", ":active", ":focus")):
                        continue
                    one = one.replace(":root", "*")
                    if ":" in one:
                        continue
                    css_rules.append((_spec(one), len(css_rules), one, props))

    def apply_css(tag, a, inherited=None):
        res = dict(inherited or {})
        res.update(a)
        chosen = {}
        for _, _, sel, props in sorted(css_rules):
            ok = False
            if sel == "*" or sel == tag:
                ok = True
            elif sel.startswith(".") and sel[1:] in str(res.get("class", "")).split():
                ok = True
            elif sel.startswith("#") and sel[1:] == res.get("id"):
                ok = True
            if ok:
                for k, (v, imp) in props.items():
                    if imp or k not in chosen:
                        chosen[k] = (v, imp)
        for k, (v, imp) in chosen.items():
            res[k] = v
        inline = res.get("style")
        if inline:
            for part in inline.split(";"):
                if ":" in part:
                    k, v = part.split(":", 1)
                    res[k.strip()] = v.strip()
        return res

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
        clip_refs = re.findall(r"url\(#([^)]+)\)", str(a.get("clip-path", "")) + " " + str(a.get("mask", "")))
        if clip_refs:
            refs = [defs_map[r] for r in clip_refs if r in defs_map]
            if refs:
                defs_xml = "".join(node_xml(n) for n in refs)
                attrs = "".join(' %s="%s"' % (k, html.escape(str(v), quote=True)) for k, v in a.items())
                mini = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %s %s"><defs>%s</defs>'
                        '<%s%s/></svg>' % (canvas[0], canvas[1], defs_xml, tag, attrs))
                try:
                    sub = _ps._svg_to_plan_py(mini)
                    for sl in sub.get("layers", []):
                        if isinstance(sl.get("mask"), dict):
                            for L in layers:
                                if L["name"] == layer:
                                    L["mask"] = sl["mask"]
                    for st in sub.get("strokes", []):
                        st["layer"] = layer
                        strokes.append(st)
                    return
                except Exception:
                    pass
        fill = a.get("fill", "")
        stroke = a.get("stroke", "")
        data_preset = a.get("data-preset", "")
        if data_preset == "fill" or (fill and fill != "none" and not stroke):
            pts = _ps._shape_points(tag, a) if tag != "path" else None
            if tag == "path":
                subs = [q for q in _ps._parse_d_subpaths(a.get("d", "")) if len(q) >= 3]
            else:
                subs = [pts] if pts and len(pts) >= 3 else []
            subs = [tpts(q) for q in subs if q]
            holes = []
            if str(a.get("fill-rule", "")) == "evenodd" and len(subs) > 1:
                def area(q):
                    return abs(sum(q[i][0]*q[(i+1) % len(q)][1] - q[(i+1) % len(q)][0]*q[i][1] for i in range(len(q))))
                items = [(area(q), q) for q in subs]
                items.sort(key=lambda x: x[0], reverse=True)
                total = sum(x[0] for x in items)
                outer_a, outer = items[0]
                if total > 0 and outer_a > total * 0.5:
                    def bbox(q):
                        xs = [p[0] for p in q]; ys = [p[1] for p in q]
                        return (min(xs), min(ys), max(xs), max(ys))
                    ob = bbox(outer)
                    for ar, q in items[1:]:
                        if ar >= outer_a:
                            continue
                        b = bbox(q)
                        if b[0] >= ob[0] and b[1] >= ob[1] and b[2] <= ob[2] and b[3] <= ob[3]:
                            holes.append(q)
                    subs = [outer]
            pt = paint_for(fill)
            pat = pattern_for(fill)
            op = float(a.get("fill-opacity", a.get("opacity", 1.0)) or 1.0)
            rule = a.get("fill-rule", "nonzero")
            color = _ps._color(fill, (0, 0, 0))
            if pt and fill.startswith("url(") and pt.get("stops"):
                cols = [s[1] for s in pt["stops"] if len(s) > 1]
                if cols:
                    color = [sum(c[i] for c in cols) / len(cols) for i in range(3)]
            if not holes and len(subs) > 1:
                st = {"layer": layer, "preset": "fill", "opacity": op, "color": color,
                      "points": [[float(p[0]), float(p[1]), 1.0] for p in subs[0]],
                      "fill_rule": rule,
                      "subpaths": [[[float(p[0]), float(p[1]), 1.0] for p in q] for q in subs]}
                if pt:
                    st["paint"] = pt
                if pat:
                    st["pattern"] = pat
                strokes.append(st)
                return
            for q in subs:
                st = {"layer": layer, "preset": "fill",
                      "opacity": op, "color": color,
                      "points": [[float(p[0]), float(p[1]), 1.0] for p in q],
                      "fill_rule": rule}
                if holes:
                    st["holes"] = [[[float(p[0]), float(p[1]), 1.0] for p in h] for h in holes]
                if pt:
                    st["paint"] = pt
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
                    ga = apply_css("g", a, inherit_attrs)
                    inherit_stack.append(dict(inherit_attrs))
                    for k in ("fill", "stroke", "stroke-width", "opacity", "font-size", "font-family", "color"):
                        if k in ga:
                            inherit_attrs[k] = ga[k]
                    mat_stack.append(_ps._mat_mul(mat_stack[-1], _ps._parse_transform(a.get("transform"))))
                    continue
                if tag == "style":
                    in_style = True
                    style_buf = []
                    skip_tag = tag
                    continue
                if tag == "link":
                    href = a.get("href", "")
                    if base_dir is not None and href and "://" not in href and not href.startswith("data:"):
                        try:
                            parse_css((base_dir / href).read_text(encoding="utf-8"))
                        except Exception:
                            pass
                    continue
                if tag in ("defs", "clipPath", "mask", "linearGradient", "radialGradient", "pattern", "filter", "symbol"):
                    skip_tag = tag
                    continue
                if tag in ("rect", "circle", "ellipse", "polygon", "polyline", "line", "path"):
                    emit_shape(tag, apply_css(tag, a, inherit_attrs))
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
                    if inherit_stack:
                        inherit_attrs = inherit_stack.pop()
                    if len(mat_stack) > 1:
                        mat_stack.pop()
    for ev in tok.close():
        pass
    if not layers:
        layers = [{"name": "layer", "blend": "normal"}]
    return {"canvas": canvas, "layers": layers, "strokes": strokes}
