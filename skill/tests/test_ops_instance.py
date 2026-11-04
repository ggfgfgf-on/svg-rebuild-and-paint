"""算子层语义：掩膜/实例/多边形必须能直接写进 op 里，且验证不过就报错。"""
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import ops

ok = True
def check(n, c, extra=""):
    global ok
    print(("PASS " if c else "FAIL ") + n + (" " + str(extra) if extra else ""))
    ok = ok and bool(c)

W, H = 60, 60
def st(x, y, layer="band"):
    return {"layer": layer, "preset": "round", "size": 6.0, "opacity": 1.0,
            "color": [200.0, 60.0, 60.0], "points": [[float(x), float(y), 1.0]]}

doc = {"canvas": [W, H], "layers": [{"name": "band", "blend": "normal"}],
       "strokes": [st(30, 30), st(30, 32), st(2, 2), st(58, 58)], "count": 4}

# 合成一张"红球 + 灰底"的图，供实例选择用
arr = np.full((H, W, 3), 60, np.uint8)
yy, xx = np.mgrid[0:H, 0:W]
arr[(xx - 30) ** 2 + (yy - 30) ** 2 <= 10 ** 2] = (220, 40, 40)
with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    img = td / "inst.png"
    Image.fromarray(arr).save(img)

    inst = {"image": str(img), "seeds": [[30, 30]], "tolerance": 60, "hue_tol": 20, "grow": 4,
            "max_area": 0.5, "allow_border": True}
    out = ops.apply(doc, [{"op": "tone_map", "gain": 1.2, "instance": inst}])
    changed = [i for i, (a, b) in enumerate(zip(doc["strokes"], out["strokes"])) if a["color"] != b["color"]]
    check("实例选择只改选区内的笔触", changed == [0, 1], changed)
    check("实例外颜色不变", out["strokes"][2]["color"] == doc["strokes"][2]["color"])

    poly = ops.apply(doc, [{"op": "tone_map", "gain": 1.2,
                            "polygon": [[20, 20], [45, 20], [45, 45], [20, 45]]}])
    changed_p = [i for i, (a, b) in enumerate(zip(doc["strokes"], poly["strokes"])) if a["color"] != b["color"]]
    check("多边形选择生效", changed_p == [0, 1], changed_p)

    bad = {"image": str(img), "seeds": [[2, 2]], "min_area": 0.2, "hue_tol": 20, "grow": 2}
    try:
        ops.apply(doc, [{"op": "tone_map", "gain": 1.2, "instance": bad}])
        check("实例验证不通过就报错", False)
    except ValueError as e:
        check("实例验证不通过就报错", "验证不通过" in str(e), str(e)[:60])

# 笔触级选择：框 + 色相阈值，绕开"给照片做分割"
doc2 = {"canvas": [100, 100], "layers": [{"name": "band", "blend": "normal"}],
        "strokes": [
            {"layer": "band", "preset": "round", "size": 4.0, "opacity": 1.0,
             "color": [30.0, 90.0, 30.0], "points": [[20.0, 20.0, 1.0]]},   # 绿
            {"layer": "band", "preset": "round", "size": 4.0, "opacity": 1.0,
             "color": [30.0, 90.0, 30.0], "points": [[80.0, 80.0, 1.0]]},   # 绿但框外
            {"layer": "band", "preset": "round", "size": 4.0, "opacity": 1.0,
             "color": [200.0, 40.0, 40.0], "points": [[25.0, 25.0, 1.0]]}],  # 红
        "count": 3}
sel = ops.select_by_predicate(doc2, {"sel_box": [0, 0, 50, 50], "sel_hue": [0.33, 0.05], "sel_sat_min": 0.2})
check("框+色相只选到框内绿色笔触", sel == [0], sel)
out2 = ops.apply(doc2, [{"op": "tone_map", "gain": 1.5, "sel_box": [0, 0, 50, 50],
                        "sel_hue": [0.33, 0.05], "sel_sat_min": 0.2}])
check("笔触级选择只改命中的笔触", out2["strokes"][0]["color"] != doc2["strokes"][0]["color"]
      and out2["strokes"][1]["color"] == doc2["strokes"][1]["color"]
      and out2["strokes"][2]["color"] == doc2["strokes"][2]["color"])
check("笔触选择对图层无副作用", len(out2["strokes"]) == 3)

# 未知键仍然拒绝（实例字段不能被当成任意透传）
try:
    ops.validate({"op": "tone_map", "gain": 1.2, "bogus": 1})
    check("未知键被拒", False)
except ValueError:
    check("未知键被拒", True)

# replan_region 是正式算子
check("replan_region 在算子表里", "replan_region" in ops.OPS)
check("replan_region 必填 ref", ops.REQUIRED["replan_region"] == ["ref"])
ops.validate({"op": "replan_region", "ref": "x.png", "layers": ["band"]})
check("replan_region 接受 mask/instance 选择字段", True)
try:
    ops.validate({"op": "replan_region", "layers": ["band"]})
    check("replan_region 缺 ref 报错", False)
except ValueError as e:
    check("replan_region 缺 ref 报错", "missing" in str(e))

print("RESULT", "OK" if ok else "FAILED")
sys.exit(0 if ok else 1)