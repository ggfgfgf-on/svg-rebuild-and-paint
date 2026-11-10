"""plan JSON -> 画作：与 daub 的 plan 契约同构的自有实现。

plan = {
  "canvas": [w, h],
  "layers": [{"name": "base", "blend": "normal", "fill": "#101820"}],   # 可省略
  "strokes": [
    {"layer": 0, "preset": "round_soft", "size": 12, "opacity": 0.9,
     "color": "#e04a3a" 或 [r,g,b], "hardness": 0.6, "spacing": 1.0,
     "region_id": 0,
     "points": [[x, y, pressure], ...]}
  ]
}

用法: python paint_plan.py plan.json out.png [--kra a.kra] [--psd a.psd]
"""
from __future__ import annotations
import base64, io, json, sys, zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np
from PIL import Image, ImageDraw, ImageFont
import paint_native
from gradient_xform import _mat_mul as _mat_mul, _inv as _mat_inv

def _mat_apply(m, x, y):
    return (m[0]*x + m[2]*y + m[4], m[1]*x + m[3]*y + m[5])
from export_layers import write_kra, write_psd


def _color(c, default=(255, 255, 255)):
    if c is None:
        return default
    if isinstance(c, str):
        c = c.lstrip("#")
        return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))
    return tuple(c)


def _find_font(family, size, weight=""):
    fam = (family or "Arial").strip().lower()
    bold = str(weight).lower() in ("bold", "600", "700", "800", "900")
    names = {
        "arial": ("arialbd.ttf" if bold else "arial.ttf"),
        "helvetica": ("arialbd.ttf" if bold else "arial.ttf"),
        "times": ("timesbd.ttf" if bold else "times.ttf"),
        "times new roman": ("timesbd.ttf" if bold else "times.ttf"),
        "courier": ("courbd.ttf" if bold else "cour.ttf"),
        "courier new": ("courbd.ttf" if bold else "cour.ttf"),
        "segoe ui": ("segoeuib.ttf" if bold else "segoeui.ttf"),
        "microsoft yahei": ("msyhbd.ttc" if bold else "msyh.ttc"),
        "微软雅黑": ("msyhbd.ttc" if bold else "msyh.ttc"),
        "simsun": "simsun.ttc",
        "宋体": "simsun.ttc",
    }
    root = Path("C:/Windows/Fonts")
    cand = names.get(fam)
    if isinstance(cand, str):
        p = root / cand
        if p.exists():
            return ImageFont.truetype(str(p), max(1, int(round(size))))
    for p in root.glob("*.tt[fc]"):
        if fam and fam.replace(" ", "") in p.stem.lower().replace(" ", ""):
            try:
                return ImageFont.truetype(str(p), max(1, int(round(size))))
            except Exception:
                pass
    try:
        return ImageFont.load_default(size=max(1, int(round(size))))
    except Exception:
        return ImageFont.load_default()


def _paint_color(paint, x, y, bbox):
    stops = paint.get("stops") or []
    if not stops:
        return None
    minx, miny, maxx, maxy = bbox
    def val(k, d):
        v = paint.get(k, d)
        if isinstance(v, str) and v.endswith("%"):
            q = float(v[:-1]) / 100.0
            return minx + (maxx-minx)*q if k in ("x1", "x2", "cx", "fx") else miny + (maxy-miny)*q
        return float(v)
    if paint.get("type") == "radial":
        cx, cy, r = val("cx", 0.5), val("cy", 0.5), max(1e-6, val("r", 0.5))
        t = min(1.0, ((x-cx)**2 + (y-cy)**2) ** 0.5 / r)
    else:
        x1, y1, x2, y2 = val("x1", 0), val("y1", 0), val("x2", 1), val("y2", 0)
        gx, gy = x2-x1, y2-y1
        gl = gx*gx + gy*gy
        t = 0.0 if gl < 1e-12 else ((x-x1)*gx + (y-y1)*gy) / gl
        t = max(0.0, min(1.0, t))
    k = 0
    while k + 1 < len(stops) and stops[k+1][0] < t:
        k += 1
    a, b = stops[k], stops[min(k+1, len(stops)-1)]
    u = 0.0 if b[0] == a[0] else (t-a[0]) / (b[0]-a[0])
    return [a[1][i] + (b[1][i]-a[1][i])*u for i in range(3)]


def _expand_paint(it):
    pts = it.get("points") or []
    if len(pts) < 2 or not isinstance(it.get("paint"), dict):
        return [it]
    xs = [float(p[0]) for p in pts]; ys = [float(p[1]) for p in pts]
    bbox = (min(xs), min(ys), max(xs), max(ys))
    out = []
    for i in range(1, len(pts)):
        p0, p1 = pts[i-1], pts[i]
        n = 6
        for k in range(n):
            t0, t1 = k/n, (k+1)/n
            a = [p0[0] + (p1[0]-p0[0])*t0, p0[1] + (p1[1]-p0[1])*t0, p0[2] if len(p0) > 2 else 1.0]
            b = [p0[0] + (p1[0]-p0[0])*t1, p0[1] + (p1[1]-p0[1])*t1, p0[2] if len(p0) > 2 else 1.0]
            c = _paint_color(it["paint"], (a[0]+b[0])/2, (a[1]+b[1])/2, bbox)
            q = dict(it)
            q["points"] = [a, b]
            q["color"] = c or it.get("color")
            q.pop("paint", None)
            out.append(q)
    return out

def _mask_alpha(spec, w, h):
    m = spec.get("mask")
    if not isinstance(m, dict):
        return None
    if m.get("encoding") == "png-base64" and m.get("data"):
        im = Image.open(io.BytesIO(base64.b64decode(m["data"]))).convert("L")
        if im.size != (w, h):
            im = im.resize((w, h), Image.BILINEAR)
        return np.asarray(im, dtype=np.uint8)
    if m.get("encoding") == "zlib-base64" and m.get("data"):
        raw = zlib.decompress(base64.b64decode(m["data"]))
        im = Image.frombytes("L", (int(m.get("width", w)), int(m.get("height", h))), raw)
        if im.size != (w, h):
            im = im.resize((w, h), Image.BILINEAR)
        return np.asarray(im, dtype=np.uint8)
    return None

def render_plan(plan, threads=8, brush=None, region=None):
    """按 plan 渲染，返回 (composite_rgba, layers[(name, rgba)])。"""
    w, h = plan["canvas"]
    layers_spec = list(plan.get("layers") or [{"name": "base", "blend": "normal"}])
    strokes_in = plan.get("strokes", [])
    for s in strokes_in:
        m = s.get("mask")
        if isinstance(m, dict):
            idx = len(layers_spec)
            layers_spec.append({"name": "masked_%d" % idx, "blend": "normal", "mask": m})
            s["layer"] = idx
    p = paint_native.Painter(int(w), int(h))
    for i, spec in enumerate(layers_spec):
        if i == 0:
            p.clear_layer(0, _color(spec.get("fill"), (0, 0, 0)), 1.0 if spec.get("fill") else 0.0)
        else:
            p.add_layer(spec.get("blend", "normal"))
        if i > 0 and spec.get("fill"):
            p.clear_layer(i, _color(spec["fill"]), 1.0)
    if region is not None:
        p.set_region(region)
    brush = brush if brush is not None else paint_native.Painter.load_brush()

    grouped = {}
    layer_index = {str(spec.get("name", "layer%d" % i)): i for i, spec in enumerate(layers_spec)}
    for s in plan.get("strokes", []):
        li = s.get("layer", 0)
        if isinstance(li, str):
            li = layer_index.get(li, 0)
        grouped.setdefault(int(li), []).append({
            "points": s.get("points") or s.get("pts"),
            "size": s.get("size", 8.0),
            "opacity": s.get("opacity", 1.0),
            "color": _color(s.get("color")),
            "preset": s.get("preset", "round_soft"),
            "hardness": s.get("hardness", 0.6),
            "spacing": s.get("spacing", 1.0),
            "region_id": int(s.get("region_id", 0)),
            "paint": s.get("paint"),
        })
    # 分层按笔触所属层提交，保证层内顺序 = plan 顺序
    for layer in sorted(grouped):
        buf = []
        for it in grouped[layer]:
            if str(it.get("preset", "")) in ("image", "text"):
                continue
            if str(it.get("preset", "")) == "fill":
                if buf:
                    p.stroke_batch(layer, buf, nthreads=threads)
                    buf = []
                pts = [(float(q[0]), float(q[1])) for q in (it.get("points") or [])]
                if len(pts) >= 3:
                    if isinstance(it.get("paint"), dict):
                        p.fill_gradient(layer, pts, it["paint"], it.get("opacity", 1.0))
                    else:
                        p.fill_polygon(layer, pts, it.get("color"), it.get("opacity", 1.0))
            elif isinstance(it.get("paint"), dict):
                if buf:
                    p.stroke_batch(layer, buf, nthreads=threads)
                    buf = []
                pts = np.asarray(it.get("points") or [], dtype=np.float32)
                if len(pts) >= 2:
                    p.stroke_gradient(layer, pts, it.get("size", 8.0), it.get("opacity", 1.0),
                                      it["paint"], it.get("preset", "round_soft"), it.get("hardness", 0.55))
            else:
                buf.append(it)
        if buf:
            p.stroke_batch(layer, buf, nthreads=threads)

    for i, spec in enumerate(layers_spec):
        alpha = _mask_alpha(spec, int(w), int(h))
        if alpha is not None:
            p.apply_mask(i, alpha)

    comp = p.composite()
    for s in strokes_in:
        if s.get("preset") != "image" or not isinstance(s.get("image"), dict):
            continue
        im = s["image"]
        try:
            raw = base64.b64decode(im.get("data", ""))
            src = Image.open(io.BytesIO(raw)).convert("RGBA")
        except Exception:
            continue
        tw = max(1, int(round(float(s.get("width", src.width)))))
        th = max(1, int(round(float(s.get("height", src.height)))))
        src = src.resize((tw, th), Image.LANCZOS)
        a = src.getchannel("A").point(lambda v: int(v * max(0.0, min(1.0, float(s.get("opacity", 1.0))))))
        src.putalpha(a)
        tr = s.get("transform")
        base = Image.fromarray(comp, "RGBA")
        if isinstance(tr, (list, tuple)) and len(tr) == 6:
            x0, y0 = float(s.get("x", 0)), float(s.get("y", 0))
            L = _mat_mul(tuple(float(v) for v in tr), (1, 0, 0, 1, x0, y0))
            pts = [_mat_apply(L, 0, 0), _mat_apply(L, tw, 0), _mat_apply(L, 0, th), _mat_apply(L, tw, th)]
            xs = [q[0] for q in pts]; ys = [q[1] for q in pts]
            mx, my = int(np.floor(min(xs))), int(np.floor(min(ys)))
            mw, mh = max(1, int(np.ceil(max(xs))) - mx), max(1, int(np.ceil(max(ys))) - my)
            inv = _mat_inv(L)
            if inv is None:
                continue
            coeff = (inv[0], inv[2], inv[0]*mx + inv[2]*my + inv[4],
                     inv[1], inv[3], inv[1]*mx + inv[3]*my + inv[5])
            piece = src.transform((mw, mh), Image.AFFINE, coeff, resample=Image.BICUBIC)
            layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
            layer.paste(piece, (mx, my), piece)
            comp = np.asarray(Image.alpha_composite(base, layer))
        else:
            layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
            layer.paste(src, (int(round(float(s.get("x", 0)))), int(round(float(s.get("y", 0))))), src)
            comp = np.asarray(Image.alpha_composite(base, layer))
    for s in strokes_in:
        if s.get("preset") != "text":
            continue
        txt = str(s.get("text", ""))
        if not txt:
            continue
        font = _find_font(s.get("font_family", "Arial"), float(s.get("font_size", 16)), s.get("font_weight", ""))
        col = _color(s.get("color"), (0, 0, 0))
        alpha = max(0, min(255, int(round(float(s.get("opacity", 1.0)) * 255))))
        anchor = {"middle": "ms", "end": "rs"}.get(str(s.get("text_anchor", "start")), "ls")
        base = Image.fromarray(comp, "RGBA")
        layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
        ImageDraw.Draw(layer).text((float(s.get("x", 0)), float(s.get("y", 0))), txt,
                                   font=font, fill=(col[0], col[1], col[2], alpha), anchor=anchor)
        comp = np.asarray(Image.alpha_composite(base, layer))
    names = [spec.get("name", "layer%d" % i) for i, spec in enumerate(layers_spec)]
    out_layers = [(names[i], p.layer_rgba(i)) for i in range(p.layer_count())]
    p.close()
    return comp, out_layers


def main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 2
    plan = json.loads(Path(argv[1]).read_text(encoding="utf-8-sig"))
    comp, layers = render_plan(plan)
    Image.fromarray(comp, "RGBA").save(argv[2])
    for flag, writer in (("--kra", write_kra), ("--psd", write_psd)):
        if flag in argv:
            writer(argv[argv.index(flag) + 1], layers, merged=comp)
    print(json.dumps({"canvas": plan["canvas"], "layers": len(layers),
                      "strokes": len(plan.get("strokes", [])), "out": argv[2]}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
