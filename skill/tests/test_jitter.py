"""jitter_strokes 契约：确定性、只动目标层、几何改变但笔数/颜色/大小不变、坐标不再是整数列。"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import plan_io
import jitter_strokes as J


def make_plan(path):
    strokes = []
    for y in range(10):
        strokes.append({"layer": "band", "preset": "round_soft", "size": 6.0, "opacity": 1.0,
                        "color": [20 + y, 10, 10], "points": [[float(x), float(y), 1.0] for x in range(0, 40, 4)]})
    strokes.append({"layer": "bed", "preset": "round_soft", "size": 20.0, "opacity": 1.0,
                    "color": [5, 5, 5], "points": [[2.0, 2.0, 1.0]]})
    plan_io.dump_plan(str(path), 40, 20, strokes, layer_blends={"bed": "normal", "band": "normal"})


def test_deterministic(tmp):
    a, b, out = Path(tmp) / "a.json", Path(tmp) / "b.json", Path(tmp) / "o.json"
    make_plan(a)
    J.jitter_plan(a, Path(tmp) / "j1.json")
    J.jitter_plan(a, Path(tmp) / "j2.json")
    d1 = (Path(tmp) / "j1.json").read_bytes()
    d2 = (Path(tmp) / "j2.json").read_bytes()
    assert d1 == d2, "same seed must produce identical plan"
    print("PASS jitter: deterministic (byte-identical)")


def test_only_target_layer(tmp):
    a = Path(tmp) / "a.json"
    make_plan(a)
    J.jitter_plan(a, Path(tmp) / "j.json")
    doc0, _, _ = plan_io.load_plan(a)
    doc1, _, _ = plan_io.load_plan(Path(tmp) / "j.json")
    bed0 = [s for s in doc0["strokes"] if s["layer"] == "bed"][0]
    bed1 = [s for s in doc1["strokes"] if s["layer"] == "bed"][0]
    assert bed0["points"] == bed1["points"], "bed layer must not move"
    assert len(doc0["strokes"]) == len(doc1["strokes"])
    band1 = [s for s in doc1["strokes"] if s["layer"] == "band"]
    assert all(s["size"] == 6.0 and s["color"][0] >= 20 for s in band1)
    xs = np.array([[p[0] for p in s["points"]] for s in band1])
    frac = float(np.mean(np.abs(xs - np.round(xs)) < 1e-6))
    assert frac < 0.2, "coordinates should leave the pixel grid, integer fraction %.2f" % frac
    print("PASS jitter: only band moved, %.0f%% coords still on integer grid" % (frac * 100))


def main():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        test_deterministic(td)
        test_only_target_layer(td)
    print("RESULT OK")


if __name__ == "__main__":
    main()
