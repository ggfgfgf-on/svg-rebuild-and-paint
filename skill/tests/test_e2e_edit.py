"""端到端验收：编译产物 -> 编辑 -> 只重算脏矩形 == 整份重画（逐位）。

不依赖模型，也不依赖外部素材：用合成计划 + 固定 ops.json。
"""
import json
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import compile_ops
import masks
import ops
import ops_incremental_demo as D
import plan_io
import tile_inc

W, H = 180, 140
OPS = [
    {"op": "set_layer_hue", "layer": "petal", "hue": 210, "guidance": 0.9},
    {"op": "densify", "layer": "petal", "k": 1.3},
]


def make_doc(path, seed=5):
    rng = np.random.default_rng(seed)
    strokes = []
    for layer, n, base in (("under", 30, (60, 70, 90)), ("petal", 30, (200, 60, 70))):
        for k in range(n):
            x = float(rng.uniform(12, W - 12)) if layer == "petal" else float(rng.uniform(12, W / 2))
            y = float(rng.uniform(12, H - 12))
            strokes.append({"layer": layer, "preset": "round_hard", "size": float(rng.uniform(5, 12)),
                            "opacity": 0.9,
                            "color": [base[0] + int(rng.integers(-20, 20)), base[1], base[2]],
                            "hardness": 0.7,
                            "points": [[x, y, 0.6], [x + float(rng.uniform(-5, 5)), y + float(rng.uniform(-5, 5)), 0.9]]})
    plan_io.dump_plan(path, W, H, strokes, {"under": "normal", "petal": "normal"})
    return plan_io.load_plan(path)[0]


def main():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        doc = make_doc(tmp / "p.json")
        compile_ops.validate_ops(doc, OPS)

        p, cat, w, h = D.new_painter(doc)
        plan1, spec = D.doc_to_plan(doc, cat)
        region = np.zeros((h, w), np.int32)
        tile_inc.full(p, plan1, spec, lambda nm: region, gpu=False, nthreads=2)
        base = np.ascontiguousarray(p.composite())

        mask = masks.rect(10, 10, 90, 80)
        sel, wt = masks.weights(doc, mask, feather=12, layer="petal")
        assert sel
        out = ops.apply(doc, OPS, selection=sel, weights=wt)
        assert ops.fingerprint(ops.apply(doc, OPS, selection=sel, weights=wt)) == ops.fingerprint(out)

        touched = {"petal"}
        plan2, spec2 = D.doc_to_plan(out, cat, base=plan1, only=touched)
        box = masks.bbox(doc, sel)
        x0, y0, x1, y1 = [int(round(v)) for v in box]
        dirty = {"petal": [(max(0, x0), max(0, y0), min(w - 1, x1), min(h - 1, y1))]}
        tile_inc.rebuild(p, plan2, spec2, dirty, lambda nm: region, gpu=False, nthreads=2)
        inc = np.ascontiguousarray(p.composite())
        p.close()

        q, _, _, _ = D.new_painter(doc)
        tile_inc.full(q, plan2, spec2, lambda nm: region, gpu=False, nthreads=2)
        ref = np.ascontiguousarray(q.composite())
        q.close()

        assert np.array_equal(inc, ref), "incremental != full rebuild (%d px)" % int(np.any(inc != ref, axis=2).sum())
        changed = np.any(base != inc, axis=2)
        ys, xs = np.nonzero(changed)
        assert len(xs) > 0, "edit produced no change"
        outside = int(((xs < dirty["petal"][0][0]) | (xs > dirty["petal"][0][2]) |
                       (ys < dirty["petal"][0][1]) | (ys > dirty["petal"][0][3])).sum())
        assert outside == 0, "%d px changed outside dirty rect" % outside
        print("PASS e2e: ops=%d selected=%d changed=%d outside=%d, incremental == full (bitwise)"
              % (len(OPS), len(sel), len(xs), outside))
    print("RESULT OK")


if __name__ == "__main__":
    main()
