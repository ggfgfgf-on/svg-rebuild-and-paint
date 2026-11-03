"""GPU / CPU 后端差异的现状快照（不是"GPU 不可用"，是"部分混合模式未对齐"）。

背景：paint_gpu.dll（DirectCompute）早就实现了，tests/test_gpu_tile.py 与
tests/test_stamp_color.py 也一直守着 normal 混合下的逐位一致。后来发现
**非 normal 的图层混合模式在两端结果不同**，这里把已知差异量级钉住，
免得下次又靠猜测（历史上两次误判都是因为没跑这几个测试）。

出处：用 15a1f06 之后第一个带预编译 DLL 的提交 `558b7a5` 复跑同一探针，
multiply/overlay/softlight 同样分别差 3.09 / 2.57 / 1.12（normal 仍是 0.000）。
所以这不是回归，而是**混合模式从未进入过对拍**：test_gpu_tile 只用了 normal 层，
test_stamp_color 只测单层逐 stamp 色彩。

判据：
  normal 混合 + 位图笔尖  -> 必须几乎逐位一致（mean < 0.5）
  非 normal 混合          -> 记录现状；mean > 1.0 记 KNOWN（不 FAIL，但打印）
"""
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE / "scripts"))
import paint_native
import paint_gpu_native as gpn
import plan_store
import tile_inc

W, H = 220, 180
REGION = np.zeros((H, W), np.int32)
FIELDS = ("pts", "offs", "params", "rids", "sid")
ok = True


def check(label, cond, extra=""):
    global ok
    ok = ok and bool(cond)
    print(("PASS " if cond else "FAIL ") + label + ("  " + extra if extra else ""))


def strokes(seed=3):
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(60):
        x, y = float(rng.uniform(12, W - 12)), float(rng.uniform(12, H - 12))
        pts = np.array([[x, y, 0.6],
                        [x + float(rng.uniform(-10, 10)), y + float(rng.uniform(-10, 10)), 0.9]],
                       np.float32)
        out.append({"points": pts, "size": float(rng.uniform(6, 18)), "opacity": 0.85,
                    "color": (int(rng.integers(40, 250)), int(rng.integers(40, 250)),
                              int(rng.integers(40, 250))), "hardness": 0.7})
    return out


def render(blend, gpu, tip=None):
    specs = [("a", blend), ("b", "normal")]
    p = (gpn.GpuPainter if gpu else paint_native.Painter)(W, H)
    p.add_layer("normal")
    p.clear_layer(0, (18, 18, 18), 1.0)
    for _, bl in specs:
        p.add_layer(bl)
    cat = paint_native.load_catalog(p) if tip else {}
    plan = {}
    for name, _ in specs:
        ss = [dict(s, preset_id=(cat[tip] if tip else 0)) for s in strokes()]
        plan[name] = dict(zip(FIELDS, plan_store.pack(ss)))
    tile_inc.full(p, plan, specs, lambda nm: REGION, gpu=gpu, nthreads=4)
    arr = np.ascontiguousarray(p.composite()).copy()
    p.close()
    return arr


def diff(blend, tip=None):
    a = render(blend, False, tip)
    b = render(blend, True, tip)
    d = np.abs(a.astype(np.int32) - b.astype(np.int32))
    return float(d.mean()), int(np.any(d > 4, axis=2).sum())


def main():
    mean, npx = diff("normal")
    check("normal: gpu == cpu", mean < 0.5, "mean=%.3f px>4=%d" % (mean, npx))

    import glob
    import os
    tips = sorted(os.path.basename(p)[:-4] for p in glob.glob(str(HERE / "scripts" / "native" / "tips" / "*.png")))
    if tips:
        mean_t, npx_t = diff("normal", tip=tips[0])
        check("bitmap tip: gpu ~= cpu", mean_t < 0.5, "mean=%.3f px>4=%d" % (mean_t, npx_t))

    known = {}
    for blend in ("multiply", "screen", "overlay", "softlight"):
        m, n = diff(blend)
        known[blend] = (m, n)
        print("INFO %-10s mean=%.3f px>4=%d%s" % (blend, m, n, "  KNOWN-DIVERGENCE" if m > 1.0 else ""))
    check("blend divergence recorded (not silently ignored)", len(known) == 4,
          "worst=%s" % max(known.items(), key=lambda kv: kv[1][0])[0])
    print("RESULT", "OK" if ok else "FAILED")


if __name__ == "__main__":
    main()
