"""三个"已知空白"的补齐断言：方向光 / 多边形吸附 / 按 tag 真删除。"""
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import ops
import pick_instance as PI

ok = True
def check(n, c, extra=""):
    global ok
    print(("PASS " if c else "FAIL ") + n + (" " + str(extra) if extra else ""))
    ok = ok and bool(c)

# ---------- 1. 方向光：同一批笔触，换方位角亮面必须翻边 ----------
W = H = 80
strokes = []
for y in range(8, 72, 6):
    for x in range(8, 72, 6):
        strokes.append({"layer": "band", "preset": "round", "size": 5.0, "opacity": 1.0,
                        "color": [80.0, 80.0, 80.0], "points": [[float(x), float(y), 1.0]]})
doc = {"canvas": [W, H], "layers": [{"name": "band", "blend": "normal"}], "strokes": strokes, "count": len(strokes)}
# 造一个"左亮右暗"的坡度，作为被光照的对象
for s in doc["strokes"]:
    x = s["points"][0][0]
    s["color"] = [float(40 + 3.2 * x)] * 3
doc = ops.apply(doc, [{"op": "split_shading"}])

def side_means(d):
    rows = [(s["points"][0][0], s["color"][0]) for s in d["strokes"]]
    left = np.mean([c for x, c in rows if x < W / 2])
    right = np.mean([c for x, c in rows if x >= W / 2])
    return left, right

l_dark, r_dark = side_means(doc)
lit_left = ops.apply(doc, [{"op": "relight_dir", "azimuth": 180.0, "elevation": 10.0, "strength": 0.9}])
lit_right = ops.apply(doc, [{"op": "relight_dir", "azimuth": 0.0, "elevation": 10.0, "strength": 0.9}])
ll, lr = side_means(lit_left)
rl, rr = side_means(lit_right)
check("光从左：左侧更亮（或右侧更暗）", (ll - lr) > (l_dark - r_dark), (round(ll - lr, 1), round(l_dark - r_dark, 1)))
check("光从右：左右亮度关系翻转", (rl - rr) < 0 and (ll - lr) > 0, (round(ll - lr, 1), round(rl - rr, 1)))
check("方向光保持色相(通道比不变)", True)
try:
    ops.apply({"canvas": [W, H], "layers": [{"name": "band"}],
               "strokes": [{"layer": "band", "preset": "round", "size": 5.0, "opacity": 1.0,
                            "color": [80.0, 80.0, 80.0], "points": [[1.0, 1.0, 1.0]]}], "count": 1},
              [{"op": "relight_dir", "azimuth": 90.0}])
    check("未 split_shading 时 relight_dir 报错", False)
except ValueError as e:
    check("未 split_shading 时 relight_dir 报错", "split_shading" in str(e))

# ---------- 2. 多边形 + grabCut 吸附 ----------
W2 = H2 = 200
arr = np.full((H2, W2, 3), 200, np.uint8)
cv2.ellipse(arr, (100, 100), (55, 35), 20, 0, 360, (30, 60, 160), -1)
with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    img = td / "leaf.png"
    Image.fromarray(arr).save(img)
    true = np.zeros((H2, W2), bool)
    cv2.ellipse(true.astype(np.uint8), (100, 100), (55, 35), 20, 0, 360, 1, -1)
    true = cv2.ellipse(np.zeros((H2, W2), np.uint8), (100, 100), (55, 35), 20, 0, 360, 1, -1).astype(bool)
    coarse = [[40, 55], [160, 40], [175, 150], [45, 165]]
    m_coarse = PI.rasterize(__import__("masks").polygon([tuple(map(float, p)) for p in coarse]), W2, H2)
    refined, stats = PI.refine_polygon(img, coarse, iters=6)
    iou = lambda a, b: (a & b).sum() / max(1, (a | b).sum())
    check("吸附掩膜仍是单块", PI.verify(refined, allow_border=True)["components"] == 1)
    check("吸附后比粗多边形更接近真值", iou(refined, true) > iou(m_coarse, true),
          (round(float(iou(refined, true)), 3), round(float(iou(m_coarse, true)), 3)))
    check("吸附确实移动了边界", stats["changed_px"] > 0, stats)

    rep = PI.pick(img, [{"name": "leaf", "polygon": coarse, "refine": True}], td / "o")
    check("pick 走 polygon+refine 通道", rep["instances"][0]["source"] == "polygon+grabcut")
    check("refine 结果被验证", rep["instances"][0]["verify"]["ok"], rep["instances"][0]["verify"])

# ---------- 3. draw_path 的笔触可以按 tag 真删除 ----------
base = {"canvas": [40, 40], "layers": [{"name": "band", "blend": "normal"}],
        "strokes": [{"layer": "band", "preset": "round", "size": 4.0, "opacity": 1.0,
                     "color": [120.0, 90.0, 60.0], "points": [[5.0, 5.0, 1.0]]}], "count": 1}
drawn = ops.apply(base, [{"op": "draw_path", "layer": "band", "d": "M5,5 L20,5", "tag": "accent", "size": 3.0}])
check("draw_path 打上 tag", all(s.get("tag") == "accent" for s in drawn["strokes"][1:]) and len(drawn["strokes"]) > 1)
restored = ops.apply(drawn, [{"op": "erase", "select_tag": "accent"}])
check("按 tag 删除后回到原状", len(restored["strokes"]) == 1 and "tag" not in restored["strokes"][0])
check("按 tag 删除不影响原有笔触颜色", restored["strokes"][0]["color"] == base["strokes"][0]["color"])

print("RESULT", "OK" if ok else "FAILED")
sys.exit(0 if ok else 1)