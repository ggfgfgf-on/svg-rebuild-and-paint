"""多光源算子 light_rig 的验证：双光颜色、区域限定、形状、确定性、亮度和守恒。

跑法：python tests/test_light_rig.py
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE / "scripts"))

import numpy as np
import ops
import plan_io


def luma(c):
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def centroids(doc):
    out = []
    for s in doc["strokes"]:
        px = sum(p[0] for p in s["points"]) / len(s["points"])
        py = sum(p[1] for p in s["points"]) / len(s["points"])
        out.append((px, py))
    return out


def main():
    doc, w, h = plan_io.load_plan(str(HERE / "examples" / "demo.plan.json"))
    cut = w * 0.5
    cen = centroids(doc)

    # 0) 未 split_shading 时拒绝
    try:
        ops.apply(doc, [{"op": "light_rig", "lights": [{"type": "directional"}]}])
    except ValueError as ex:
        assert "split_shading" in str(ex), ex
    else:
        raise AssertionError("light_rig without split_shading should fail")
    print("requires split_shading: ok")

    shaded = ops.apply(doc, [{"op": "split_shading"}])
    base = [tuple(s["color"]) for s in shaded["strokes"]]
    base_luma = float(np.mean([luma(c) for c in base]))

    # 1) 双光源：左暖右冷；亮度和守恒
    two = [{"type": "directional", "azimuth": 180, "elevation": 10, "strength": 0.9, "color": "#ffcc88"},
           {"type": "directional", "azimuth": 0, "elevation": 10, "strength": 0.9, "color": "#88bbff"}]
    out = ops.apply(shaded, [{"op": "light_rig", "lights": two}])
    left = [i for i, (px, py) in enumerate(cen) if px < cut]
    right = [i for i, (px, py) in enumerate(cen) if px >= cut]

    def mean(vals):
        return float(np.mean(vals)) if vals else 0.0

    def warm_ratio(doc_, idx):
        return mean([doc_["strokes"][i]["color"][0] / max(1.0, doc_["strokes"][i]["color"][2]) for i in idx])

    assert warm_ratio(out, left) > warm_ratio(shaded, left), "左侧应更暖"
    assert warm_ratio(out, right) < warm_ratio(shaded, right), "右侧应更冷"
    new_luma = float(np.mean([luma(tuple(s["color"])) for s in out["strokes"]]))
    assert abs(new_luma - base_luma) / base_luma < 0.02, (new_luma, base_luma)
    print("two lights color + luma preservation: ok (luma %.2f -> %.2f)" % (base_luma, new_luma))

    # 2) 区域限定（preserve=none）：圆外的笔触必须逐字节不变
    only_left = [{"type": "directional", "azimuth": 180, "elevation": 10, "strength": 1.0,
                  "color": "#ffddaa", "shape": {"circle": [cut * 0.5, h * 0.5, cut * 0.42], "feather": 8}}]
    out2 = ops.apply(shaded, [{"op": "light_rig", "lights": only_left, "preserve": "none"}])
    changed_right = sum(1 for i in right if tuple(out2["strokes"][i]["color"]) != base[i])
    changed_left = sum(1 for i in left if tuple(out2["strokes"][i]["color"]) != base[i])
    assert changed_right == 0, "圆外（右半）不应被照亮，改了 %d 笔" % changed_right
    assert changed_left > 0
    print("region circle: ok (left %d changed, right 0 changed)" % changed_left)

    # 3) 多边形形状 + 反向遮罩可跑
    poly = [{"type": "radial", "at": [w * 0.25, h * 0.5], "strength": 1.0, "color": "#ffffff",
             "radius": max(w, h) * 0.6,
             "shape": {"polygon": [[0, 0], [cut - 20, 0], [cut - 20, h], [0, h]], "feather": 6}}]
    out3 = ops.apply(shaded, [{"op": "light_rig", "lights": poly, "preserve": "none"}])
    changed_right3 = sum(1 for i in right if tuple(out3["strokes"][i]["color"]) != base[i])
    assert changed_right3 == 0
    print("radial light + polygon region: ok")

    # 4) 确定性 + guidance=0 逐字节不变
    a = ops.apply(shaded, [{"op": "light_rig", "lights": two}])
    b = ops.apply(shaded, [{"op": "light_rig", "lights": two}])
    assert ops.canonical(a) == ops.canonical(b)
    z = ops.apply(shaded, [{"op": "light_rig", "lights": two, "guidance": 0.0}])
    assert ops.canonical(z) == ops.canonical(shaded)
    print("deterministic + guidance=0: ok")

    # 5) 渲染级守恒可跑（rendered）
    r = ops.apply(shaded, [{"op": "light_rig", "lights": two, "preserve": "rendered"}])
    assert len(r["strokes"]) == len(shaded["strokes"])
    print("preserve=rendered: ok")

    # 6) 贴图式光照：彩色窗花 gobo（rgb 模式）+ 任意形状掩膜图
    import os
    import tempfile
    import cv2

    td = tempfile.mkdtemp()
    glass = np.zeros((64, 128, 3), np.uint8)      # BGR
    glass[:, :60] = (0, 0, 255)                  # 左：红玻璃
    glass[:, 68:] = (255, 0, 0)                  # 右：蓝玻璃
    glass[:, 60:68] = (0, 0, 0)                  # 中缝：黑铅条（不透光）
    glass_path = os.path.join(td, "glass.png")
    cv2.imwrite(glass_path, glass)

    gobo = [{"type": "directional", "azimuth": 200, "elevation": 10, "strength": 1.8,
             "map": glass_path, "map_mode": "rgb"}]
    outg = ops.apply(shaded, [{"op": "light_rig", "lights": gobo, "preserve": "none"}])
    lead = [i for i, (px, py) in enumerate(cen) if 0.49 * w <= px <= 0.51 * w]
    if lead:
        assert all(tuple(outg["strokes"][i]["color"]) == base[i] for i in lead), "铅条处应不透光"
    assert warm_ratio(outg, left) > warm_ratio(shaded, left), "左窗格应投红光"
    assert warm_ratio(outg, right) < warm_ratio(shaded, right), "右窗格应投蓝光"
    print("stained-glass gobo (rgb): ok (lead band %d strokes unchanged)" % len(lead))

    blob = np.zeros((320, 480), np.uint8)
    cv2.circle(blob, (120, 160), 90, 255, -1)
    blob_path = os.path.join(td, "blob.png")
    cv2.imwrite(blob_path, blob)
    ml = [{"type": "directional", "azimuth": 200, "elevation": 10, "strength": 1.2,
           "shape": {"mask_file": blob_path, "feather": 6}}]
    outm = ops.apply(shaded, [{"op": "light_rig", "lights": ml, "preserve": "none"}])
    far = [i for i, (px, py) in enumerate(cen) if px > 320]
    if far:
        assert all(tuple(outm["strokes"][i]["color"]) == base[i] for i in far), "掩膜外不应受影响"
    inside = [i for i, (px, py) in enumerate(cen) if (px - 120) ** 2 + (py - 160) ** 2 < 70 ** 2]
    if inside:
        assert any(tuple(outm["strokes"][i]["color"]) != base[i] for i in inside), "掩膜内应有变化"
    print("arbitrary mask_file: ok (far %d unchanged)" % len(far))

    g2 = ops.apply(shaded, [{"op": "light_rig", "lights": gobo, "preserve": "none"}])
    assert ops.canonical(g2) == ops.canonical(outg)
    print("gobo deterministic: ok")

    print("RESULT OK")


if __name__ == "__main__":
    main()
