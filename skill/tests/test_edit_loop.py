"""edit_loop 的性质断言（不锁像素数值）。

- 受影响的层由 ops 推出来（layer=None 视为全部层）
- 既没有视觉提示也没有掩膜文件时必须报错，不能静默改全图
- 掩膜模式透传正确
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import edit_loop as EL

ok = True
def check(name, cond, extra=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name + (" " + str(extra) if extra else ""))
    ok = ok and bool(cond)

doc = {"layers": [{"name": n} for n in ("bed", "band", "ink", "highlight")]}
check("layer op touches only that layer", EL._touched(doc, [{"op": "x", "layer": "band"}]) == {"band"})
check("global op touches all layers",
      EL._touched(doc, [{"op": "x"}]) == {"bed", "band", "ink", "highlight"})
check("union of layer ops",
      EL._touched(doc, [{"op": "x", "layer": "bed"}, {"op": "y", "layer": "ink"}]) == {"bed", "ink"})

try:
    EL.run("nope.plan.json", [{"op": "tone_map", "gain": 1.1}], "out", seeds=None, mask_file=None)
    check("no seeds and no mask raises", False)
except SystemExit as e:
    check("no seeds and no mask raises", "seeds" in str(e))

import numpy as np
x = np.array([[[8, 18, 16]]], np.uint8)
y = np.array([[[15, 27, 16]]], np.uint8)
check("uint8 wrap guard: zero change -> 0", float(EL._delta(x, x).mean()) == 0.0)
check("uint8 wrap guard: real delta sane", abs(float(EL._delta(y, x).mean()) - (7 + 9 + 0) / 3) < 1e-6,
      float(EL._delta(y, x).mean()))
b = np.zeros((20, 30), bool); b[5:12, 4:20] = True
m = __import__("masks").from_bool(b)
check("full mask roundtrip", bool((EL._full_mask(m, 20, 30) == b).all()))

print("RESULT", "OK" if ok else "FAILED")
sys.exit(0 if ok else 1)