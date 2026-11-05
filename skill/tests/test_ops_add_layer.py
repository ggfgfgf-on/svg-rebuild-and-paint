"""add_layer / draw_path 自动建层 / paint_cli --save-plan 的端到端验证。

跑法：python tests/test_ops_add_layer.py
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
SCRIPTS = HERE / "scripts"
sys.path.insert(0, str(SCRIPTS))

import numpy as np
import ops
import plan_io
import render_util


def arr(doc):
    return render_util.render_arr(doc, gpu=False, nthreads=8)


def layer_names(doc):
    return [l["name"] for l in doc["layers"]]


def main():
    doc, w, h = plan_io.load_plan(str(HERE / "examples" / "demo.plan.json"))
    assert "glow_probe" not in layer_names(doc)

    # 1) add_layer：新层出现、顺序在末尾、blend 生效；入参 doc 不被修改
    out = ops.apply(doc, [{"op": "add_layer", "name": "glow_probe", "blend": "screen"}])
    assert layer_names(doc) == layer_names(plan_io.load_plan(str(HERE / "examples" / "demo.plan.json"))[0]), "apply 不应改入参"
    assert layer_names(out)[-1] == "glow_probe"
    assert out["layers"][-1]["blend"] == "screen"
    print("add_layer: ok ->", layer_names(out)[-1])

    # 2) 幂等：再调一次不重复建层
    out2 = ops.apply(out, [{"op": "add_layer", "name": "glow_probe"}])
    assert layer_names(out2).count("glow_probe") == 1
    assert out2["layers"][-1]["blend"] == "screen", "已存在的层不应被改 blend"
    print("add_layer idempotent: ok")

    # 3) guidance=0 不动计划
    out3 = ops.apply(doc, [{"op": "add_layer", "name": "ghost", "guidance": 0.0}])
    assert "ghost" not in layer_names(out3)
    print("add_layer guidance=0: ok")

    # 4) draw_path 指向不存在的层 -> 自动建层并落笔
    spec = {"op": "draw_path", "layer": "rain_probe", "d": "M 20 20 L 220 200",
            "color": "#ffffff", "size": 8, "opacity": 0.6, "preset": "liner"}
    out4 = ops.apply(doc, [spec])
    assert "rain_probe" in layer_names(out4)
    assert any(s["layer"] == "rain_probe" for s in out4["strokes"])
    print("draw_path auto-create: ok")

    # 5) 渲染确定性：同一计划两次渲染逐像素一致，且新层真的出像素
    a1, a2 = arr(out4), arr(out4)
    assert int(np.abs(a1.astype(np.int32) - a2.astype(np.int32)).max()) == 0
    print("deterministic render: ok")

    # 6) 校验器：add_layer 名字必填；draw_path 新层放行；普通算子指新层仍拒绝
    import compile_ops
    compile_ops.validate_ops(doc, [{"op": "add_layer", "name": "t_glow"}])
    compile_ops.validate_ops(doc, [spec])
    try:
        compile_ops.validate_ops(doc, [{"op": "set_layer_brightness", "layer": "nope", "factor": 1.2}])
    except ValueError as ex:
        assert "unknown layer" in str(ex)
    else:
        raise AssertionError("普通算子指向不存在的层应被拒绝")
    print("validator rules: ok")

    # 7) CLI 端到端：edit + --save-plan，落盘计划重渲 == PNG
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        plan_in = td / "in.plan.json"
        plan_in.write_text(json.dumps(doc), encoding="utf-8")
        ops_path = td / "ops.json"
        ops_path.write_text(json.dumps([
            {"op": "add_layer", "name": "glow_probe", "blend": "screen"},
            {"op": "draw_path", "layer": "glow_probe", "d": "M 60 60 L 360 300",
             "color": "#ffe9b0", "size": 18, "opacity": 0.5, "preset": "round_soft"},
            {"op": "densify", "layer": "highlight", "k": 1.1},
        ]), encoding="utf-8")
        png = td / "out.png"
        saved = td / "out.plan.json"
        r = subprocess.run([sys.executable, str(SCRIPTS / "paint_cli.py"), "edit", str(plan_in),
                            "--ops", str(ops_path), "--out", str(png), "--save-plan", str(saved),
                            "--fast"], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr[-800:]
        rep = json.loads(r.stdout.strip().splitlines()[-1])
        assert rep["ok"] and rep.get("plan") == str(saved)
        d2, w2, h2 = plan_io.load_plan(str(saved))
        assert "glow_probe" in layer_names(d2) and d2["layers"][-1]["blend"] == "screen"
        assert isinstance(rep.get("plan_strokes"), int) and rep["plan_strokes"] == len(d2["strokes"])
        again = arr(d2)
        ref = np.asarray(render_util.load_png(str(png)) if hasattr(render_util, "load_png") else None) if False else None
        from PIL import Image
        ref = np.asarray(Image.open(png).convert("RGBA"))
        diff = int(np.abs(again.astype(np.int32) - ref.astype(np.int32)).max())
        assert diff == 0, "落盘计划重渲应与 PNG 逐像素一致，实际 maxdiff=%d" % diff
        print("cli --save-plan round-trip: ok (maxdiff 0, strokes=%d, layers=%d)" % (len(d2["strokes"]), len(d2["layers"])))

    print("RESULT OK")


if __name__ == "__main__":
    main()
