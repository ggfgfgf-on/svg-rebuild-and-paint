"""Linear gradient transform helpers.

SVG gradientTransform maps gradient coordinates to user coordinates.  For a
linear gradient this is still a linear field, so it can be rewritten as a new
pair of endpoints; radial gradients are not handled here.
"""
import math
import re

_NUM = re.compile(r"[-+]?(?:\d*\.\d+|\d+)(?:[eE][-+]?\d+)?")


def _mat_mul(a, b):
    # 2x3 affine matrices, a then b
    return (
        a[0] * b[0] + a[2] * b[1],
        a[1] * b[0] + a[3] * b[1],
        a[0] * b[2] + a[2] * b[3],
        a[1] * b[2] + a[3] * b[3],
        a[0] * b[4] + a[2] * b[5] + a[4],
        a[1] * b[4] + a[3] * b[5] + a[5],
    )


def _inv(m):
    det = m[0] * m[3] - m[1] * m[2]
    if abs(det) < 1e-12:
        return None
    return (m[3] / det, -m[1] / det, -m[2] / det, m[0] / det,
            (m[2] * m[5] - m[3] * m[4]) / det,
            (m[1] * m[4] - m[0] * m[5]) / det)


def parse_matrix(text):
    if not text:
        return None
    m = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    for name, body in re.findall(r"([a-zA-Z]+)\s*\(([^)]*)\)", str(text)):
        v = [float(x) for x in _NUM.findall(body)]
        if name == "matrix" and len(v) >= 6:
            q = tuple(v[:6])
        elif name == "translate" and v:
            q = (1, 0, 0, 1, v[0], v[1] if len(v) > 1 else 0.0)
        elif name == "scale" and v:
            q = (v[0], 0, 0, v[1] if len(v) > 1 else v[0], 0, 0)
        elif name == "rotate" and v:
            a = math.radians(v[0]); c, s = math.cos(a), math.sin(a)
            q = (c, s, -s, c, 0, 0)
            if len(v) >= 3:
                cx, cy = v[1], v[2]
                q = _mat_mul((1, 0, 0, 1, cx, cy), _mat_mul(q, (1, 0, 0, 1, -cx, -cy)))
        elif name == "skewX" and v:
            q = (1, 0, math.tan(math.radians(v[0])), 1, 0, 0)
        elif name == "skewY" and v:
            q = (1, math.tan(math.radians(v[0])), 0, 1, 0, 0)
        else:
            continue
        m = _mat_mul(m, q)
    return m


def _norm(v, default):
    if v is None:
        v = default
    if isinstance(v, str) and v.endswith("%"):
        return float(v[:-1]) / 100.0
    return float(v)



def radial_matrix(paint, bbox):
    """Return affine (a,b,c,d,e,f) mapping user coords -> gradient coords."""
    if paint.get("type") != "radial":
        return None
    src = ((paint.get("source") or {}).get("gradient") or {}).get("attrs") or {}
    text = src.get("gradientTransform") or paint.get("gradientTransform")
    if not text:
        return None
    M = parse_matrix(text)
    A = _inv(M) if M else None
    if A is None:
        return None
    units = paint.get("units", "objectBoundingBox")
    if units == "objectBoundingBox":
        bx, by, bw, bh = bbox
        B = (1.0 / max(1e-9, bw), 0.0, 0.0, 1.0 / max(1e-9, bh), -bx / max(1e-9, bw), -by / max(1e-9, bh))
        return _mat_mul(A, B)
    return A

def apply_linear(paint, bbox):
    """Return new (x1,y1,x2,y2) for a transformed linear gradient, or None."""
    if paint.get("type", "linear") != "linear":
        return None
    src = ((paint.get("source") or {}).get("gradient") or {}).get("attrs") or {}
    text = src.get("gradientTransform") or paint.get("gradientTransform")
    if not text:
        return None
    M = parse_matrix(text)
    A = _inv(M) if M else None
    if A is None:
        return None
    units = paint.get("units", "objectBoundingBox")
    x1n = _norm(paint.get("x1"), 0.0); y1n = _norm(paint.get("y1"), 0.0)
    x2n = _norm(paint.get("x2"), 1.0); y2n = _norm(paint.get("y2"), 0.0)
    gx, gy = x2n - x1n, y2n - y1n
    q1 = (M[0] * x1n + M[2] * y1n + M[4], M[1] * x1n + M[3] * y1n + M[5])
    if units == "objectBoundingBox":
        bx, by, bw, bh = bbox
        S = (1.0 / max(1e-9, bw), 0.0, 0.0, 1.0 / max(1e-9, bh), 0.0, 0.0)
        ox, oy = bx + q1[0] * bw, by + q1[1] * bh
    else:
        S = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
        ox, oy = q1[0], q1[1]
    C = _mat_mul(A, S)
    hx = C[0] * gx + C[1] * gy
    hy = C[2] * gx + C[3] * gy
    h2 = hx * hx + hy * hy
    g2 = gx * gx + gy * gy
    if h2 < 1e-18 or g2 < 1e-18:
        return None
    k = g2 / h2
    return (ox, oy, ox + hx * k, oy + hy * k)
