"""masks 两种选取语义：any=碰到就算，all=整笔在里面才算（改背景不染主体）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import masks
import plan_io


def make_doc(tmp):
    p = Path(tmp) / "m.json"
    strokes = [
        {"layer": "bed", "preset": "round_soft", "size": 6.0, "opacity": 1.0,
         "color": [10, 10, 10], "points": [[20, 20, 1.0], [30, 22, 1.0]]},       # 完全在掩膜内
        {"layer": "bed", "preset": "round_soft", "size": 6.0, "opacity": 1.0,
         "color": [10, 10, 10], "points": [[40, 40, 1.0], [120, 120, 1.0]]},     # 只有起点在内
    ]
    plan_io.dump_plan(str(p), 200, 200, strokes, layer_blends={"bed": "normal"})
    return plan_io.load_plan(p)[0]


def test_modes(tmp):
    doc = make_doc(tmp)
    m = masks.rect(0, 0, 60, 60)
    sel_any, _ = masks.weights(doc, m, 0.0, mode="any")
    sel_all, _ = masks.weights(doc, m, 0.0, mode="all")
    assert sel_any == [0, 1], sel_any
    assert sel_all == [0], sel_all
    print("PASS mask modes: any selects %d strokes, all selects %d (straddler excluded)"
          % (len(sel_any), len(sel_all)))


def main():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        test_modes(td)
    print("RESULT OK")


if __name__ == "__main__":
    main()
