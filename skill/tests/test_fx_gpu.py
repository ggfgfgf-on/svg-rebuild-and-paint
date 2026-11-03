"""GPU compute 版 smudge/impasto 与 CPU 版的一致性 + 性能（自包含）。"""
import sys
import time
import numpy as np
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import brush_ext as bx
import paint_fx_native as fx

ok = True


def check(label, cond, extra=""):
    global ok
    ok = ok and bool(cond)
    print(("PASS " if cond else "FAIL ") + label + ("  " + extra if extra else ""))


def make_img(h, w, seed=3):
    rng = np.random.default_rng(seed)
    a = np.zeros((h, w, 4), np.float32)
    yy, xx = np.mgrid[0:h, 0:w]
    blob = ((xx - w * 0.5) ** 2 + (yy - h * 0.5) ** 2) < (min(h, w) * 0.35) ** 2
    a[blob, 0] = 0.85
    a[blob, 1] = 0.3
    a[blob, 2] = 0.22
    a[blob, 3] = 0.9
    a[..., :3] += rng.random((h, w, 3)).astype(np.float32) * 0.06 * a[..., 3:4]
    return np.clip(a, 0, 1).astype(np.float32)


def main():
    if not fx.available():
        print("SKIP paint_fx.dll unavailable")
        print("RESULT", "OK")
        return

    h, w = 96, 128
    img = make_img(h, w)
    path = np.array([[26, 48, 1.0], [100, 52, 1.0]], np.float32)

    cpu_sm = bx.smudge_rgba(img, path, radius=14, amount=0.7, hardness=0.5)
    gpu_sm = fx.smudge_rgba_gpu(img, path, radius=14, amount=0.7, hardness=0.5)
    d_sm = np.abs(cpu_sm - gpu_sm)
    check("smudge gpu finite", np.isfinite(gpu_sm).all())
    check("smudge gpu == cpu (float32 noise)", float(d_sm.max()) < 2e-5,
          "max=%.3e mean=%.3e" % (float(d_sm.max()), float(d_sm.mean())))
    check("smudge keeps alpha semantics", np.array_equal(cpu_sm[..., 3] > 0, gpu_sm[..., 3] > 0))

    cpu_im = bx.impasto_rgba(img)
    gpu_im = fx.impasto_rgba_gpu(img)
    d_im = np.abs(cpu_im - gpu_im)
    check("impasto gpu finite", np.isfinite(gpu_im).all())
    check("impasto gpu == cpu (float32 noise)", float(d_im.max()) < 2e-5,
          "max=%.3e mean=%.3e" % (float(d_im.max()), float(d_im.mean())))
    check("impasto keeps alpha", np.array_equal(cpu_im[..., 3], gpu_im[..., 3]))
    check("impasto changes rgb", float(np.abs(cpu_im[..., :3] - img[..., :3]).max()) > 0.05)

    # 确定性
    gpu_im2 = fx.impasto_rgba_gpu(img)
    check("gpu deterministic", np.array_equal(gpu_im, gpu_im2))

    # 性能（相对 CPU 的加速比）
    for size in (1024, 2048, 4096):
        big = make_img(size, size, seed=9)
        t0 = time.perf_counter(); bx.impasto_rgba(big); t_cpu = time.perf_counter() - t0
        t0 = time.perf_counter(); fx.impasto_rgba_gpu(big); t_gpu = time.perf_counter() - t0
        bpath = np.array([[size * 0.2, size * 0.6, 1.0], [size * 0.8, size * 0.6, 1.0]], np.float32)
        t0 = time.perf_counter(); bx.smudge_rgba(big, bpath, radius=24, amount=0.7); ts_cpu = time.perf_counter() - t0
        t0 = time.perf_counter(); fx.smudge_rgba_gpu(big, bpath, radius=24, amount=0.7); ts_gpu = time.perf_counter() - t0
        print("size %d  impasto cpu=%.3fs gpu=%.3fs (%.1fx)  smudge cpu=%.3fs gpu=%.3fs (%.1fx)"
              % (size, t_cpu, t_gpu, t_cpu / max(t_gpu, 1e-9), ts_cpu, ts_gpu, ts_cpu / max(ts_gpu, 1e-9)))

    print("RESULT", "OK" if ok else "FAILED")


if __name__ == "__main__":
    main()
