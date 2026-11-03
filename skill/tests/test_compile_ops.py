"""指令编译层的验收：摘要 / 提示词 / schema 校验 / 落盘 apply 可复现。"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import compile_ops
import ops
import plan_io
import masks

W, H = 120, 90


def make_doc(path):
    strokes = []
    for layer, color, n in (("petal", [200, 40, 60], 6), ("leaf", [40, 160, 60], 4)):
        for k in range(n):
            strokes.append({"layer": layer, "preset": "round_hard", "size": 6.0 + k, "opacity": 0.9,
                            "color": color, "hardness": 0.7,
                            "points": [[10.0 + 8 * k, 12.0 + 5 * k, 0.7], [14.0 + 8 * k, 16.0 + 5 * k, 0.9]]})
    plan_io.dump_plan(path, W, H, strokes, {"petal": "normal", "leaf": "multiply"})
    doc, _, _ = plan_io.load_plan(path)
    return doc


def main():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        plan = tmp / "p.json"
        doc = make_doc(plan)

        s = compile_ops.summarize(doc)
        assert [l["name"] for l in s["layers"]] == ["petal", "leaf"], s["layers"]
        assert s["layers"][0]["strokes"] == 6 and s["layers"][0]["mean_color"].startswith("#")
        assert s["palette_top"] and s["total_strokes"] == 10
        print("PASS summarize: layers=%d palette=%d total=%d"
              % (len(s["layers"]), len(s["palette_top"]), s["total_strokes"]))

        p = compile_ops.build_prompt(s, "把花改成蓝色，叶子保持", masks.rect(5, 5, 60, 40))
        for token in ("set_layer_hue", "guidance", "把花改成蓝色，叶子保持", '"petal"'):
            assert token in p, token
        assert p.count("JSON") >= 2
        print("PASS prompt: schema + summary + instruction + mask, %d chars" % len(p))

        good = [{"op": "set_layer_hue", "layer": "petal", "hue": 215, "guidance": 0.9},
                {"op": "densify", "layer": "petal", "k": 1.4}]
        compile_ops.validate_ops(doc, good)

        bad_cases = [
            ([{"op": "set_layer_hue", "layer": "petal", "hue": 215, "guidance": 1.5}], "guidance"),
            ([{"op": "densify", "layer": "petal"}], "missing"),
            ([{"op": "paint_magic", "layer": "petal"}], "unknown op"),
            ([{"op": "set_layer_hue", "layer": "stem", "hue": 100}], "unknown layer"),
            ([{"op": "swap_brush", "layer": "petal", "preset": "no_such_brush"}], "unknown preset"),
            ([{"op": "set_layer_hue", "layer": "petal", "hue": 10, "extra": 1}], "extra"),
        ]
        for specs, _why in bad_cases:
            try:
                compile_ops.validate_ops(doc, specs)
            except ValueError:
                continue
            raise AssertionError("should have rejected: %r" % (specs,))
        print("PASS validate: guidance/缺键/未知算子/未知层/未知笔刷 全部拒绝")

        out_path = tmp / "ops.json"
        out_path.write_text(json.dumps(good), encoding="utf-8")
        before = ops.fingerprint(doc)
        out1 = ops.apply(doc, compile_ops.validate_ops(doc, json.loads(out_path.read_text(encoding="utf-8"))))
        out2 = ops.apply(doc, compile_ops.validate_ops(doc, json.loads(out_path.read_text(encoding="utf-8"))))
        assert ops.fingerprint(out1) == ops.fingerprint(out2), "compiled ops are not reproducible"
        assert ops.fingerprint(out1) != before
        print("PASS apply: fingerprint %s -> %s, repeat identical" % (before, ops.fingerprint(out1)))
    print("RESULT OK")


if __name__ == "__main__":
    main()
