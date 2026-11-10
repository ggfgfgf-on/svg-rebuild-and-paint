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
        assert [[round(v, 5) for v in p] for p in st["points"]] == [[round(v, 5) for v in p] for p in doc["strokes"][0]["points"]]
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




def test_shapes_css_use_gradient():
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
           '<defs><linearGradient id="g"><stop offset="0" stop-color="#ff0000"/>'
           '<stop offset="1" stop-color="#0000ff"/></linearGradient>'
           '<path id="p" d="M 0 0 L 10 0"/></defs>'
           '<style>.k{stroke:#00ff00;stroke-width:4}</style>'
           '<use href="#p" x="10" y="20" transform="translate(5,5)"/>'
           '<rect class="k" x="50" y="50" width="10" height="10"/>'
           '<circle cx="20" cy="20" r="5" fill="url(#g)"/></svg>')
    d = plan_svg.svg_to_plan(svg)
    assert len(d["strokes"]) == 3
    assert d["strokes"][0]["points"][0] == [15.0, 25.0, 1.0]
    assert d["strokes"][1]["size"] == 4 and d["strokes"][1]["color"] == [0, 255, 0]
    c = d["strokes"][2]["color"]
    assert abs(c[0] - 127.5) <= 1 and c[1] == 0 and abs(c[2] - 127.5) <= 1


def test_native_fallback_metadata():
    import plan_svg_native as N
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
           '<path d="M 1 1 L 2 2" data-center="1,1,1;2,2,1" data-d-hash="wrong" '
           'stroke="#ff0000" stroke-width="2"/></svg>')
    pts = N.svg_to_plan(svg)["strokes"][0]["points"]
    assert pts[0] == [1.0, 1.0, 1.0]




def test_clip_fill_multi_subpath():
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
           '<defs><clipPath id="c"><rect x="0" y="0" width="50" height="100"/></clipPath></defs>'
           '<path clip-path="url(#c)" d="M 0 0 L 100 0" stroke="#ff0000" stroke-width="2"/>'
           '<rect x="0" y="0" width="100" height="20" fill="#00ff00" clip-path="url(#c)"/>'
           '<path d="M 0 50 L 10 50 M 20 60 L 30 60" stroke="#0000ff" stroke-width="2"/>'
           '</svg>')
    d = plan_svg.svg_to_plan(svg)
    assert len(d["strokes"]) == 4
    assert d["strokes"][0]["points"][-1][0] == 50.0
    assert d["strokes"][1]["preset"] == "fill"
    assert len(d["strokes"][2]["points"]) == 2 and len(d["strokes"][3]["points"]) == 2




def test_gradient_paint_roundtrip():
    doc = {"canvas": [100, 100], "layers": [{"name": "l"}],
           "strokes": [{"layer": "l", "preset": "fill", "color": [128, 0, 128],
                        "points": [[0, 0], [10, 0], [10, 10]],
                        "paint": {"type": "linear", "units": "objectBoundingBox",
                                  "stops": [[0, [255, 0, 0], 1], [1, [0, 0, 255], 1]],
                                  "x1": "0%", "y1": "0%", "x2": "100%", "y2": "0%"}}]}
    svg = plan_svg.plan_to_svg(doc)
    assert "linearGradient" in svg and "url(#paint0)" in svg
    back = plan_svg.svg_to_plan(svg)
    assert back["strokes"][0]["paint"]["stops"][0][1] == [255, 0, 0]
    assert back["strokes"][0]["paint"]["stops"][1][1] == [0, 0, 255]




def test_mask_layer_alpha():
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 50">'
           '<defs><mask id="m"><rect x="0" y="0" width="50" height="50" fill="white"/></mask></defs>'
           '<rect x="0" y="0" width="100" height="50" fill="#ff0000" mask="url(#m)"/></svg>')
    d = plan_svg.svg_to_plan(svg)
    assert any(isinstance(x.get("mask"), dict) and x["mask"].get("encoding") in ("png-base64", "zlib-base64")
               for x in d["layers"])


if __name__ == "__main__":
    test_roundtrip()
    test_deterministic()
    with tempfile.TemporaryDirectory() as td:
        test_dump_cli(td)
    test_outline_and_stamps_roundtrip()
    test_transform_and_arc()
    test_shapes_css_use_gradient()
    test_native_fallback_metadata()
    test_clip_fill_multi_subpath()
    test_gradient_paint_roundtrip()
    test_mask_layer_alpha()
    print("all plan_svg tests passed")
