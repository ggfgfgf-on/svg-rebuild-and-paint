"""生成压力校准表 brush.json，并给出可验证的准确性指标。

表 = 渲染器笔尖模型在 33 个压力点上的采样（半径比 r、峰值覆盖 a）。
渲染时 C++ 用线性插值查表，因此"查表渲染"与"原始公式渲染"应当像素一致——
verify_roundtrip() 就是这条判据；表的数值可以随时被实测/风格拟合值替换，C++ 不用改。
"""
import json, sys
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import paint_native

N_POINTS = 33
PRES = np.linspace(0.0, 1.0, N_POINTS)
TIPS = {"round_soft": 1.0, "round_hard": 1.0, "flat": 1.0, "dry": 1.0, "airbrush": 0.5,
        "liner": 1.0, "chalk": 0.6, "sponge": 0.7, "bristle": 0.8, "marker": 0.98}


def model(preset):
    """与 paint_core.cpp 的 emit() 完全同构：压力 -> (半径比, 覆盖)。"""
    r = 0.30 + 0.70 * PRES
    a = 0.15 + 0.85 * PRES
    return r, a


def build():
    out = {}
    for name in paint_native.PRESETS:
        r, a = model(name)
        # 沉积量 = 单点笔的 alpha 总量（等价"这一笔留下多少墨"），按压力采样
        dep = []
        for pv in PRES:
            pt = paint_native.Painter(160, 160)
            pt.add_layer()
            pt.clear_layer(0, (0, 0, 0), 0.0)
            pt.stroke(0, np.array([[80, 80, float(pv)]], np.float32), 60.0, 1.0,
                      (255, 255, 255), preset=name, hardness=0.6)
            al = pt.layer_rgba(0)[:, :, 3].astype(np.float64)
            pt.close()
            dep.append(round(float(al.sum() / 255.0) / (60.0 * 60.0), 4))   # 覆盖当量 / 面积
        out[name] = {"r": [round(float(v), 4) for v in r],
                     "a": [round(float(v), 4) for v in a],
                     "dep": dep}

    dst = Path(__file__).resolve().parent / "native" / "brush.json"
    dst.write_text(json.dumps(out, indent=1), encoding="utf-8")
    return dst


def verify_roundtrip(preset="round_soft", pressure=0.6, size=80.0, canvas=320):
    """查表 vs 原始曲线：同一笔的像素差。"""
    def render(lut):
        p = paint_native.Painter(canvas, canvas)
        p.add_layer()
        p.clear_layer(0, (0, 0, 0), 0.0)
        p.stroke(0, np.array([[canvas / 2, canvas / 2, pressure]], np.float32), size, 1.0,
                 (255, 255, 255), preset=preset, hardness=0.6, lut=lut)
        arr = p.layer_rgba(0)[:, :, 3].astype(np.int16)
        p.close()
        return arr

    base = render(None)
    table = json.loads((Path(__file__).resolve().parent / "native" / "brush.json")
                       .read_text(encoding="utf-8"))
    cal = render(table[preset])
    d = np.abs(base - cal)
    return {"preset": preset, "pressure": pressure,
            "maxdiff": int(d.max()), "mean": round(float(d.mean()), 4)}


def _smoothstep(e0, e1, x):
    t = min(1.0, max(0.0, (x - e0) / (e1 - e0))) if e1 != e0 else (1.0 if x >= e1 else 0.0)
    return t * t * (3.0 - 2.0 * t)


def _tip(d, R, h, preset):
    """与 C++ tip() 同构（不含 flat/dry 的纹理项）。"""
    if d >= R:
        return 0.0
    x = d / R
    if preset == "round_hard":
        return 1.0 if x <= 0.94 else 1.0 - _smoothstep(0.94, 1.0, x)
    if preset == "airbrush":
        b = 1.0 - _smoothstep(0.15, 1.0, x)
        return b * b * 0.5
    core = h
    return 1.0 if x <= core else 1.0 - _smoothstep(core, 1.0, x)


def _half_radius_factor(preset, h):
    """半高半径 / R：从同一公式二分求出。"""
    peak = _tip(0.0, 1.0, h, preset)
    lo, hi = 0.0, 1.0
    for _ in range(40):
        mid = (lo + hi) / 2
        if _tip(mid, 1.0, h, preset) > peak * 0.5:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def measure_accuracy(size=80.0, canvas=320, pressures=(0.25, 0.5, 0.75, 1.0), h=0.6):
    """实测半高半径与中心覆盖 vs 表预测（只用径向笔头；flat 是椭圆、dry 带噪点不参与）。"""
    table = json.loads((Path(__file__).resolve().parent / "native" / "brush.json")
                       .read_text(encoding="utf-8"))
    rows = []
    for name in ("round_soft", "round_hard", "airbrush"):
        k = _half_radius_factor(name, h)
        r_lut, a_lut = table[name]["r"], table[name]["a"]
        for pv in pressures:
            fx = pv * (N_POINTS - 1)
            i0 = int(fx); i1 = min(i0 + 1, N_POINTS - 1); fr = fx - i0
            pr = r_lut[i0] * (1 - fr) + r_lut[i1] * fr
            pa = a_lut[i0] * (1 - fr) + a_lut[i1] * fr
            pt = paint_native.Painter(canvas, canvas)
            pt.add_layer()
            pt.clear_layer(0, (0, 0, 0), 0.0)
            pt.stroke(0, np.array([[canvas / 2, canvas / 2, pv]], np.float32), size, 1.0,
                      (255, 255, 255), preset=name, hardness=h)
            a = pt.layer_rgba(0)[:, :, 3].astype(np.float64)
            pt.close()
            peak = a.max()
            ys, xs = np.nonzero(a >= peak * 0.5)
            d = np.hypot(xs - canvas / 2, ys - canvas / 2)
            r_meas = float(d.max())
            r_pred = size / 2.0 * pr * k
            rows.append({"preset": name, "p": pv,
                         "r_err_px": round(abs(r_meas - r_pred), 2),
                         "r_err_pct": round(abs(r_meas - r_pred) / max(r_pred, 1e-6) * 100, 2),
                         "peak_err_pct": round(abs(peak / 255.0 - pa * TIPS[name]) /
                                               max(pa * TIPS[name], 1e-6) * 100, 2)})
    return rows


if __name__ == "__main__":
    dst = build()
    print("saved", dst, "points", N_POINTS)
    for pr in ("round_soft", "flat", "airbrush"):
        print(verify_roundtrip(pr))
    rows = measure_accuracy()
    print("measured vs table: max r err %.2f%% (%.2f px), max peak err %.2f%%" % (
        max(r["r_err_pct"] for r in rows), max(r["r_err_px"] for r in rows),
        max(r["peak_err_pct"] for r in rows)))
