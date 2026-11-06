"""最小形状演示：fill_polygon + tapered_stroke -> plan / png。

    python examples/shape_demo.py

产物在 examples/out_shape_demo/。它不是一张完整作品，只演示两个 helper 的调用；
完整流程、风格参数组和宿主工具调用见 docs/EXAMPLES.md。
"""
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import plan_io
import render_util as RU
import shape_ops

W, H = 800, 600
doc = {"canvas": [W, H], "layers": [], "strokes": []}

# 底：整幅天空，平涂大笔
shape_ops.fill_polygon(doc, "sky", [[0, 0], [W, 0], [W, H], [0, H]],
                       color="#141a2a", size=140, spacing=46,
                       preset="flat", edge=False, jitter=0.0)

# 月：多边形近似圆；fill_polygon 的 edge 默认会补边
moon = []
for k in range(96):
    a = 2.0 * math.pi * k / 96.0
    moon.append([210 + 58 * math.cos(a), 150 + 58 * math.sin(a)])
shape_ops.fill_polygon(doc, "moon", moon, color="#e8dfc0", size=18,
                       spacing=6, preset="round_soft", blend="screen")

# 鹿：身体、颈、头、腿分开填，后面才能单独调
body = [[420, 360], [610, 350], [650, 430], [635, 500], [430, 505], [400, 430]]
shape_ops.fill_polygon(doc, "stag_body", body, color="#3a2c26", size=26,
                       spacing=9, preset="bristle", jitter=0.25)
shape_ops.fill_polygon(doc, "stag_neck", [[430, 360], [455, 270], [500, 275], [505, 365]],
                       color="#3a2c26", size=18, spacing=7, preset="bristle")
shape_ops.fill_polygon(doc, "stag_head", [[455, 270], [500, 275], [540, 300], [500, 315]],
                       color="#332620", size=13, spacing=5, preset="bristle")
for leg in ([[445, 495], [475, 495], [470, 575], [448, 575]],
            [[590, 495], [620, 495], [618, 575], [596, 575]]):
    shape_ops.fill_polygon(doc, "stag_legs", leg, color="#30241f", size=13,
                           spacing=5, preset="bristle")

# 鹿角：每根一条锥形笔
for d in ("M 478 270 C 470 230 455 205 430 190",
          "M 490 268 C 495 225 515 195 545 180",
          "M 470 245 C 445 225 430 220 405 220"):
    shape_ops.tapered_stroke(doc, "antler", d, color="#6b5744",
                             size=10, size_end=1.2, steps=24, preset="round_hard")

out = ROOT / "examples" / "out_shape_demo"
out.mkdir(parents=True, exist_ok=True)
plan_io.dump_plan(str(out / "shape.plan.json"), W, H, doc["strokes"],
                  layer_blends={"moon": "screen"})
arr = RU.render_arr(plan_io.load_plan(str(out / "shape.plan.json"))[0],
                    gpu=False, nthreads=8)[..., :3]
from PIL import Image
Image.fromarray(arr.astype("uint8")).save(out / "shape.png")
print("strokes", len(doc["strokes"]), "layers", len(doc["layers"]),
      "out", out / "shape.png")
