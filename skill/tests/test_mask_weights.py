"""笔触权重/掩膜度量的性质断言（不锁像素数值）。

- 折线加密：两点长笔触中间必须有点（否则"穿过掩膜"会被漏判）
- raster + feather：边界附近权重在 (0,1)，深处为 1，外部为 0
- color 掩膜也能转成度量用的像素图
"""
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import edit_loop as EL
import masks

ok = True
def check(name, cond, extra=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name + (" " + str(extra) if extra else ""))
    ok = ok and bool(cond)

pts = np.array([[0.0, 0.0], [100.0, 0.0]])
d = masks._densify(pts, 10.0)
check("densify keeps endpoints", np.allclose(d[0], pts[0]) and np.allclose(d[-1], pts[-1]))
check("densify inserts interior points", len(d) > 3, len(d))
check("densify monotonic x", bool(np.all(np.diff(d[:, 0]) >= 0)))

b = np.zeros((60, 60), bool)
b[20:40, 20:40] = True
m = masks.from_json(masks.to_json(masks.from_bool(b)))
inner = masks.raster_weight(m, [(30, 30)], feather=5.0)[0]
edge = masks.raster_weight(m, [(21, 30)], feather=5.0)[0]
outer = masks.raster_weight(m, [(5, 5)], feather=5.0)[0]
check("raster inner full weight", abs(inner - 1.0) < 1e-6, inner)
check("raster edge feathered", 0.0 < edge < 1.0, edge)
check("raster outside zero", outer == 0.0, outer)
check("raster no feather is binary", masks.raster_weight(m, [(21, 30)])[0] == 1.0)

base = np.zeros((10, 10, 3), np.float64); base[..., 0] = 200.0
cm = {"type": "color", "rgb": [200, 0, 0], "tol": 20}
full = EL._full_mask(cm, 10, 10, base)
check("color mask -> pixel map", bool(full.all()))

print("RESULT", "OK" if ok else "FAILED")
sys.exit(0 if ok else 1)