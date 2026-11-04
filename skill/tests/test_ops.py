"""计划空间编辑算子的验收（对应扩散编辑里 CFG / SDEdit / mask 三件事）。

判定标准全是硬的：
  * guidance=0 / 空掩膜 / k=1 / fraction=0  -> 计划逐字节不变
  * selection 之外笔触 -> 字段一个不动
  * 同 (doc, ops, seed) -> 逐字节相同
  * 渲染后：掩膜外的像素差必须为 0（脏矩形之外逐位相同）
"""
import json
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import masks
import ops
import plan_io

W, H = 160, 120
LAYERS = ("under", "petal")
COLORS = [(200, 40, 60), (40, 120, 200), (60, 180, 90), (230, 200, 80), (120, 60, 180)]


def make_doc(path, n_per_layer=24, seed=3):
    rng = np.random.default_rng(seed)
    strokes = []
    for li, layer in enumerate(LAYERS):
        for k in range(n_per_layer):
            x = float(rng.uniform(10, W - 10)) if li == 0 else float(rng.uniform(10, W / 2 - 5))
            y = float(rng.uniform(10, H - 10))
            strokes.append({
                "layer": layer, "preset": "round_hard", "size": float(rng.uniform(4, 10)),
                "opacity": 0.9, "color": list(COLORS[int(rng.integers(0, len(COLORS)))]),
                "hardness": 0.7,
                "points": [[x, y, 0.6], [x + float(rng.uniform(-6, 6)), y + float(rng.uniform(-6, 6)), 0.9]],
            })
    plan_io.dump_plan(path, W, H, strokes, {"under": "normal", "petal": "normal"})
    doc, _, _ = plan_io.load_plan(path)
    return doc


def same(a, b):
    return ops.canonical(a) == ops.canonical(b)


def hue_of(c):
    import colorsys
    r, g, bb = (np.asarray(c, np.float64) / 255.0).tolist()
    return colorsys.rgb_to_hsv(r, g, bb)[0] * 360.0


def layer_ids(doc, layer):
    return [i for i, s in enumerate(doc["strokes"]) if s["layer"] == layer]


def test_invariance(tmp):
    doc = make_doc(tmp / "p.json")
    cases = {
        "hue g=0": ([{"op": "set_layer_hue", "layer": "petal", "hue": 220, "guidance": 0.0}], None),
        "densify k=1": ([{"op": "densify", "layer": "petal", "k": 1.0}], None),
        "redraw f=0": ([{"op": "redraw", "fraction": 0.0}], None),
        "erase empty mask": ([{"op": "erase", "layer": "petal"}], masks.rect(-500, -500, 10, 10)),
        "scale k=1": ([{"op": "scale_stroke_size", "layer": "petal", "k": 1.0}], None),
    }
    for name, (spec, mask) in cases.items():
        sel = wt = None
        if mask is not None:
            sel, wt = masks.weights(doc, mask)
        out = ops.apply(doc, spec, selection=sel, weights=wt)
        assert same(doc, out), "%s: plan changed though it should not" % name
    print("PASS guidance=0 / empty mask / k=1 / fraction=0 keep the plan byte-identical")


def test_hue(tmp):
    doc = make_doc(tmp / "p.json")
    out = ops.apply(doc, [{"op": "set_layer_hue", "layer": "petal", "hue": 200}])
    ids = layer_ids(out, "petal")
    assert len(ids) == 24
    for i in ids:
        assert abs(hue_of(out["strokes"][i]["color"]) - 200.0) < 1.0, (i, hue_of(out["strokes"][i]["color"]))
    for i in layer_ids(out, "under"):
        assert out["strokes"][i] == doc["strokes"][i], "untouched layer changed"
    # guidance=0.5 -> 落在原色与目标之间（不是等于目标）
    half = ops.apply(doc, [{"op": "set_layer_hue", "layer": "petal", "hue": 200, "guidance": 0.5}])
    moved = sum(1 for i in ids if half["strokes"][i]["color"] != doc["strokes"][i]["color"])
    assert moved == 24
    print("PASS hue guidance=1 hits 200 deg on 24/24 strokes; other layer untouched")


def test_mask_and_densify(tmp):
    doc = make_doc(tmp / "p.json")
    m = masks.rect(10, 10, 60, 40)
    sel, wt = masks.weights(doc, m, feather=6, layer="petal")
    assert sel, "mask selected nothing"
    assert all(0.0 < w <= 1.0 for w in wt)
    out = ops.apply(doc, [{"op": "set_layer_hue", "layer": "petal", "hue": 120}],
                    selection=sel, weights=wt)
    changed = {i for i in layer_ids(out, "petal") if out["strokes"][i] != doc["strokes"][i]}
    assert changed == set(sel), "selection leaked: %r" % (changed ^ set(sel))
    k = 1.5
    out2 = ops.apply(doc, [{"op": "densify", "layer": "petal", "k": k}], selection=sel, weights=wt)
    added = len(out2["strokes"]) - len(doc["strokes"])
    assert added == int(round((k - 1.0) * sum(wt))), (added, sum(wt))
    print("PASS mask selects %d strokes (feather ok); hue touches exactly those; densify adds %d"
          % (len(sel), added))


def test_erase_and_redraw(tmp):
    doc = make_doc(tmp / "p.json")
    sel = masks.select(doc, masks.rect(10, 10, 40, 30))
    out = ops.apply(doc, [{"op": "erase"}], selection=sel)
    assert len(out["strokes"]) == len(doc["strokes"]) - len(sel)
    keep = [s for i, s in enumerate(doc["strokes"]) if i not in set(sel)]
    assert out["strokes"] == keep
    n_sel = len(layer_ids(doc, "petal"))
    r1 = ops.apply(doc, [{"op": "redraw", "fraction": 0.5, "jitter": 0.2, "layer": "petal"}])
    changed = sum(1 for i in layer_ids(r1, "petal") if r1["strokes"][i] != doc["strokes"][i])
    assert changed == int(round(0.5 * n_sel)), (changed, n_sel)
    r2 = ops.apply(doc, [{"op": "redraw", "fraction": 0.5, "jitter": 0.2, "layer": "petal"}])
    assert same(r1, r2), "redraw is not deterministic"
    assert all(r1["strokes"][i] == doc["strokes"][i] for i in layer_ids(doc, "under"))
    print("PASS erase removes exactly %d strokes; redraw changes exactly %d/%d and repeats bitwise"
          % (len(sel), changed, n_sel))


def test_render_delta(tmp):
    from PIL import Image
    doc = make_doc(tmp / "p.json")
    m = masks.rect(8, 8, 56, 44)
    sel, wt = masks.weights(doc, m, feather=0)
    assert sel
    out = ops.apply(doc, [{"op": "set_layer_hue", "layer": "petal", "hue": 40},
                          {"op": "set_layer_brightness", "layer": "under", "factor": 1.35}],
                    selection=sel, weights=wt)
    p1 = tmp / "a.json"
    p2 = tmp / "b.json"
    plan_io.dump_plan(p1, W, H, [_stroke_json(s) for s in doc["strokes"]], {"under": "normal", "petal": "normal"})
    plan_io.dump_plan(p2, W, H, [_stroke_json(s) for s in out["strokes"]], {"under": "normal", "petal": "normal"})
    plan_io.render_plan(p1, tmp / "a.png", threads=2)
    t0 = time.perf_counter()
    plan_io.render_plan(p2, tmp / "b.png", threads=2)
    dt = time.perf_counter() - t0
    a1 = np.asarray(Image.open(tmp / "a.png"))
    a2 = np.asarray(Image.open(tmp / "b.png"))
    diff = np.any(a1 != a2, axis=2)
    bx = masks.bbox(doc, sel)
    x0, y0, x1, y1 = bx
    ys, xs = np.nonzero(diff)
    assert len(xs), "nothing changed at all"
    margin = 1
    outside = ((xs < x0 - margin) | (xs > x1 + margin) | (ys < y0 - margin) | (ys > y1 + margin)).sum()
    assert outside == 0, "%d changed pixels outside the dirty rect" % outside
    print("PASS render: %d px changed, all inside dirty rect (%.0f,%.0f)-(%.0f,%.0f), outside=0, %.2fs"
          % (len(xs), x0, y0, x1, y1, dt))


def _stroke_json(s):
    return {"layer": s["layer"], "preset": s["preset"], "size": s["size"], "opacity": s["opacity"],
            "color": s["color"], "hardness": s.get("hardness", 0.7),
            "points": [[p[0], p[1], p[2]] for p in s["points"]]}



def test_sel_keys_complete():
    """回归：resolve_mask 支持的每个来源都必须在 _SEL_KEYS 里。
    漏登记的后果不是报错，而是 ops.apply 解析不到掩膜 → selection=None → 算子静默作用到全图
    （实测用 cells 选叶时 hue 改了 94.58% 画布，应为 5.57%）。
    """
    want = {"mask", "mask_file", "all", "polygon", "instance", "tiles", "cells",
            "prompt", "segment", "include", "exclude"}
    got = set(ops._SEL_KEYS)
    missing = want - got
    assert not missing, "selection 来源漏登记：%s" % sorted(missing)
    print("PASS selection 来源登记完整（%d 个）" % len(got))
    return True

def main():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        test_invariance(tmp)
        test_hue(tmp)
        test_mask_and_densify(tmp)
        test_erase_and_redraw(tmp)
        test_render_delta(tmp)
    print("RESULT OK")


if __name__ == "__main__":
    main()
