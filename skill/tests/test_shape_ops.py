"""shape_ops（新增 fill_polygon / tapered_stroke）的契约测试。

    python tests/test_shape_ops.py

断言：
  * fill_polygon 只在多边形内撒点，产物是普通笔触，可渲染且不露底
  * fill_polygon 同输入两次逐字节相同，guidance=0 不改 doc
  * tapered_stroke 的笔径从 size 单调收到 size_end，沿 path 落笔
  * 两个 helper 都不改原有 20 个算子；只往 doc 追加笔触
  * dump_plan 可用 layer_order 显式层序（含空层）
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import numpy as np
import ops
import plan_io
import render_util as RU
import shape_ops


def empty_doc():
    return {"canvas": [200, 200], "layers": [], "strokes": []}


def test_fill_polygon():
    poly = [[40, 40], [160, 40], [160, 160], [40, 160]]
    doc = shape_ops.fill_polygon(empty_doc(), "body", poly, color="#c08060",
                                 size=12.0, opacity=1.0, preset="round_hard",
                                 spacing=5.0, jitter=0.2)
    assert len(doc["strokes"]) > 100, len(doc["strokes"])
    assert [l["name"] for l in doc["layers"]] == ["body"]
    for s in doc["strokes"]:
        assert s["layer"] == "body"
        for x, y, p in s["points"]:
            assert 30 <= x <= 170 and 30 <= y <= 170, (x, y)
    # 可渲染、不露底（中心 alpha 必须满）
    with tempfile.TemporaryDirectory() as td:
        plan = Path(td) / "fill.plan.json"
        plan_io.dump_plan(str(plan), 200, 200, doc["strokes"], layer_blends={"body": "normal"})
        arr = RU.render_arr(plan_io.load_plan(str(plan))[0], gpu=False, nthreads=4)
        assert arr.shape[:2] == (200, 200)
        assert float(arr[100, 100, 3]) >= 254, float(arr[100, 100, 3])
    # 确定性
    doc2 = shape_ops.fill_polygon(empty_doc(), "body", poly, color="#c08060",
                                  size=12.0, opacity=1.0, preset="round_hard",
                                  spacing=5.0, jitter=0.2)
    assert ops.canonical(doc) == ops.canonical(doc2)
    print("PASS fill_polygon %d strokes, deterministic, opaque center" % len(doc["strokes"]))


def test_fill_guidance_zero():
    doc = empty_doc()
    before = ops.canonical(doc)
    shape_ops.fill_polygon(doc, "body", [[40, 40], [160, 40], [160, 160]], guidance=0.0)
    assert ops.canonical(doc) == before
    print("PASS fill_polygon guidance=0 keeps doc unchanged")


def test_tapered_stroke():
    doc = shape_ops.tapered_stroke(empty_doc(), "antler", "M 80 170 C 100 110 150 70 180 30",
                                   color="#d8c090", size=14.0, size_end=1.0,
                                   steps=24, preset="round_hard")
    assert len(doc["strokes"]) == 24, len(doc["strokes"])
    sizes = [s["size"] for s in doc["strokes"]]
    assert abs(sizes[0] - 14.0) < 0.8, sizes[0]
    assert sizes[-1] < 3.0, sizes[-1]
    assert all(a >= b for a, b in zip(sizes, sizes[1:])), sizes
    assert [l["name"] for l in doc["layers"]] == ["antler"]
    doc2 = shape_ops.tapered_stroke(empty_doc(), "antler", "M 80 170 C 100 110 150 70 180 30",
                                    color="#d8c090", size=14.0, size_end=1.0,
                                    steps=24, preset="round_hard")
    assert ops.canonical(doc) == ops.canonical(doc2)
    print("PASS tapered_stroke %d segments, size %.2f -> %.2f, deterministic" % (len(sizes), sizes[0], sizes[-1]))


def test_fill_holes_and_pressure():
    outer = [[20, 20], [180, 20], [180, 180], [20, 180]]
    hole = [[80, 80], [120, 80], [120, 120], [80, 120]]
    doc = shape_ops.fill_polygon(empty_doc(), "body", outer, holes=[hole],
                                 color="#c08060", size=5.0, spacing=5.0,
                                 jitter=0.0, edge=False, pressure=0.4,
                                 preset="round_hard")
    assert doc["strokes"]
    for s in doc["strokes"]:
        assert all(abs(p - 0.4) < 1e-9 for _, _, p in s["points"]), s["points"]
        x, y = s["points"][0][:2]
        assert not (80 < x < 120 and 80 < y < 120), (x, y)
    with tempfile.TemporaryDirectory() as td:
        plan = Path(td) / "holes.plan.json"
        plan_io.dump_plan(str(plan), 200, 200, doc["strokes"], layer_blends={"body": "normal"})
        arr = RU.render_arr(plan_io.load_plan(str(plan))[0], gpu=False, nthreads=4)
        assert float(arr[100, 100, 3]) < 16, float(arr[100, 100, 3])
        x, y = doc["strokes"][0]["points"][0][:2]
        assert float(arr[int(y), int(x), 3]) > 50, float(arr[int(y), int(x), 3])
    print("PASS fill_polygon holes + pressure, %d strokes" % len(doc["strokes"]))


def test_taper_pressure():
    doc = shape_ops.tapered_stroke(empty_doc(), "antler", "M 80 170 L 180 30",
                                   size=10.0, size_end=2.0, steps=12,
                                   pressure=1.0, pressure_end=0.2)
    ps = [s["points"][0][2] for s in doc["strokes"]]
    assert abs(ps[0] - 1.0) < 0.15, ps
    assert ps[-1] < ps[0], ps
    assert all(a >= b for a, b in zip(ps, ps[1:])), ps
    doc2 = shape_ops.tapered_stroke(empty_doc(), "antler", "M 80 170 L 180 30",
                                    size=10.0, size_end=2.0, steps=12,
                                    pressure=1.0, pressure_end=0.2)
    assert ops.canonical(doc) == ops.canonical(doc2)
    print("PASS tapered_stroke pressure %.2f -> %.2f, deterministic" % (ps[0], ps[-1]))


def test_fill_gradient_and_hardness():
    doc = shape_ops.fill_polygon(empty_doc(), "body", [[40, 40], [160, 40], [160, 160], [40, 160]],
                                 color="#ff0000", color2="#0000ff", size=10.0,
                                 opacity=1.0, preset="round_hard", spacing=6.0,
                                 jitter=0.0, edge=False, hardness=0.2)
    assert len(doc["strokes"]) > 100
    assert all(s["hardness"] == 0.2 for s in doc["strokes"])
    colors = [s["color"] for s in doc["strokes"]]
    assert colors[0] != colors[-1], (colors[0], colors[-1])
    assert colors[0][0] > colors[-1][0], (colors[0], colors[-1])
    print("PASS fill_polygon color2 gradient + hardness, %d strokes" % len(doc["strokes"]))


def test_dump_layer_order():
    strokes = [
        {"layer": "top", "preset": "round_hard", "size": 4.0, "opacity": 1.0,
         "color": [255, 0, 0], "hardness": 1.0, "points": [[5, 5, 1.0]]},
        {"layer": "bottom", "preset": "round_hard", "size": 4.0, "opacity": 1.0,
         "color": [0, 0, 255], "hardness": 1.0, "points": [[5, 5, 1.0]]},
    ]
    with tempfile.TemporaryDirectory() as td:
        plan = Path(td) / "order.plan.json"
        plan_io.dump_plan(str(plan), 10, 10, strokes,
                          layer_blends={"empty": "screen"},
                          layer_order=["empty", "bottom", "top"])
        doc = json.loads(plan.read_text(encoding="utf-8"))
        assert [l["name"] for l in doc["layers"]] == ["empty", "bottom", "top"]
        assert doc["layers"][0]["blend"] == "screen"
    print("PASS dump_plan explicit layer_order + empty layer")


def test_assemble_parts():
    parts = [
        {"name": "near", "z": 2, "polygon": [[110, 40], [160, 40], [160, 120], [110, 120]],
         "color": [220, 80, 60]},
        {"name": "far", "z": 0, "polygon": [[30, 50], [80, 50], [80, 130], [30, 130]],
         "color": [60, 90, 220], "value": -1},
    ]
    doc = shape_ops.assemble_parts(empty_doc(), "parts", parts, size=8.0,
                                   spacing=6.0, jitter=0.0, edge=False,
                                   far_near_delta=15.0)
    assert doc["strokes"]
    assert all(s["layer"] == "parts" for s in doc["strokes"])
    names = [s.get("tag") for s in doc["strokes"]]
    assert names[0] == "part:far" and names[-1] == "part:near", (names[0], names[-1])
    first = next(s for s in doc["strokes"] if s.get("tag") == "part:far")
    assert first["color"][2] > first["color"][0], first["color"]  # blue-ish far part
    print("PASS assemble_parts z-order + value shift + tags, %d strokes" % len(doc["strokes"]))


def test_taper_copies_and_hardness():
    doc = shape_ops.tapered_stroke(empty_doc(), "antler", "M 80 170 L 180 30",
                                   size=12.0, size_end=2.0, steps=10,
                                   copies=3, spread=2.0, hardness=0.3)
    assert len(doc["strokes"]) == 30, len(doc["strokes"])
    assert all(s["hardness"] == 0.3 for s in doc["strokes"])
    # 三个副本的同一段应互相偏移
    xs = [s["points"][0][0] for s in doc["strokes"][:10]]
    assert max(xs) - min(xs) > 2.5, (min(xs), max(xs))
    print("PASS tapered_stroke copies=3 spread=2, %d strokes" % len(doc["strokes"]))


def test_ribbon():
    poly = shape_ops.ribbon([(20, 100), (180, 100)], 20)
    assert len(poly) == 4, poly
    ys = [pt[1] for pt in poly]
    assert abs(min(ys) - 90) < 1e-9 and abs(max(ys) - 110) < 1e-9, (min(ys), max(ys))
    taper = shape_ops.ribbon([(20, 100), (180, 100)], [4, 12])
    assert abs(abs(taper[0][1] - 100) - 2) < 1e-9, taper[0]
    assert abs(abs(taper[1][1] - 100) - 6) < 1e-9, taper[1]
    print("PASS ribbon centerline+width polygon")


def test_checkpoint():
    doc = shape_ops.fill_polygon(empty_doc(), "base", [[20, 20], [80, 20], [80, 80], [20, 80]],
                                 color=(80, 90, 100), size=8.0, spacing=8.0,
                                 edge=False, seed=3)
    with tempfile.TemporaryDirectory() as td:
        out = plan_io.checkpoint(doc, "draft", td, render=False)
        assert Path(out["plan"]).exists() and out["png"] is None, out
        assert out["strokes"] > 0 and out["layers"] == 1, out
        out2 = plan_io.checkpoint(doc, "final", td, render=True, threads=1)
        assert Path(out2["png"]).exists(), out2
        assert out2["render"]["strokes"] == out2["strokes"], out2
    print("PASS checkpoint dump + optional render")


def test_fill_clip():
    poly = [[40, 40], [160, 40], [160, 160], [40, 160]]
    doc = shape_ops.fill_polygon(empty_doc(), "body", poly, color="#c08060",
                                 size=12.0, opacity=1.0, preset="round_hard",
                                 spacing=4.0, jitter=0.0, clip=True)
    assert doc["strokes"]
    with tempfile.TemporaryDirectory() as td:
        plan = Path(td) / "clip.plan.json"
        plan_io.dump_plan(str(plan), 200, 200, doc["strokes"], layer_blends={"body": "normal"})
        arr = RU.render_arr(plan_io.load_plan(str(plan))[0], gpu=False, nthreads=4)
        assert float(arr[100, 100, 3]) >= 254, float(arr[100, 100, 3])
        assert float(arr[100, 34, 3]) == 0, float(arr[100, 34, 3])
        assert float(arr[100, 166, 3]) == 0, float(arr[100, 166, 3])
    print("PASS fill_polygon clip=True keeps stamps inside", len(doc["strokes"]))


if __name__ == "__main__":
    test_fill_polygon()
    test_fill_guidance_zero()
    test_tapered_stroke()
    test_fill_gradient_and_hardness()
    test_fill_holes_and_pressure()
    test_taper_pressure()
    test_taper_copies_and_hardness()
    test_dump_layer_order()
    test_assemble_parts()
    test_ribbon()
    test_checkpoint()
    test_fill_clip()
    print("all shape_ops tests passed")
