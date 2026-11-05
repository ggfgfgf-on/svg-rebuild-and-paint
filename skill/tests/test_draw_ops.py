"""draw_path（从零加笔）与 redraw order（改动优先级）的契约测试。

    python tests/test_draw_ops.py

断言：
  * draw_path 只加笔、不改旧笔；guidance=0 计划逐字节不变；同输入两次逐字节相同
  * 新笔触点落在 path 的 bbox 内；掩膜 bbox 能直接框住它（脏矩形可用）
  * redraw order=coverage/layer/zorder 的优先级与定义一致；未知 order 报错
  * compile_ops.validate_ops 接受合法 draw_path（新层自动建层）、拒绝坏 path；未知层只对非建层算子拒绝
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import numpy as np
import ops
import masks
import compile_ops


def base_doc():
    return {
        "canvas": [200, 200],
        "count": 2,
        "layers": [{"name": "back", "blend": "normal"}, {"name": "front", "blend": "normal"}],
        "strokes": [
            {"layer": "back", "preset": "round_soft", "size": 30.0, "opacity": 0.9,
             "color": [200, 50, 50], "points": [[20, 20, 1.0], [120, 120, 1.0]]},
            {"layer": "front", "preset": "round_soft", "size": 3.0, "opacity": 0.9,
             "color": [50, 200, 50], "points": [[20, 20, 1.0], [24, 24, 1.0]]},
        ],
    }


def canon(doc):
    return ops.canonical(doc)


def test_draw_path_basic():
    doc = base_doc()
    out = ops.apply(doc, [{"op": "draw_path", "layer": "front", "d": "M 40 40 L 140 60 Q 160 100 120 150",
                           "color": "#d9b45a", "size": 9.0, "opacity": 0.8}])
    assert len(out["strokes"]) == 3, len(out["strokes"])
    s = out["strokes"][-1]
    assert s["layer"] == "front" and len(s["points"]) > 4
    assert s["color"] == [217.0, 180.0, 90.0], s["color"]
    assert s["preset"] == "round_soft"
    xs = [p[0] for p in s["points"]]
    ys = [p[1] for p in s["points"]]
    assert min(xs) >= 39 and max(xs) <= 161, (min(xs), max(xs))
    assert min(ys) >= 39 and max(ys) <= 151, (min(ys), max(ys))
    # 旧笔触逐字节不变
    for a, b in zip(doc["strokes"], out["strokes"][:2]):
        assert a == b
    print("PASS draw_path adds 1 stroke (%d pts) on front, color normalized, old strokes untouched" % len(s["points"]))


def test_draw_path_invariance_and_determinism():
    doc = base_doc()
    spec = [{"op": "draw_path", "layer": "front", "d": "M 10 10 L 90 90", "guidance": 0.0}]
    assert canon(ops.apply(doc, spec)) == canon(doc), "guidance=0 must keep the plan byte-identical"
    once = ops.apply(doc, [{"op": "draw_path", "layer": "front", "d": "M 10 10 L 90 90", "size": 7, "opacity": 0.6}])
    twice = ops.apply(doc, [{"op": "draw_path", "layer": "front", "d": "M 10 10 L 90 90", "size": 7, "opacity": 0.6}])
    assert canon(once) == canon(twice), "draw_path must be deterministic"
    half = ops.apply(doc, [{"op": "draw_path", "layer": "front", "d": "M 10 30 L 90 30", "size": 7, "opacity": 0.6, "guidance": 0.5}])
    assert abs(half["strokes"][-1]["opacity"] - 0.3) < 1e-6, half["strokes"][-1]["opacity"]
    grown = ops.apply(doc, [{"op": "draw_path", "layer": "nope", "d": "M 1 1 L 2 2",
                            "blend": "screen"}])
    names = [l["name"] for l in grown["layers"]]
    assert names[-1] == "nope" and grown["layers"][-1]["blend"] == "screen", grown["layers"]
    assert grown["strokes"][-1]["layer"] == "nope"
    print("PASS draw_path: guidance=0 identical, repeat identical, guidance=0.5 halves opacity, new layer auto-created")


def test_draw_path_dirty_rect():
    doc = base_doc()
    out = ops.apply(doc, [{"op": "draw_path", "layer": "front", "d": "M 50 50 L 150 50",
                           "size": 10.0, "opacity": 1.0}])
    idx = [len(out["strokes"]) - 1]
    box = masks.bbox(out, idx)
    x0, y0, x1, y1 = box
    assert x0 <= 46 and x1 >= 154 and y0 <= 46 and y1 >= 54, box
    print("PASS draw_path dirty rect %s covers path (size inflated)" % (tuple(round(v, 1) for v in box),))


def test_redraw_order():
    doc = base_doc()
    # coverage: 大的先改（back 那笔 bbox 更大）
    r = ops.apply(doc, [{"op": "redraw", "fraction": 0.5, "jitter": 0.2, "order": "coverage"}])
    assert r["strokes"][0] != doc["strokes"][0], "biggest stroke should be redrawn first"
    assert r["strokes"][1] == doc["strokes"][1]
    # layer: back 先改
    r2 = ops.apply(doc, [{"op": "redraw", "fraction": 0.5, "jitter": 0.2, "order": "layer"}])
    assert r2["strokes"][0] != doc["strokes"][0] and r2["strokes"][1] == doc["strokes"][1]
    # zorder: 后面画的先改
    r3 = ops.apply(doc, [{"op": "redraw", "fraction": 0.5, "jitter": 0.2, "order": "zorder"}])
    assert r3["strokes"][1] != doc["strokes"][1] and r3["strokes"][0] == doc["strokes"][0]
    # 确定性
    r4 = ops.apply(doc, [{"op": "redraw", "fraction": 0.5, "jitter": 0.2, "order": "coverage"}])
    assert canon(r) == canon(r4)
    try:
        ops.apply(doc, [{"op": "redraw", "fraction": 0.5, "order": "bogus"}])
        raise AssertionError("bad order accepted")
    except ValueError:
        pass
    print("PASS redraw order: coverage=biggest / layer=first layer / zorder=topmost, deterministic, bad order rejected")


def test_validate_ops():
    doc = base_doc()
    compile_ops.validate_ops(doc, [{"op": "draw_path", "layer": "front", "d": "M 0 0 L 10 10", "size": 4.0}])
    compile_ops.validate_ops(doc, [{"op": "draw_path", "layer": "ghost", "d": "M 0 0 L 1 1"}])   # 新层放行
    compile_ops.validate_ops(doc, [{"op": "add_layer", "name": "glow", "blend": "screen"}])
    for bad in ({"op": "draw_path", "layer": "front", "d": ""},
                {"op": "draw_path", "layer": "front", "d": "M 0 0 L 1 1", "size": 0},
                {"op": "add_layer"},
                {"op": "set_layer_brightness", "layer": "ghost", "factor": 1.2},
                {"op": "redraw", "order": "bogus"}):
        try:
            compile_ops.validate_ops(doc, [bad])
            raise AssertionError("bad op accepted: %r" % (bad,))
        except ValueError:
            pass
    print("PASS validate_ops: valid draw_path & new layer & add_layer accepted; empty d / size<=0 / add_layer without name / unknown layer on non-building op / bad order rejected")


def main():
    with tempfile.TemporaryDirectory() as td:
        test_draw_path_basic()
        test_draw_path_invariance_and_determinism()
        test_draw_path_dirty_rect()
        test_redraw_order()
        test_validate_ops()
    print("RESULT OK")


if __name__ == "__main__":
    main()
