"""rebuild_shadow_layer 契约：确定性、只换 shadow 层、混合模式不变、不再落在 38px 格点上。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import plan_io
import rebuild_shadow_layer as R


def make_plan(path):
    strokes = [{"layer": "bed", "preset": "round_soft", "size": 30.0, "opacity": 1.0,
                "color": [40, 60, 40], "points": [[0, 0, 1.0]]}]
    for gy in range(0, 40, 16):
        for gx in range(0, 40, 16):
            strokes.append({"layer": "shadow", "preset": "flat", "size": 12.0, "opacity": 0.4,
                            "color": [10, 10, 10],
                            "points": [[gx + 8, gy + 8, 1.0], [gx + 8, gy + 7, 1.0]]})
    plan_io.dump_plan(str(path), 40, 40, strokes, layer_blends={"bed": "normal", "shadow": "multiply"})


def test_rebuild_random(tmp):
    p = Path(tmp) / "a.json"
    make_plan(p)
    out1, out2 = Path(tmp) / "o1.json", Path(tmp) / "o2.json"
    n1 = R.rebuild(p, out1, mode="random", seed=7)
    n2 = R.rebuild(p, out2, mode="random", seed=7)
    assert n1 > 0 and n1 == n2
    assert out1.read_bytes() == out2.read_bytes(), "same seed must be byte-identical"
    doc, _, _ = plan_io.load_plan(out1)
    sh = [s for s in doc["strokes"] if s["layer"] == "shadow"]
    assert sh and all(s["preset"] == "round_soft" for s in sh)
    assert {l["name"]: l["blend"] for l in doc["layers"]}["shadow"] == "multiply"
    xs = [round(s["points"][0][0] % 16, 3) for s in sh]
    assert len(set(xs)) > 3, "positions should leave the 16px lattice"
    print("PASS rebuild(random): %d strokes, deterministic, blend kept, lattice broken" % n1)


def test_rebuild_grid(tmp):
    p = Path(tmp) / "b.json"
    make_plan(p)
    out = Path(tmp) / "g.json"
    n = R.rebuild(p, out, cell=16, mode="grid", seed=1)
    doc, _, _ = plan_io.load_plan(out)
    sh = [s for s in doc["strokes"] if s["layer"] == "shadow"]
    assert n == len(sh) and n > 0
    print("PASS rebuild(grid): %d soft dabs" % n)


def main():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        test_rebuild_random(td)
        test_rebuild_grid(td)
    print("RESULT OK")


if __name__ == "__main__":
    main()
