"""Render image / text / pattern overlay strokes to a full-canvas RGBA array."""
import base64
import io
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from gradient_xform import _mat_mul, _inv


def _mat_apply(m, x, y):
    return (m[0]*x + m[2]*y + m[4], m[1]*x + m[3]*y + m[5])


def _find_font(family, size, weight=""):
    fam = (family or "Arial").strip().lower()
    bold = str(weight).lower() in ("bold", "600", "700", "800", "900")
    names = {
        "arial": ("arialbd.ttf" if bold else "arial.ttf"),
        "helvetica": ("arialbd.ttf" if bold else "arial.ttf"),
        "times": ("timesbd.ttf" if bold else "times.ttf"),
        "times new roman": ("timesbd.ttf" if bold else "times.ttf"),
        "courier": ("courbd.ttf" if bold else "cour.ttf"),
        "courier new": ("courbd.ttf" if bold else "cou.ttf"),
        "segoe ui": ("segoeuib.ttf" if bold else "segoeui.ttf"),
        "microsoft yahei": ("msyhbd.ttc" if bold else "msyh.ttc"),
        "微软雅黑": ("msyhbd.ttc" if bold else "msyh.ttc"),
        "simsun": "simsun.ttc",
        "宋体": "simsun.ttc",
    }
    root = Path("C:/Windows/Fonts")
    cand = names.get(fam)
    if isinstance(cand, str) and (root / cand).exists():
        return ImageFont.truetype(str(root / cand), max(1, int(round(size))))
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


def _color(v, default=(0, 0, 0)):
    if v is None:
        return default
    if isinstance(v, str):
        v = v.lstrip("#")
        return tuple(int(v[i:i + 2], 16) for i in (0, 2, 4))
    return tuple(v[:3])


def render_overlay(s, w, h):
    w, h = int(w), int(h)
    base = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    preset = str(s.get("preset", ""))
    if preset == "image" and isinstance(s.get("image"), dict):
        im = s["image"]
        try:
            src = Image.open(io.BytesIO(base64.b64decode(im.get("data", "")))).convert("RGBA")
        except Exception:
            return None
        tw = max(1, int(round(float(s.get("width", src.width)))))
        th = max(1, int(round(float(s.get("height", src.height)))))
        src = src.resize((tw, th), Image.LANCZOS)
        a = src.getchannel("A").point(lambda v: int(v * max(0.0, min(1.0, float(s.get("opacity", 1.0))))))
        src.putalpha(a)
        tr = s.get("transform")
        if isinstance(tr, (list, tuple)) and len(tr) == 6:
            x0, y0 = float(s.get("x", 0)), float(s.get("y", 0))
            L = _mat_mul(tuple(float(v) for v in tr), (1, 0, 0, 1, x0, y0))
            pts = [_mat_apply(L, 0, 0), _mat_apply(L, tw, 0), _mat_apply(L, 0, th), _mat_apply(L, tw, th)]
            xs = [q[0] for q in pts]; ys = [q[1] for q in pts]
            mx, my = int(np.floor(min(xs))), int(np.floor(min(ys)))
            mw, mh = max(1, int(np.ceil(max(xs))) - mx), max(1, int(np.ceil(max(ys))) - my)
            inv = _inv(L)
            if inv is None:
                return None
            coeff = (inv[0], inv[2], inv[0]*mx + inv[2]*my + inv[4],
                     inv[1], inv[3], inv[1]*mx + inv[3]*my + inv[5])
            piece = src.transform((mw, mh), Image.AFFINE, coeff, resample=Image.BICUBIC)
            base.paste(piece, (mx, my), piece)
        else:
            base.paste(src, (int(round(float(s.get("x", 0)))), int(round(float(s.get("y", 0))))), src)
    elif preset == "text":
        txt = str(s.get("text", ""))
        if not txt:
            return None
        layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer)
        alpha = max(0, min(255, int(round(float(s.get("opacity", 1.0)) * 255))))
        anchor = {"middle": "ms", "end": "rs"}.get(str(s.get("text_anchor", "start")), "ls")
        runs = s.get("runs") if isinstance(s.get("runs"), list) and s.get("runs") else None
        if runs:
            cx, cy = float(s.get("x", 0)), float(s.get("y", 0))
            for r in runs:
                font = _find_font(r.get("font_family", s.get("font_family", "Arial")),
                                  float(r.get("font_size", s.get("font_size", 16))), s.get("font_weight", ""))
                col = _color(r.get("color", s.get("color")), (0, 0, 0))
                rt = str(r.get("text", ""))
                draw.text((cx, cy), rt, font=font, fill=(col[0], col[1], col[2], alpha), anchor=anchor)
                try:
                    cx += draw.textlength(rt, font=font)
                except Exception:
                    cx += 0.6 * float(r.get("font_size", s.get("font_size", 16))) * len(rt)
        else:
            font = _find_font(s.get("font_family", "Arial"), float(s.get("font_size", 16)), s.get("font_weight", ""))
            col = _color(s.get("color"), (0, 0, 0))
            draw.text((float(s.get("x", 0)), float(s.get("y", 0))), txt,
                      font=font, fill=(col[0], col[1], col[2], alpha), anchor=anchor)
        tr = s.get("transform")
        if isinstance(tr, (list, tuple)) and len(tr) == 6:
            inv = _inv(tuple(float(v) for v in tr))
            if inv is not None:
                layer = layer.transform((w, h), Image.AFFINE,
                                        (inv[0], inv[2], inv[4], inv[1], inv[3], inv[5]), resample=Image.BICUBIC)
        base = layer
    elif isinstance(s.get("pattern"), dict):
        ptn = s["pattern"]
        try:
            tile = Image.open(io.BytesIO(base64.b64decode(ptn.get("data", "")))).convert("RGBA")
        except Exception:
            return None
        tw = max(1, int(round(float(ptn.get("width", tile.width)))))
        th = max(1, int(round(float(ptn.get("height", tile.height)))))
        if ptn.get("units") == "objectBoundingBox":
            q = [(float(a[0]), float(a[1])) for a in (s.get("points") if s.get("points") is not None else [])]
            if len(q) >= 3:
                bw = max(1e-6, max(a[0] for a in q) - min(a[0] for a in q))
                bh = max(1e-6, max(a[1] for a in q) - min(a[1] for a in q))
                tw = max(1, int(round(float(ptn.get("width", 0.1)) * bw)))
                th = max(1, int(round(float(ptn.get("height", 0.1)) * bh)))
        if tile.size != (tw, th):
            tile = tile.resize((tw, th), Image.LANCZOS)
        canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        for y in range(0, h, th):
            for x in range(0, w, tw):
                canvas.alpha_composite(tile, (x, y))
        mask = Image.new("L", (w, h), 0)
        pts = [(float(a[0]), float(a[1])) for a in (s.get("points") if s.get("points") is not None else [])]
        if len(pts) >= 3:
            ImageDraw.Draw(mask).polygon(pts, fill=255)
        op = max(0.0, min(1.0, float(s.get("opacity", 1.0))))
        if op < 1.0:
            mask = mask.point(lambda v: int(v * op))
        base.paste(canvas, (0, 0), mask)
    else:
        return None
    return np.asarray(base, dtype=np.uint8)
