"""GPU 后端回归（自包含）：tile 重绘逐位一致 + 与 CPU 的已知差异量级。"""
import sys
import numpy as np
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import paint_native, paint_gpu_native as gpn, plan_store, tile_inc

W, H = 200, 160
SPECS = [("a", "normal"), ("b", "normal")]
FIELDS = ("pts", "offs", "params", "rids", "sid")
REGION = np.zeros((H, W), np.int32)
ok = True


def check(label, cond, extra=""):
    global ok
    ok = ok and bool(cond)
    print(("PASS " if cond else "FAIL ") + label + ("  " + extra if extra else ""))


def make_plan(seed=5):
    rng = np.random.default_rng(seed)
    plan = {}
    for name, _ in SPECS:
        strokes = []
        for _ in range(50):
            x, y = float(rng.uniform(12, W - 12)), float(rng.uniform(12, H - 12))
            pts = np.array([[x, y, 0.6],
                            [x + float(rng.uniform(-12, 12)), y + float(rng.uniform(-12, 12)), 0.9]],
                           np.float32)
            strokes.append({"points": pts, "size": float(rng.uniform(5, 14)), "opacity": 0.85,
                            "color": (int(rng.integers(40, 255)), int(rng.integers(40, 255)),
                                      int(rng.integers(40, 255))), "hardness": 0.7, "preset_id": 0})
        plan[name] = dict(zip(FIELDS, plan_store.pack(strokes)))
    return plan


def gpu_painter():
    p = gpn.GpuPainter(W, H)
    p.add_layer("normal")                 # pg_create 不建层：补一张背景，之后下标与 CPU 对齐
    p.clear_layer(0, (18, 18, 18), 1.0)
    for _, bl in SPECS:
        p.add_layer(bl)
    return p


def cpu_painter():
    p = paint_native.Painter(W, H)
    p.clear_layer(0, (18, 18, 18), 1.0)  # paint_create 自带 layer 0（背景），不要再 add_layer
    for _, bl in SPECS:
        p.add_layer(bl)
    return p


def main():
    plan = make_plan()

    g = gpu_painter()
    tile_inc.full(g, plan, SPECS, lambda nm: REGION, gpu=True, nthreads=4)
    gpu_full = np.ascontiguousarray(g.composite())
    g.close()

    g2 = gpu_painter()
    tile_inc.full(g2, plan, SPECS, lambda nm: REGION, gpu=True, nthreads=4)
    tile_inc.rebuild(g2, plan, SPECS, {"a": [(20, 20, 90, 90)]}, lambda nm: REGION, gpu=True, nthreads=4)
    gpu_tile = np.ascontiguousarray(g2.composite())
    g2.close()

    c = cpu_painter()
    tile_inc.full(c, plan, SPECS, lambda nm: REGION, gpu=False, nthreads=4)
    cpu_full = np.ascontiguousarray(c.composite())

    check("gpu tile rebuild == gpu full", np.array_equal(gpu_tile, gpu_full))

    d = np.abs(gpu_full.astype(int) - cpu_full.astype(int))
    mean_d = float(d.mean())
    cov_g = int((gpu_full[..., 3] > 8).sum())
    cov_c = int((cpu_full[..., 3] > 8).sum())
    rel = abs(cov_g - cov_c) / max(1, cov_c)
    check("gpu vs cpu mean diff small", mean_d < 1.0, "mean=%.4f" % mean_d)
    check("gpu vs cpu coverage close", rel < 0.02, "cpu=%d gpu=%d rel=%.4f" % (cov_c, cov_g, rel))
    check("gpu renders content", cov_g > 5000, "px=%d" % cov_g)

    print("RESULT", "OK" if ok else "FAILED")


if __name__ == "__main__":
    main()
