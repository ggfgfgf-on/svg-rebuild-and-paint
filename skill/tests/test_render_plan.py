"""compose/render_plan 契约：多 ops 顺序拼接、与直接 ops.apply 等价、确定性。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import numpy as np
from PIL import Image

import ops
import plan_io
import render_plan as RP
import render_util as RU


def make_doc(path, w=32, h=32):
    strokes = []
    for y in range(0, h, 8):
        for x in range(0, w, 8):
            strokes.append({"layer": "bed", "preset": "round_soft", "size": 12.0, "opacity": 1.0,
                            "color": [90, 40, 30], "points": [[x + 4, y + 4, 1.0]]})
            strokes.append({"layer": "top", "preset": "round_soft", "size": 10.0, "opacity": 0.6,
                            "color": [200, 180, 60], "points": [[x + 6, y + 3, 1.0]]})
    plan_io.dump_plan(str(path), w, h, strokes, layer_blends={"bed": "normal", "top": "normal"})
    return plan_io.load_plan(path)


def test_equivalence(tmp):
    doc, w, h = make_doc(Path(tmp) / "p.json")
    specs = [{"op": "erase", "layer": "top"}, {"op": "tone_map", "gain": 0.8}]
    arr, out = RP.render_doc_with_ops(doc, specs)
    ref = RU.render_arr(ops.apply(doc, specs, selection=None, weights=None))
    assert np.array_equal(arr, ref), "render_doc_with_ops must equal ops.apply + render"
    arr2, _ = RP.render_doc_with_ops(doc, specs)
    assert np.array_equal(arr, arr2), "deterministic"
    print("PASS equivalence: chained ops == ops.apply + render (luma %.2f)" % RU.luma_of(arr))


def test_multi_files_and_png(tmp):
    doc = make_doc(Path(tmp) / "q.json")[0]
    a = Path(tmp) / "a.json"; b = Path(tmp) / "b.json"
    a.write_text('[{"op": "erase", "layer": "top"}]', encoding="utf-8")
    b.write_text('[{"op": "tone_map", "gain": 0.5}]', encoding="utf-8")
    specs = RP.load_specs([a, b])
    assert [s["op"] for s in specs] == ["erase", "tone_map"]
    info = RP.render_plan_with_ops(Path(tmp) / "q.json", specs, Path(tmp) / "o.png")
    im = Image.open(Path(tmp) / "o.png")
    assert im.size == (32, 32)
    assert info["ops"] == 2 and info["luma"] > 0
    print("PASS multi ops files + png: %s luma %.2f" % (im.size, info["luma"]))


def main():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        test_equivalence(td)
        test_multi_files_and_png(td)
    print("RESULT OK")


if __name__ == "__main__":
    main()
