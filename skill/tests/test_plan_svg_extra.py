"""v2 extra: pyd and file-level must preserve unknown metadata."""
import json, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import plan_svg_native as N


def sample():
    return {
        "canvas": [20, 10],
        "subject_layers": ["base"],
        "custom_top": "bar",
        "layers": [{"name": "base", "blend": "normal", "role": "x"}],
        "strokes": [{
            "layer": "base", "preset": "flat", "size": 5.0, "opacity": 1.0,
            "hardness": 0.7, "color": [1, 2, 3],
            "points": [[1, 2, 1.0], [3, 4, 0.5]],
            "region_id": 3, "custom": "foo",
        }],
    }


def check(doc):
    assert doc["subject_layers"] == ["base"]
    assert doc["custom_top"] == "bar"
    assert doc["layers"][0]["role"] == "x"
    assert doc["strokes"][0]["region_id"] == 3
    assert doc["strokes"][0]["custom"] == "foo"


def main():
    doc = sample()
    check(N.unpack_plan(N.pack_plan(doc)))
    print("PASS pyd v2 extra roundtrip")
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "a.plan.json"
        psb = Path(td) / "a.psvb"
        dst = Path(td) / "b.plan.json"
        src.write_text(json.dumps(doc), encoding="utf-8")
        N.pack_plan_file(str(src), str(psb))
        N.unpack_plan_file(str(psb), str(dst))
        check(json.loads(dst.read_text(encoding="utf-8")))
        print("PASS file-level v2 extra roundtrip")
    print("all plan_svg_extra tests passed")


if __name__ == "__main__":
    main()
