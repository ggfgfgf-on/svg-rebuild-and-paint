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

print("RESULT", "OK" if ok else "FAILED")
sys.exit(0 if ok else 1)
