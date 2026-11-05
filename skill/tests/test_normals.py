"""法线 helper 的不变量：dome 解析、gradient 场、采样。跑法：python tests/test_normals.py"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE / "scripts"))

import numpy as np
import ops
import plan_io


def main():
    doc, w, h = plan_io.load_plan(str(HERE / "examples" / "demo.plan.json"))
    doc = ops.apply(doc, [{"op": "split_shading"}])
    targets = list(ops._targets(doc, None, None, None, 1.0))
    cx, cy, rx, ry = ops._dome_frame(targets)
    assert rx > 0 and ry > 0

    n0 = ops._dome_normal(cx, cy, cx, cy, rx, ry)
    assert abs(n0[2] - 1.0) < 1e-12, n0            # 中心法线朝观察者
    nL = ops._dome_normal(cx - rx * 0.5, cy, cx, cy, rx, ry)
    nR = ops._dome_normal(cx + rx * 0.5, cy, cx, cy, rx, ry)
    assert nL[0] < 0 < nR[0]                        # 左右法线相反
    for n in (n0, nL, nR):
        assert abs(np.linalg.norm(n) - 1.0) < 1e-12

    fields = ops._normal_fields(targets, w, h, 9)
    nx, ny, nz, cover = fields
    assert nx.shape == (h, w) and ny.shape == (h, w) and nz.shape == (h, w)
    nrm = np.sqrt(nx * nx + ny * ny + nz * nz)
    assert np.allclose(nrm, 1.0, atol=1e-9)
    assert cover.any()

    s = ops._normal_sample(fields, 10, 20, w, h)
    assert len(s) == 3 and all(np.isfinite(v) for v in s)
    f2 = ops._normal_fields(targets, w, h, 9)
    assert all(np.array_equal(a, b) for a, b in zip(fields, f2))

    print("dome + gradient normals: ok")
    print("RESULT OK")


if __name__ == "__main__":
    main()