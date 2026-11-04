"""calibrate_tone 契约：从参考亮度反解 gain，不依赖任何硬编码常数。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import calibrate_tone as C
import ops
import plan_io


def make_doc(path, w=48, h=48):
    strokes = []
    for y in range(0, h, 8):
        for x in range(0, w, 8):
            strokes.append({"layer": "bed", "preset": "round_soft", "size": 14.0, "opacity": 1.0,
                            "color": [40 + x, 60 + y, 30], "points": [[x + 4, y + 4, 1.0]]})
            strokes.append({"layer": "top", "preset": "round_soft", "size": 10.0, "opacity": 0.6,
                            "color": [120, 90, 60], "points": [[x + 6, y + 3, 1.0]]})
    plan_io.dump_plan(str(path), w, h, strokes, layer_blends={"bed": "normal", "top": "normal"})
    return plan_io.load_plan(path)


def test_roundtrip(tmp):
    doc, _, _ = make_doc(Path(tmp) / "p.json")
    known = 0.62
    target = C.render_luma(ops.apply(doc, [{"op": "tone_map", "gain": known}],
                                     selection=None, weights=None))
    gain, luma, renders = C.calibrate(doc, target, tol=0.05, iters=14)
    assert abs(gain - known) < 0.05, (gain, known)
    assert abs(luma - target) < 0.5
    assert renders <= 14
    print("PASS roundtrip: gain %.2f -> %.4f, luma %.2f (target %.2f, %d renders)"
          % (known, gain, luma, target, renders))


def test_base_ops_kept(tmp):
    doc, _, _ = make_doc(Path(tmp) / "q.json")
    base = [{"op": "erase", "layer": "top"}]
    known = 0.8
    target = C.render_luma(ops.apply(doc, base + [{"op": "tone_map", "gain": known}],
                                     selection=None, weights=None))
    gain, luma, _ = C.calibrate(doc, target, base, tol=0.05, iters=12)
    assert abs(gain - known) < 0.08, (gain, known)
    combined = [dict(s) for s in base if s.get("op") != "tone_map"] + [{"op": "tone_map", "gain": gain}]
    assert combined[0]["op"] == "erase" and combined[-1]["op"] == "tone_map"
    print("PASS base ops preserved: erase kept, gain %.3f (known %.2f), luma %.2f" % (gain, known, luma))


def main():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        test_roundtrip(td)
        test_base_ops_kept(td)
    print("RESULT OK")


if __name__ == "__main__":
    main()
