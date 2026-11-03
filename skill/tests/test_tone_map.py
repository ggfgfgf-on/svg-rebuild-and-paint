"""tone_map 契约测试：恒等 / guidance=0 / 逐层 / clamp / gamma 方向 / schema 拒绝坏参数。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import ops
import compile_ops


def make_doc():
    return {
        "canvas": {"w": 40, "h": 32, "seed": 1},
        "layers": [{"name": "bed", "blend": "normal"}, {"name": "shadow", "blend": "multiply"}],
        "strokes": [
            {"layer": "bed", "preset": "round_soft", "size": 8.0, "opacity": 1.0,
             "color": [100.0, 50.0, 200.0], "points": [[2, 2, 1.0], [20, 20, 1.0]]},
            {"layer": "shadow", "preset": "round_soft", "size": 6.0, "opacity": 1.0,
             "color": [60.0, 60.0, 60.0], "points": [[4, 4, 1.0], [24, 18, 1.0]]},
        ],
    }


def colors(doc):
    return [list(s["color"]) for s in doc["strokes"]]


def test_identity():
    d0 = make_doc()
    d = ops.apply(make_doc(), [{"op": "tone_map", "gain": 1.0}])
    # apply 会补 "count"，所以比较笔触本身（算子的契约是"计划内容不变"）
    assert json.dumps(d["strokes"], sort_keys=True) == json.dumps(d0["strokes"], sort_keys=True)
    d = ops.apply(make_doc(), [{"op": "tone_map", "gain": 2.0, "guidance": 0.0}])
    assert colors(d) == colors(d0)
    print("PASS identity: gain=1 / guidance=0 -> strokes byte-identical")


def test_gain_and_clamp():
    d = ops.apply(make_doc(), [{"op": "tone_map", "gain": 2.0, "layer": "bed"}])
    c = colors(d)
    assert abs(c[0][0] - 200.0) < 0.01 and abs(c[0][1] - 100.0) < 0.01
    assert c[1] == [60.0, 60.0, 60.0], "layer scoping leaked"
    d = ops.apply(make_doc(), [{"op": "tone_map", "gain": 10.0}])
    assert all(max(c) <= 255.0 and min(c) >= 0.0 for c in colors(d))
    print("PASS gain: x2 doubles, clamps at 255, layer scoping respected")


def test_lift_gamma():
    d = ops.apply(make_doc(), [{"op": "tone_map", "lift": 0.1}])
    assert abs(colors(d)[0][0] - 125.5) < 0.2, colors(d)[0]
    mid = ops.apply(make_doc(), [{"op": "tone_map", "gamma": 2.0}])
    assert colors(mid)[0][0] > 100.0, "gamma>1 should brighten midtones"
    print("PASS lift/gamma: lift=0.1 adds 25.5; gamma=2 brightens midtones")


def test_guidance_half():
    d = ops.apply(make_doc(), [{"op": "tone_map", "gain": 2.0, "guidance": 0.5}])
    assert abs(colors(d)[0][0] - 150.0) < 0.3, colors(d)[0]
    print("PASS guidance: 0.5 -> halfway between 100 and 200")


def test_validate():
    doc = make_doc()
    for bad in ({"op": "tone_map", "gain": 0}, {"op": "tone_map", "lift": 2.0}, {"op": "tone_map", "gamma": -1}):
        try:
            compile_ops.validate_ops(doc, [bad])
        except Exception:
            pass
        else:
            raise AssertionError("accepted bad spec %r" % (bad,))
    compile_ops.validate_ops(doc, [{"op": "tone_map", "gain": 1.6, "lift": 0.05, "gamma": 1.2}])
    print("PASS validate: rejects gain<=0 / lift>1 / gamma<=0, accepts normal params")


def main():
    test_identity()
    test_gain_and_clamp()
    test_lift_gamma()
    test_guidance_half()
    test_validate()
    print("RESULT OK")


if __name__ == "__main__":
    main()
