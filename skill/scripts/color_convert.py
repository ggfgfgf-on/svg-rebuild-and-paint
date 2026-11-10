"""Color space conversion for the plan/SVG bridge.

Target is always sRGB.  ICC profiles are loaded through Pillow's ImageCms
(lcms2).  GPU renderers only ever see the converted sRGB values.
"""
from __future__ import annotations
import io
import math
import re

_PROFILES = {}
_SRGB = None


def _srgb_profile():
    global _SRGB
    if _SRGB is None:
        from PIL import ImageCms
        _SRGB = ImageCms.createProfile("sRGB")
    return _SRGB


def register_profile(name, icc_bytes):
    from PIL import ImageCms
    if isinstance(icc_bytes, (bytes, bytearray)):
        p = ImageCms.ImageCmsProfile(io.BytesIO(bytes(icc_bytes)))
    else:
        p = icc_bytes
    _PROFILES[str(name)] = p
    _PROFILES["--" + str(name)] = p


def clear_profiles():
    _PROFILES.clear()


def _clamp(v, lo=0.0, hi=1.0):
    return max(lo, min(hi, v))


def _hex(s):
    s = s.lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    return [int(s[i:i + 2], 16) for i in (0, 2, 4)]


def _nums(s):
    return [float(x) for x in re.findall(r"[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?", s)]


def _linear(v):
    v = v / 255.0 if v > 1.0 else v
    return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4


def _gamma(v):
    v = _clamp(v)
    return 12.92 * v if v <= 0.0031308 else 1.055 * (v ** (1 / 2.4)) - 0.055


def _xyz_to_srgb(x, y, z):
    r = 3.2406*x - 1.5372*y - 0.4986*z
    g = -0.9689*x + 1.8758*y + 0.0415*z
    b = 0.0557*x - 0.2040*y + 1.0570*z
    return [round(_gamma(v) * 255) for v in (r, g, b)]


def _lab_to_xyz(L, a, b):
    fy = (L + 16) / 116.0
    fx = fy + a / 500.0
    fz = fy - b / 200.0
    def f(t):
        return t ** 3 if t > 6 / 29 else 3 * (6 / 29) ** 2 * (t - 4 / 29)
    return 0.95047 * f(fx), 1.0 * f(fy), 1.08883 * f(fz)


def _p3_to_srgb(r, g, b):
    r, g, b = _linear(r), _linear(g), _linear(b)
    x = 0.4865709*r + 0.2656677*g + 0.1982173*b
    y = 0.2289746*r + 0.6917385*g + 0.0792869*b
    z = 0.0000000*r + 0.0451134*g + 1.0439444*b
    return _xyz_to_srgb(x, y, z)


def _oklab_to_srgb(L, a, b):
    l_ = L + 0.3963377774*a + 0.2158037573*b
    m_ = L - 0.1055613458*a - 0.0638541728*b
    s_ = L - 0.0894841775*a - 1.2914855480*b
    l, m, s = l_**3, m_**3, s_**3
    r = 4.0767416621*l - 3.3077115913*m + 0.2309699292*s
    g = -1.2684380046*l + 2.6097574011*m - 0.3413193965*s
    bb = -0.0041960863*l - 0.7034186147*m + 1.7076147010*s
    return [round(_gamma(v) * 255) for v in (r, g, b)]


def _icc_to_srgb(r, g, b, profile_name):
    from PIL import Image, ImageCms
    p = _PROFILES.get(str(profile_name)) or _PROFILES.get("--" + str(profile_name))
    if p is None:
        raise ValueError("unknown ICC profile: %s" % profile_name)
    tr = ImageCms.buildTransform(p, _srgb_profile(), "RGB", "RGB")
    im = Image.new("RGB", (1, 1), (int(r), int(g), int(b)))
    out = ImageCms.applyTransform(im, tr).getpixel((0, 0))
    return [int(out[0]), int(out[1]), int(out[2])]


def to_srgb(value, profile=None):
    """Return [r,g,b] 0..255.  Accepts hex/rgb()/hsl()/color()/lab()/oklab()."""
    if value is None:
        return [0, 0, 0]
    if isinstance(value, (list, tuple)):
        return [int(round(float(v))) for v in value[:3]]
    s = str(value).strip()
    low = s.lower()
    if not s:
        return [0, 0, 0]
    if s.startswith("#"):
        return _hex(s)
    if low.startswith("rgb"):
        v = _nums(s)
        return [int(round(v[0])), int(round(v[1])), int(round(v[2]))] if len(v) >= 3 else [0, 0, 0]
    if low.startswith("hsl"):
        v = _nums(s)
        if len(v) < 3:
            return [0, 0, 0]
        h, ss, ll = v[0] % 360 / 360.0, _clamp(v[1] / 100.0), _clamp(v[2] / 100.0)
        def hue(p, q, t):
            t %= 1.0
            if t < 1/6: return p + (q-p)*6*t
            if t < 1/2: return q
            if t < 2/3: return p + (q-p)*(2/3-t)*6
            return p
        if ss == 0:
            r = g = b = ll
        else:
            q = ll * (1+ss) if ll < 0.5 else ll + ss - ll*ss
            p = 2*ll - q
            r, g, b = hue(p, q, h+1/3), hue(p, q, h), hue(p, q, h-1/3)
        return [round(r*255), round(g*255), round(b*255)]
    if low.startswith("color("):
        body = s[s.find("(")+1:s.rfind(")")]
        toks = body.replace(",", " ").split()
        if not toks:
            return [0, 0, 0]
        space = toks[0].lower()
        v = [float(x) for x in toks[1:4]]
        if len(v) < 3:
            return [0, 0, 0]
        if space in ("display-p3", "p3"):
            return _p3_to_srgb(*v)
        if space in ("srgb", "srgb-linear"):
            if space == "srgb-linear":
                return [round(_gamma(x)*255) for x in v]
            return [round(_clamp(x)*255) for x in v]
        raise ValueError("unsupported color space: %s" % space)
    if low.startswith("lab("):
        v = _nums(s)
        if len(v) < 3: return [0, 0, 0]
        return _xyz_to_srgb(*_lab_to_xyz(v[0], v[1], v[2]))
    if low.startswith("lch("):
        v = _nums(s)
        if len(v) < 3: return [0, 0, 0]
        h = math.radians(v[2])
        return _xyz_to_srgb(*_lab_to_xyz(v[0], math.cos(h)*v[1], math.sin(h)*v[1]))
    if low.startswith("oklab("):
        v = _nums(s)
        return _oklab_to_srgb(*v[:3]) if len(v) >= 3 else [0, 0, 0]
    if low.startswith("oklch("):
        v = _nums(s)
        if len(v) < 3: return [0, 0, 0]
        h = math.radians(v[2])
        return _oklab_to_srgb(v[0], math.cos(h)*v[1], math.sin(h)*v[1])
    if low.startswith("icc-color("):
        body = s[s.find("(")+1:s.rfind(")")]
        name, rest = body.split(",", 1)
        v = _nums(rest)
        return _icc_to_srgb(v[0], v[1], v[2], name.strip())
    if low.startswith("device-cmyk("):
        if not profile:
            raise ValueError("device-cmyk needs a device ICC profile")
        return _icc_to_srgb(0, 0, 0, profile)
    named = {"white": [255,255,255], "black": [0,0,0], "red": [255,0,0],
             "green": [0,128,0], "blue": [0,0,255], "none": [0,0,0]}
    if low in named:
        return named[low]
    # fallback: comma separated numbers
    v = _nums(s)
    if len(v) >= 3:
        return [int(round(v[0])), int(round(v[1])), int(round(v[2]))]
    raise ValueError("unsupported color: %s" % value)
