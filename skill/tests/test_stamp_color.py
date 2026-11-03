"""逐 stamp 上色验收（CPU 后端）。

不变式：
  * amount=0 与旧行为逐位一致（开启后只改 RGB，覆盖率 alpha 完全不变）
  * 同一笔内部不同 stamp 颜色不同（不是把整笔拆成多笔）
  * 确定性：同样的输入两次渲染逐位相同
  * 均值接近原色（不整体偏色）
"""
import sys
import numpy as np
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import paint_native

W, H = 160, 120
COLOR = (200, 80, 40)
ok = True


def check(label, cond, extra=""):
    global ok
    ok = ok and bool(cond)
    print(("PASS " if cond else "FAIL ") + label + ("  " + extra if extra else ""))


def render(amount):
    p = paint_native.Painter(W, H)
    p.add_layer("normal")
    p.set_dynamics(0.0, 0.0, 0.0, 0.0, 0.0)
    p.set_color_dynamics(amount)
    pts = np.stack([np.linspace(20, 140, 20), np.full(20, 60.0), np.full(20, 1.0)], -1).astype(np.float32)
    par = np.array([[26.0, 1.0, COLOR[0] / 255.0, COLOR[1] / 255.0, COLOR[2] / 255.0, 0.7, 1.0, 0.0]],
                   np.float32)
    offs = np.array([0, pts.size], np.int32)
    p.stroke_batch_raw(1, pts, offs, par, np.zeros(1, np.int32), nthreads=1)
    return np.ascontiguousarray(p.composite())


def main():
    base = render(0.0)
    base2 = render(0.0)
    jit = render(0.4)
    jit2 = render(0.4)

    check("amount=0 deterministic", np.array_equal(base, base2))
    check("jitter deterministic", np.array_equal(jit, jit2))
    check("jitter keeps coverage", np.array_equal(base[..., 3], jit[..., 3]),
          "alpha_max_delta=%d" % int(np.abs(base[..., 3].astype(int) - jit[..., 3].astype(int)).max()))
    check("jitter changes rgb", not np.array_equal(base[..., :3], jit[..., :3]),
          "max_delta=%d" % int(np.abs(base[..., :3].astype(int) - jit[..., :3].astype(int)).max()))

    m = jit[..., 3] > 250
    check("stroke covers area", int(m.sum()) > 800, "px=%d" % int(m.sum()))
    uniq = len({tuple(c) for c in jit[m][:, :3]})
    check("per-stamp color variety", uniq >= 6, "unique=%d" % uniq)

    mb = base[m][:, :3].astype(np.float64).mean(0)
    mj = jit[m][:, :3].astype(np.float64).mean(0)
    dev = float(np.abs(mj - mb).max() / 255.0)
    check("mean color roughly preserved", dev < 0.10, "dev=%.3f(钳位带来轻微上偏)" % dev)
    check("base color matches request", float(np.abs(mb - np.array(COLOR)).max()) <= 2.0,
          "base=%s" % np.round(mb, 1).tolist())

    # amount=0 与「不调用 set_color_dynamics」一致（默认路径不受影响）
    p = paint_native.Painter(W, H)
    p.add_layer("normal")
    pts = np.stack([np.linspace(20, 140, 20), np.full(20, 60.0), np.full(20, 1.0)], -1).astype(np.float32)
    par = np.array([[26.0, 1.0, COLOR[0] / 255.0, COLOR[1] / 255.0, COLOR[2] / 255.0, 0.7, 1.0, 0.0]], np.float32)
    p.stroke_batch_raw(1, pts, np.array([0, pts.size], np.int32), par, np.zeros(1, np.int32), nthreads=1)
    check("default path untouched", np.array_equal(np.ascontiguousarray(p.composite()), base))

    print("RESULT", "OK" if ok else "FAILED")


if __name__ == "__main__":
    main()
