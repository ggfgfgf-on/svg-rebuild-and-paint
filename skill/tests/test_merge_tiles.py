"""merge_canvas_tiles 契约：tile 合成 1 笔、覆盖全画布、其它层不动、混合模式保留。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import plan_io
import merge_canvas_tiles as M


def make_plan(path):
    strokes = [
        {"layer": "bed", "preset": "round_soft", "size": 20.0, "opacity": 1.0,
         "color": [10, 20, 30], "points": [[5, 5, 1.0]]},
        {"layer": "texture", "preset": "02_rough-canvas", "size": 96.0, "opacity": 0.3,
         "color": [80, 40, 20], "points": [[50, 50, 1.0]]},
        {"layer": "texture", "preset": "02_rough-canvas", "size": 96.0, "opacity": 0.3,
         "color": [100, 60, 40], "points": [[150, 50, 1.0]]},
    ]
    plan_io.dump_plan(str(path), 200, 160, strokes, layer_blends={"bed": "normal", "texture": "multiply"})


def test_merge(tmp):
    src, out = Path(tmp) / "a.json", Path(tmp) / "b.json"
    make_plan(src)
    n, w, h = M.merge_tiles(src, out)
    assert n == 2 and (w, h) == (200, 160)
    doc, _, _ = plan_io.load_plan(out)
    tex = [s for s in doc["strokes"] if s["layer"] == "texture"]
    assert len(tex) == 1, tex
    assert tex[0]["size"] >= (200 ** 2 + 160 ** 2) ** 0.5, tex[0]["size"]
    assert tex[0]["points"][0][:2] == [100.0, 80.0]
    assert abs(tex[0]["color"][0] - 90.0) < 0.01, tex[0]["color"]
    assert [s for s in doc["strokes"] if s["layer"] == "bed"]
    assert {l["name"]: l["blend"] for l in doc["layers"]}["texture"] == "multiply"
    print("PASS merge: 2 tiles -> 1 stroke size %.0f, color mean %.0f, blends kept" % (
        tex[0]["size"], tex[0]["color"][0]))


def test_empty_layer(tmp):
    p = Path(tmp) / "c.json"
    plan_io.dump_plan(str(p), 10, 10, [{"layer": "bed", "preset": "round_soft", "size": 5.0,
                                       "opacity": 1.0, "color": [1, 2, 3], "points": [[1, 1, 1.0]]}])
    try:
        M.merge_tiles(p, Path(tmp) / "d.json")
    except ValueError as ex:
        print("PASS merge: layer without strokes raises (%s)" % ex)
    else:
        raise AssertionError("expected ValueError")


def main():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        test_merge(td)
        test_empty_layer(td)
    print("RESULT OK")


if __name__ == "__main__":
    main()
