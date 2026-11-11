"""plan <-> SVG bridge contract tests."""
import base64
import json
import sys
import zlib
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


def test_svgz_output(tmp_path):
    import gzip
    doc, w, h = plan_io.load_plan(str(Path(__file__).parent.parent / "examples" / "demo.plan.json"))
    p = tmp_path / "demo.svgz"
    info = plan_svg.plan_to_svgz(doc, str(p))
    assert info["gz_bytes"] > 0 and info["gz_bytes"] < info["svg_bytes"]
    with gzip.open(p, "rt", encoding="utf-8") as f:
        assert "<svg" in f.read(200)


def test_dump_cli(tmp_path):
    src = Path(tmp_path) / "a.plan.json"
    svg = Path(tmp_path) / "a.svg"
    dst = Path(tmp_path) / "b.plan.json"
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




def test_clip_evenodd_line():
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 20">'
           '<defs><clipPath id="c" clip-rule="evenodd">'
           '<rect x="0" y="0" width="60" height="20"/>'
           '<rect x="20" y="0" width="60" height="20"/>'
           '</clipPath></defs>'
           '<path d="M0 10 L100 10" stroke="#f00" stroke-width="2" clip-path="url(#c)"/>'
           '</svg>')
    d = plan_svg.svg_to_plan(svg)
    segs = [(x["points"][0][0], x["points"][-1][0]) for x in d["strokes"]]
    assert segs == [(0.0, 20.0), (60.0, 80.0)]




def test_clip_evenodd_fill_hole():
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
           '<defs><clipPath id="c" clip-rule="evenodd">'
           '<rect x="0" y="0" width="100" height="100"/>'
           '<rect x="25" y="25" width="50" height="50"/>'
           '</clipPath></defs>'
           '<rect width="100" height="100" fill="#f00" clip-path="url(#c)"/>'
           '</svg>')
    d = plan_svg.svg_to_plan(svg)
    assert len(d["strokes"]) == 1
    st = d["strokes"][0]
    assert st["fill_rule"] == "evenodd"
    assert len(st["holes"]) == 1
    assert isinstance(st["mask"], dict)
    out = plan_svg.plan_to_svg(d)
    assert 'fill-rule="evenodd"' in out
    path_d = out.split('<path d="', 1)[1].split('"', 1)[0]
    assert path_d.count("Z") == 2




def test_radial_focal_roundtrip():
    d = {"canvas": [100, 100], "layers": [{"name": "layer", "blend": "normal"}],
         "strokes": [{"layer": "layer", "preset": "fill", "opacity": 1.0,
                      "paint": {"type": "radial", "units": "objectBoundingBox",
                                "cx": 0.5, "cy": 0.5, "r": 0.5, "fx": 0.25, "fy": 0.5,
                                "stops": [[0, [0, 0, 0], 1], [1, [255, 255, 255], 1]]},
                      "points": [[0, 0, 1], [100, 0, 1], [100, 100, 1], [0, 100, 1]]}]}
    svg = plan_svg.plan_to_svg(d)
    assert 'fx="0.25"' in svg
    back = plan_svg.svg_to_plan(svg)
    assert abs(float(back["strokes"][0]["paint"]["fx"]) - 0.25) < 1e-6


def test_clip_path_units_bbox():
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 50">'
           '<defs><clipPath id="c" clipPathUnits="objectBoundingBox">'
           '<rect x="0" y="0" width="0.5" height="1"/></clipPath></defs>'
           '<rect x="30" y="10" width="40" height="20" fill="#f00" clip-path="url(#c)"/>'
           '</svg>')
    d = plan_svg.svg_to_plan(svg)
    xs = [p[0] for p in d["strokes"][0]["points"]]
    assert min(xs) >= 30 - 1e-6 and max(xs) <= 50 + 1e-6


def test_mask_type_alpha():
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 50">'
           '<defs><mask id="m" mask-type="alpha">'
           '<rect width="50" height="50" fill="#ff0000"/></mask></defs>'
           '<rect width="100" height="50" fill="#0000ff" mask="url(#m)"/></svg>')
    d = plan_svg.svg_to_plan(svg)
    m = d["layers"][0]["mask"]
    raw = zlib.decompress(base64.b64decode(m["data"]))
    assert raw[20 * m["width"] + 20] == 255




def test_gradient_transform_roundtrip():
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
           '<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="0"'
           ' gradientTransform="rotate(90 0.5 0.5)">'
           '<stop offset="0" stop-color="#000"/><stop offset="1" stop-color="#fff"/>'
           '</linearGradient></defs>'
           '<rect width="100" height="100" fill="url(#g)"/></svg>')
    d = plan_svg.svg_to_plan(svg)
    src = d["strokes"][0]["paint"]["source"]["gradient"]["attrs"]
    assert "rotate(90" in src["gradientTransform"]
    out = plan_svg.plan_to_svg(d)
    assert "gradientTransform" in out
    d2 = plan_svg.svg_to_plan(out)
    assert "rotate(90" in d2["strokes"][0]["paint"]["source"]["gradient"]["attrs"]["gradientTransform"]




def test_text_roundtrip():
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 50">'
           '<text x="10" y="30" font-family="Arial" font-size="20" fill="#ff0000">Hi</text>'
           '</svg>')
    d = plan_svg.svg_to_plan(svg)
    st = d["strokes"][0]
    assert st["preset"] == "text"
    assert st["text"] == "Hi"
    assert st["font_family"] == "Arial"
    assert st["font_size"] == 20
    out = plan_svg.plan_to_svg(d)
    assert "<text" in out and 'font-family="Arial"' in out




def test_pattern_roundtrip():
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 50">'
           '<defs><pattern id="p" width="10" height="10" patternUnits="userSpaceOnUse">'
           '<rect width="5" height="5" fill="#ff0000"/></pattern></defs>'
           '<rect width="100" height="50" fill="url(#p)"/></svg>')
    d = plan_svg.svg_to_plan(svg)
    st = d["strokes"][0]
    assert st.get("pattern") and st["pattern"].get("data")
    out = plan_svg.plan_to_svg(d)
    assert "<pattern" in out and ("url(#pat" in out or "url(#pt" in out)




def test_filter_rasterized():
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
           '<defs><filter id="f"><feGaussianBlur stdDeviation="4"/></filter></defs>'
           '<rect x="30" y="30" width="40" height="40" fill="#ff0000" filter="url(#f)"/>'
           '</svg>')
    d = plan_svg.svg_to_plan(svg)
    assert d["strokes"][0]["preset"] == "image"
    out = plan_svg.plan_to_svg(d)
    assert "<image" in out




def test_radial_transform_roundtrip():
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
           '<defs><radialGradient id="g" cx="0.5" cy="0.5" r="0.25"'
           ' gradientTransform="rotate(45 0.5 0.5)">'
           '<stop offset="0" stop-color="#000"/><stop offset="1" stop-color="#fff"/>'
           '</radialGradient></defs>'
           '<rect width="100" height="100" fill="url(#g)"/></svg>')
    d = plan_svg.svg_to_plan(svg)
    src = d["strokes"][0]["paint"]["source"]["gradient"]["attrs"]
    assert "rotate(45" in src["gradientTransform"]
    out = plan_svg.plan_to_svg(d)
    assert "gradientTransform" in out




def test_stream_svg_basic():
    import svg_stream
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 50">'
           '<defs><rect width="10" height="10" fill="#0f0"/></defs>'
           '<g data-name="layer">'
           '<rect x="10" y="10" width="30" height="20" fill="#f00"/>'
           '<path d="M60 25 L90 25" stroke="#00f" stroke-width="4"/>'
           '</g></svg>')
    d = svg_stream.stream_svg_to_plan(svg)
    assert d["canvas"] == [100, 50]
    assert len(d["strokes"]) == 2
    assert d["strokes"][0]["preset"] == "fill"




def test_stream_svg_use():
    import svg_stream
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 50">'
           '<defs><rect id="r" width="20" height="20" fill="#f00"/></defs>'
           '<use href="#r" x="30" y="10"/></svg>')
    d = svg_stream.stream_svg_to_plan(svg)
    assert len(d["strokes"]) == 1
    assert d["strokes"][0]["points"][0][:2] == [30.0, 10.0]


def test_stream_evenodd_holes():
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
           '<path fill="#f00" fill-rule="evenodd" '
           'd="M0 0 L100 0 L100 100 L0 100 Z M25 25 L75 25 L75 75 L25 75 Z"/></svg>')
    d = plan_svg.svg_to_plan(svg)
    assert len(d["strokes"]) == 1
    assert len(d["strokes"][0]["holes"]) == 1


def test_stream_evenodd_disjoint():
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
           '<path fill="#0f0" fill-rule="evenodd" '
           'd="M0 0 L40 0 L40 40 L0 40 Z M60 60 L100 60 L100 100 L60 100 Z"/></svg>')
    d = plan_svg.svg_to_plan(svg)
    assert len(d["strokes"]) == 1
    assert len(d["strokes"][0]["subpaths"]) == 2
    assert not d["strokes"][0].get("holes")


def test_stream_group_inheritance():
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
           '<g fill="#f00"><rect x="10" y="10" width="20" height="20"/></g></svg>')
    d = plan_svg.svg_to_plan(svg)
    assert len(d["strokes"]) == 1
    assert d["strokes"][0]["color"] == [255.0, 0.0, 0.0]


def test_multi_subpath_roundtrip():
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
           '<path fill="#0f0" fill-rule="evenodd" '
           'd="M0 0 L40 0 L40 40 L0 40 Z M60 60 L100 60 L100 100 L60 100 Z"/></svg>')
    d = plan_svg.svg_to_plan(svg)
    out = plan_svg.plan_to_svg(d)
    assert out.count("<path") == 1
    d2 = plan_svg.svg_to_plan(out)
    assert len(d2["strokes"]) == 1
    assert len(d2["strokes"][0]["subpaths"]) == 2


def test_native_parse_d_subpaths():
    import plan_svg_native as n
    if not n.available():
        return
    subs = n.parse_d_subpaths("M0 0 L10 0 L10 10 Z M20 20 L30 20 L30 30 Z")
    assert len(subs) == 2
    assert subs[0][0] == [0.0, 0.0]


def test_stream_file_incremental(tmp_path):
    import svg_stream
    p = tmp_path / "big.svg"
    p.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
                 '<path fill="#f00" d="M0 0 L10 0 L10 10 Z M20 20 L30 20 L30 30 Z"/>'
                 '</svg>', encoding="utf-8")
    d = svg_stream.stream_svg_to_plan(str(p))
    assert len(d["strokes"]) == 1
    assert len(d["strokes"][0]["subpaths"]) == 2
    assert len(d["strokes"][0]["points"]) >= 3


def test_path_stream_parser_equivalence():
    import svg_stream
    d = ('M0 0 C10 0 10 10 20 10 Q30 10 30 20 L40 20 Z '
         'M50 50 A5 5 0 0 1 60 60 Z')
    ps = svg_stream._PathStreamParser()
    for i in range(0, len(d), 3):
        ps.feed(d[i:i + 3])
    ps.finish()
    ref = [q for q in plan_svg._parse_d_subpaths(d) if len(q) >= 3]
    assert len(ps.out) == len(ref)
    for a, b in zip(ps.out, ref):
        assert len(a) == len(b)
        for p, q in zip(a, b):
            assert abs(p[0] - q[0]) < 1e-6 and abs(p[1] - q[1]) < 1e-6


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
    test_clip_evenodd_line()
    test_clip_evenodd_fill_hole()
    test_radial_focal_roundtrip()
    test_clip_path_units_bbox()
    test_mask_type_alpha()
    test_gradient_transform_roundtrip()
    test_text_roundtrip()
    test_pattern_roundtrip()
    test_stream_svg_basic()
    test_stream_svg_use()
    test_stream_evenodd_holes()
    test_stream_evenodd_disjoint()
    test_filter_rasterized()
    test_radial_transform_roundtrip()
    test_gradient_paint_roundtrip()
    test_mask_layer_alpha()
    print("all plan_svg tests passed")
