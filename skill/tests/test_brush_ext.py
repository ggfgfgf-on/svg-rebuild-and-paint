"""brush_ext 验收：smudge / 厚涂光照 / 动态材质 / 色彩动态 / 速度响应 / KRA·PSD 读写。"""
import sys, tempfile, os
import numpy as np
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import brush_ext as bx
import export_layers as el

ok = True


def check(label, cond, extra=""):
    global ok
    ok = ok and bool(cond)
    print(("PASS " if cond else "FAIL ") + label + ((" " + str(extra)) if extra else ""))


def base_layer(h=64, w=64):
    a = np.zeros((h, w, 4), np.float32)
    a[24:40, 8:24, :3] = [0.9, 0.2, 0.2]
    a[24:40, 8:24, 3] = 1.0
    return a


# 1) smudge
img = base_layer()
path = np.array([[16, 32, 1.0], [56, 32, 1.0]], np.float32)
s1 = bx.smudge_rgba(img, path, radius=8, amount=0.9)
s2 = bx.smudge_rgba(img, path, radius=8, amount=0.9)
moved = float(s1[32, 24:30, 0].sum())
orig = float(img[32, 24:30, 0].sum())
check("smudge deterministic", np.array_equal(s1, s2))
check("smudge drags color along path", moved > orig + 0.5, "moved=%.2f orig=%.2f" % (moved, orig))
tot0, tot1 = float(img[..., 3].sum()), float(s1[..., 3].sum())
check("smudge pigment stays bounded", 0.8 < tot1 / max(1e-6, tot0) < 1.45,
      "ratio=%.3f" % (tot1 / max(1e-6, tot0)))
check("smudge drags pigment out", float(s1[32, 26:40, 3].max()) > 0.05,
      "front_alpha=%.2f" % float(s1[32, 26:40, 3].max()))

# 2) impasto
imp_l = bx.impasto_rgba(s1, light=(-0.8, 0.3, 0.5))
imp_r = bx.impasto_rgba(s1, light=(0.8, 0.3, 0.5))
check("impasto keeps alpha", np.array_equal(imp_l[..., 3], s1[..., 3]))
check("impasto light direction matters", float(np.abs(imp_l - imp_r).max()) > 0.05)
check("impasto deterministic", np.array_equal(imp_l, bx.impasto_rgba(s1, light=(-0.8, 0.3, 0.5))))

# 3) 色彩动态
strokes = [{"points": np.zeros((2, 3), np.float32), "size": 10, "opacity": 1.0,
            "color": (200, 40, 40), "hardness": 0.7, "preset": 0} for _ in range(30)]
cd = bx.apply_color_dynamics(strokes, hue=0.05, sat=0.2, val=0.2, seed=11)
cd2 = bx.apply_color_dynamics(strokes, hue=0.05, sat=0.2, val=0.2, seed=11)
cols = {s["color"] for s in cd}
check("color dynamics deterministic", [s["color"] for s in cd] == [s["color"] for s in cd2])
check("color dynamics varies colors", len(cols) > 5, "unique=%d" % len(cols))
check("color dynamics stays in gamut", all(0 <= c <= 255 for s in cd for c in s["color"]))

# 4) 动态材质
tip = np.linspace(0, 1, 64 * 64, dtype=np.float32).reshape(64, 64)
grain = np.random.default_rng(0).random((32, 32)).astype(np.float32)
g1 = bx.grain_variant(tip, grain, amount=0.6, scale=1.0, seed=1)
g2 = bx.grain_variant(tip, grain, amount=0.6, scale=1.0, seed=2)
check("grain variant deterministic", np.array_equal(g1, bx.grain_variant(tip, grain, amount=0.6, scale=1.0, seed=1)))
check("grain variant differs by seed", not np.array_equal(g1, g2))
check("grain variant keeps range", float(g1.min()) >= 0 and float(g1.max()) <= 1)

# 5) 速度响应
fast = [{"points": np.array([[0, 0, 1], [40, 0, 1]], np.float32), "size": 20, "opacity": 1.0,
         "color": (10, 10, 10), "hardness": 0.7, "preset": 0}]
slow = [{"points": np.array([[0, 0, 1], [2, 0, 1]], np.float32), "size": 20, "opacity": 1.0,
         "color": (10, 10, 10), "hardness": 0.7, "preset": 0}]
vr = bx.apply_velocity_response(fast + slow, gain=0.5)
check("velocity shrinks fast stroke", vr[0]["size"] < vr[1]["size"] + 1e-6,
      "fast=%.2f slow=%.2f" % (vr[0]["size"], vr[1]["size"]))

# 6) KRA 回环
with tempfile.TemporaryDirectory() as td:
    layers = [("bg", np.asarray(np.dstack([s1[..., :3] * 255, s1[..., 3:] * 255]), np.uint8))]
    kp = os.path.join(td, "t.kra")
    el.write_kra(kp, layers)
    back = bx.read_kra(kp)
    check("kra read layers", len(back) == 1 and back[0]["rgba"].shape[:2] == (64, 64),
          "n=%d" % len(back))
    pp = os.path.join(td, "t.psd")
    el.write_psd(pp, layers)
    pback = bx.read_psd(pp)
    check("psd read layers", len(pback) >= 1 and pback[0]["rgba"].shape[:2] == (64, 64),
          "n=%d blend=%s" % (len(pback), pback[0].get("blend")))


# 7) effect 参与区域失效：只在矩形内重算，结果与整图重算逐位相同
big = np.zeros((128, 128, 4), np.float32)
big[40:88, 20:108] = np.array([0.85, 0.25, 0.2, 1.0], np.float32)
big[40:88, 20:108, 3] = 1.0
spath = np.array([[28, 64, 1.0], [100, 64, 1.0]], np.float32)
srect = bx.smudge_rect(spath, radius=8)
full_sm = bx.smudge_rgba(big, spath, radius=8, amount=0.8)
part_sm, used = bx.apply_in_rect(big, srect, lambda c, ox, oy: bx.smudge_rgba(
    c, spath - np.array([ox, oy, 0], np.float32), radius=8, amount=0.8), pad=int(16))
x0, y0, x1, y1 = srect
check("smudge rect rebuild == full", np.array_equal(part_sm[y0:y1 + 1, x0:x1 + 1], full_sm[y0:y1 + 1, x0:x1 + 1]))
check("smudge rect leaves outside untouched", np.array_equal(part_sm[0:y0], big[0:y0]) if y0 > 0 else True)

ire = bx.impasto_rect(full_sm)
full_im = bx.impasto_rgba(full_sm)
part_im, _ = bx.apply_in_rect(full_sm, ire, lambda c, ox, oy: bx.impasto_rgba(c), pad=2)
check("impasto rect rebuild == full", np.array_equal(part_im[ire[1]:ire[3] + 1, ire[0]:ire[2] + 1],
                                                     full_im[ire[1]:ire[3] + 1, ire[0]:ire[2] + 1]))

# 8) 逐段色彩（逐 stamp 近似）
long_stroke = [{"points": np.stack([np.linspace(10, 110, 12), np.linspace(60, 60, 12), np.ones(12)], -1).astype(np.float32),
                "size": 10, "opacity": 1.0, "color": (200, 40, 40), "hardness": 0.7, "preset": 0}]
segs = bx.split_color_dynamics(long_stroke, segments=6, hue=0.06, sat=0.2, val=0.2, seed=3)
segs2 = bx.split_color_dynamics(long_stroke, segments=6, hue=0.06, sat=0.2, val=0.2, seed=3)
check("split color deterministic", [s["color"] for s in segs] == [s["color"] for s in segs2])
check("split color segment count", len(segs) == 6, "n=%d" % len(segs))
check("split color varies along stroke", len({s["color"] for s in segs}) >= 3)
check("split keeps path endpoints", np.allclose(segs[0]["points"][0], long_stroke[0]["points"][0]) and
      np.allclose(segs[-1]["points"][-1], long_stroke[0]["points"][-1]))

# 9) PSD 原生解析（不依赖 psd-tools）
with tempfile.TemporaryDirectory() as td:
    a = np.zeros((32, 48, 4), np.uint8); a[..., 0] = 200; a[..., 3] = 255
    b = np.zeros((32, 48, 4), np.uint8); b[..., 1] = 120; b[..., 3] = 255
    pp = os.path.join(td, "two.psd")
    el.write_psd(pp, [("bottom", a), ("top", b)])
    got = bx._psd_parse_layers(Path(pp).read_bytes())
    check("psd native layer count", len(got) == 2, "n=%d" % len(got))
    check("psd native names", [g["name"] for g in got] == ["bottom", "top"], [g["name"] for g in got])
    check("psd native pixels", np.array_equal(got[0]["rgba"], a) and np.array_equal(got[1]["rgba"], b))


# 10) 效果按块应用（参与区域失效）：分块 == 整图
left, right = (0, 0, 63, 127), (64, 0, 127, 127)
full_im2 = bx.impasto_rgba(big)
tiled_im = bx.apply_effect_tiled(big, [left, right], lambda c, ox, oy: bx.impasto_rgba(c), pad=2)
check("tiled impasto == full", np.array_equal(tiled_im, full_im2))

# 11) PSD RLE 压缩通道
def _mk_rle_psd():
    import struct as _s
    W, H = 4, 2
    planes = {0: 50, 1: 60, 2: 70, -1: 255}
    row = lambda v: bytes([3, v, v, v, v])
    ch = b""
    for v in planes.values():
        rb = row(v)
        ch += _s.pack(">H", 1) + _s.pack(">HH", len(rb), len(rb)) + rb + rb
    rec = _s.pack(">iiii", 0, 0, H, W) + _s.pack(">H", 4)
    for cid in planes:
        rec += _s.pack(">hI", cid, 2 + H * 2 + 10)
    rec += b"8BIM" + b"norm" + bytes([255, 0, 0, 0])
    nm = b"rle"
    nm_p = bytes([len(nm)]) + nm
    extra_ = _s.pack(">II", 0, 0) + nm_p
    rec += _s.pack(">I", len(extra_)) + extra_
    li = _s.pack(">h", -1) + rec + ch
    lm = _s.pack(">I", len(li)) + li + _s.pack(">I", 0)
    head = b"8BPS" + _s.pack(">H", 1) + b"\0" * 6 + _s.pack(">HIIHH", 4, H, W, 8, 3)
    return head + _s.pack(">II", 0, 0) + _s.pack(">I", len(lm)) + lm + _s.pack(">H", 0)

rle = _mk_rle_psd()
got_rle = bx._psd_parse_layers(rle)
check("psd rle layer", len(got_rle) == 1 and got_rle[0]["name"] == "rle", [g["name"] for g in got_rle])
check("psd rle pixels", np.array_equal(got_rle[0]["rgba"][0, :, 0], np.array([50, 50, 50, 50], np.uint8)) and
      int(got_rle[0]["rgba"][0, 0, 3]) == 255)

print("RESULT", "OK" if ok else "FAILED")
sys.exit(0 if ok else 1)
