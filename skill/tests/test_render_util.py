"""render_util 契约：亮度口径、单层渲染、笔触发送三条路径都稳定且确定性。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import numpy as np
from PIL import Image

import plan_io
import render_util as RU


def test_luma():
    assert abs(RU.luma_of(np.full((4, 4, 3), 255.0)) - 255.0) < 1e-9
    assert abs(RU.luma_of(np.full((4, 4, 4), 0.0))) < 1e-9
    # 纯绿 255 -> 0.7152*255
    g = np.zeros((2, 2, 3)); g[..., 1] = 255.0
    assert abs(RU.luma_of(g) - 0.7152 * 255.0) < 1e-9
    print("PASS luma_of: white/black/pure-green")


def test_target_image(tmp):
    p = Path(tmp) / "ref.png"
    Image.new("RGB", (8, 8), (0, 255, 0)).save(p)
    assert abs(RU.target_luma_from_image(p) - 0.7152 * 255.0) < 1.0
    print("PASS target_luma_from_image: green 8x8")


def make_doc(path, w=32, h=32):
    strokes = []
    for y in range(0, h, 8):
        for x in range(0, w, 8):
            strokes.append({"layer": "bed", "preset": "round_soft", "size": 12.0, "opacity": 1.0,
                            "color": [90, 40, 30], "points": [[x + 4, y + 4, 1.0]]})
    plan_io.dump_plan(str(path), w, h, strokes, layer_blends={"bed": "normal"})
    return plan_io.load_plan(path)


def test_render_paths(tmp):
    doc, w, h = make_doc(Path(tmp) / "p.json")
    arr = RU.render_arr(doc)
    assert arr.shape[:2] == (h, w) and arr.ndim == 3
    l1 = RU.render_luma(doc)
    l2 = RU.render_luma(doc)
    assert l1 == l2, "render_luma must be deterministic"
    top = RU.render_layer_rgba(doc, "bed")
    assert top[..., 3].max() == 255
    empty = RU.render_layer_rgba(doc, "missing")
    assert empty[..., 3].max() == 0, "absent layer must render empty"
    rgba = RU.strokes_to_rgba(doc["strokes"], w, h)
    assert rgba[..., 3].max() == 255
    rgba2 = RU.strokes_to_rgba(doc["strokes"], w, h)
    assert np.array_equal(rgba, rgba2), "strokes_to_rgba must be deterministic"
    print("PASS render paths: arr %.0fx%.0f luma %.1f, layer alpha 255, empty alpha 0, strokes deterministic"
          % (arr.shape[1], arr.shape[0], l1))


def main():
    import tempfile
    test_luma()
    with tempfile.TemporaryDirectory() as td:
        test_target_image(td)
        test_render_paths(td)
    print("RESULT OK")


if __name__ == "__main__":
    main()
