"""plan <-> SVG bridge contract tests."""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import ops
import plan_io
import plan_svg


def sample():
    return {
        "canvas": [120, 80],
        "layers": [
            {"name": "base", "blend": "normal"},
            {"name": "empty", "blend": "screen"},
            {"name": "light", "blend": "screen"},
        ],
        "strokes": [
            {"layer": "base", "preset": "flat", "size": 9.0, "opacity": 1.0,
             "color": [30.0, 40.0, 50.0], "hardness": 0.9,
             "points": [[10, 10, 1.0], [100, 20, 0.6]]},
            {"layer": "light", "preset": "round_soft", "size": 5.0, "opacity": 0.4,
             "color": [240, 180, 90], "hardness": 0.3, "tag": "glint",
             "points": [[20, 60, 1.0], [40, 55, 0.8], [70, 65, 0.9]]},
        ],
    }


def test_roundtrip():
    doc = sample()
    svg = plan_svg.plan_to_svg(doc)
    back = plan_svg.svg_to_plan(svg)
    assert back["canvas"] == [120, 80], back["canvas"]
    assert [l["name"] for l in back["layers"]] == ["base", "empty", "light"], back["layers"]
    assert [l["blend"] for l in back["layers"]] == ["normal", "screen", "screen"], back["layers"]
    assert len(back["strokes"]) == 2
    a, b = doc["strokes"], back["strokes"]
    for x, y in zip(a, b):
        assert x["layer"] == y["layer"]
        assert x["preset"] == y["preset"]
        assert abs(x["size"] - y["size"]) < 1e-6
        assert abs(x["opacity"] - y["opacity"]) < 1e-6
        assert abs(x["hardness"] - y["hardness"]) < 1e-6
        assert [round(v, 3) for v in x["color"]] == [round(v, 3) for v in y["color"]]
        assert len(x["points"]) == len(y["points"])
        assert [[round(v, 3) for v in p] for p in x["points"]] == [[round(v, 3) for v in p] for p in y["points"]]
    assert b[1].get("tag") == "glint"
    assert "data-preset" in svg
    print("PASS plan->svg->plan roundtrip, layers/blends/tags/pressure")


def test_deterministic():
    doc = sample()
    a = plan_svg.plan_to_svg(doc)
    b = plan_svg.plan_to_svg(doc)
    assert a == b
    print("PASS plan->svg deterministic")


def test_dump_cli(tmp):
    src = Path(tmp) / "a.plan.json"
    svg = Path(tmp) / "a.svg"
    dst = Path(tmp) / "b.plan.json"
    doc = sample()
    plan_io.dump_plan(str(src), 120, 80, doc["strokes"],
                      layer_blends={l["name"]: l["blend"] for l in doc["layers"]},
                      layer_order=[l["name"] for l in doc["layers"]])
    info = plan_svg.dump_plan_svg(str(src), str(svg))
    assert info["strokes"] == 2 and svg.exists()
    info2 = plan_svg.load_svg_plan(str(svg), str(dst))
    assert info2["strokes"] == 2 and dst.exists()
    doc2, w, h = plan_io.load_plan(str(dst))
    assert [l["name"] for l in doc2["layers"]] == ["base", "empty", "light"]
    print("PASS dump/load files, %d bytes SVG" % len(svg.read_text(encoding="utf-8")))




def test_outline_and_stamps_roundtrip():
    doc = {
        "canvas": [200, 120],
        "layers": [{"name": "ink", "blend": "normal"}],
        "strokes": [{
            "layer": "ink", "preset": "dry", "size": 9, "opacity": 0.8,
            "hardness": 0.6, "color": [12, 34, 56],
            "points": [[10, 20, 1.0], [50, 30, 0.5], [90, 25, 0.8]],
            "tag": "t1",
        }],
    }
    for svg in (plan_svg.plan_to_svg_outline(doc), plan_svg.plan_to_svg_stamps(doc)):
        back = plan_svg.svg_to_plan(svg)
        assert len(back["strokes"]) == 1
        st = back["strokes"][0]
        assert st["size"] == 9
        assert st["preset"] == "dry"
        assert st["color"] == [12, 34, 56]
        assert st["points"] == doc["strokes"][0]["points"]
        assert st.get("tag") == "t1"


def test_transform_and_arc():
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
           '<g id="g" transform="translate(10,20)">'
           '<path d="M 0 0 L 10 0" stroke="#ff0000" stroke-width="5"/></g></svg>')
    d = plan_svg.svg_to_plan(svg)
    assert d["strokes"][0]["points"] == [[10.0, 20.0, 1.0], [20.0, 20.0, 1.0]]
    svg2 = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
            '<path d="M 10 10 A 20 20 0 0 1 50 10" stroke="#00ff00" stroke-width="3"/></svg>')
    pts = plan_svg.svg_to_plan(svg2)["strokes"][0]["points"]
    assert len(pts) == 17
    assert abs(pts[-1][0] - 50) < 1e-6 and abs(pts[-1][1] - 10) < 1e-6


if __name__ == "__main__":
    test_roundtrip()
    test_deterministic()
    with tempfile.TemporaryDirectory() as td:
        test_dump_cli(td)
    test_outline_and_stamps_roundtrip()
    test_transform_and_arc()
    print("all plan_svg tests passed")
