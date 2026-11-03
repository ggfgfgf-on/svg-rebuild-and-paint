"""GPU / CPU 后端差异的现状快照（不是"GPU 不可用"，是"部分混合模式未对齐"）。

背景：paint_gpu.dll（DirectCompute）早就实现了，tests/test_gpu_tile.py 与
tests/test_stamp_color.py 也一直守着 normal 混合下的逐位一致。后来发现
**非 normal 的图层混合模式在两端结果不同**，这里把已知差异量级钉住，
免得下次又靠猜测（历史上两次误判都是因为没跑这几个测试）。

出处：用 15a1f06 之后第一个带预编译 DLL 的提交 `558b7a5` 复跑同一探针，
multiply/overlay/softlight 同样分别差 3.09 / 2.57 / 1.12（normal 仍是 0.000）。
所以这不是回归，而是**混合模式从未进入过对拍**：test_gpu_tile 只用了 normal 层，
test_stamp_color 只测单层逐 stamp 色彩。

判据：全部必须逐位/近似逐位一致（mean < 0.5）。

历史：这张探针曾经显示 multiply/screen/overlay/softlight 差 1–3 个灰阶，
当时误判成"GPU 后端有问题"。真正的原因在 **CPU 侧**：`paint_create` 自带 layer 0 作背景，
而 new_painter 又 add_layer("normal")，导致文档里的第 i 层被画进第 i+1 层、混合模式整体错位一位。
GPU 的 `pg_create` 不建隐式层，同样的代码反而对齐——所以当年"GPU 与 CPU 不一致"其实是 CPU 的接线错了。
修掉图层基准后，四种混合模式 mean 全部 = 0.000。
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
    if gpu:
        p.add_layer("normal")          # GPU 没有隐式背景层
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
        check("bitmap tip: gpu == cpu", npx_t == 0 and mean_t < 1e-3,
              "mean=%.6f px>4=%d（个别像素差 1/255，属浮点累加顺序）" % (mean_t, npx_t))

    worst = ("-", 0.0, 0)
    for blend in paint_native.BLENDS:      # 全部 26 种，含 hue/saturation/color/luminosity
        if blend == "normal":
            continue
        m, n = diff(blend)
        check("blend %-12s gpu == cpu" % blend, m < 0.5, "mean=%.3f px>4=%d" % (m, n))
        if m > worst[1]:
            worst = (blend, m, n)
    print("worst blend:", worst)
    print("RESULT", "OK" if ok else "FAILED")


if __name__ == "__main__":
    main()
