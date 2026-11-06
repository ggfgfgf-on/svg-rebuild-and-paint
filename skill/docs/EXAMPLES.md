# 形状能力调用示例（新增）

这份文档是**新增示例**：原有 20 个算子、原有流程都不动；新增的是
scripts/shape_ops.py 里的两个 helper，用来解决“主体块面”和“鹿角/毛流笔形”。

- fill_polygon：用普通笔触填一个多边形（主体剪影、岩台、水面块面）。
- tapered_stroke：沿 SVG path 画一条由粗到细的笔道（鹿角、枝干、毛流）。

它们不是新笔刷，也不改渲染器；产物仍是可 diff、可 densify、可光照的普通笔触。
风格不是固定的：下面所有颜色、尺寸、笔刷都是参数，示例只展示“怎么调”，不规定
“必须这样画”。

仓库里有一个可直接跑的最小演示：

~~~powershell
python examples/shape_demo.py
~~~

产物在 examples/out_shape_demo/（shape.plan.json / shape.png）。

## 1. 最小可运行：填一个块面 + 一条锥形笔

~~~python
import sys
from pathlib import Path

sys.path.insert(0, r"<skill>\scripts")
import plan_io
import render_util as RU
import shape_ops

W, H = 400, 300
doc = {"canvas": [W, H], "layers": [], "strokes": []}

# 主体块面：多边形按你画的剪影走，点按顺序给，不用闭合
body = [[120, 90], [300, 90], [320, 210], [100, 210]]
shape_ops.fill_polygon(doc, "body", body, color="#7a5a46", size=22,
                       opacity=1.0, preset="bristle", spacing=8, jitter=0.35)

# 鹿角：一条 path = 一根由粗到细的枝
shape_ops.tapered_stroke(doc, "antler", "M 250 100 C 260 60 285 40 310 30",
                         color="#b9986a", size=14, size_end=1.5,
                         steps=28, preset="round_hard")

out = Path(r"<out>")
plan_io.dump_plan(str(out / "shape.plan.json"), W, H, doc["strokes"],
                  layer_blends={"body": "normal", "antler": "normal"})
arr = RU.render_arr(plan_io.load_plan(str(out / "shape.plan.json"))[0],
                    gpu=False, nthreads=8)[..., :3]
from PIL import Image
Image.fromarray(arr.astype("uint8")).save(out / "shape.png")
print("strokes", len(doc["strokes"]), "layers", [l["name"] for l in doc["layers"]])
~~~

要点：

- body 是普通多边形，不是 SVG；点可以凹多边形，helper 会做点在多边形内测试。
- spacing 默认是 size 的 0.45 倍；想更实就减小，想更透就增大。
- fill_polygon 默认会沿多边形边补一圈（edge=True），减少圆笔触的锯齿边。

## 2. 主体块面：从剪影到块面

先只填一个可读的剪影，不要一上来画毛：

~~~python
# 鹿的身体：侧身剪影，头颈单独一块，腿单独几块
shape_ops.fill_polygon(doc, "stag_body",
    [[560, 430], [760, 400], [820, 520], [800, 640], [560, 650], [520, 540]],
    color="#6b4f3f", size=30, opacity=1.0, preset="bristle",
    spacing=11, jitter=0.3)

shape_ops.fill_polygon(doc, "stag_neck",
    [[560, 430], [600, 300], [650, 300], [640, 440]],
    color="#6b4f3f", size=24, opacity=1.0, preset="bristle", spacing=9)

for leg in (
    [[570, 640], [600, 640], [600, 820], [575, 820]],
    [[730, 640], [760, 640], [760, 820], [735, 820]],
):
    shape_ops.fill_polygon(doc, "stag_legs", leg, color="#5c4438", size=18,
                           opacity=1.0, preset="bristle", spacing=7)
~~~

块面层用 normal，尺寸大、笔触少；后面再用 detail 层收边和毛流。
不要用一个 fill_polygon 画完整只鹿；头、颈、躯干、腿分开，后面才能单独调。

## 3. 鹿角/毛流：锥形笔

鹿角不是一条等宽线，而是从根部到尖端逐渐变细：

~~~python
branches = [
    "M 640 310 C 650 250 680 210 720 190",
    "M 660 270 C 690 240 720 230 750 220",
    "M 670 250 C 650 210 630 190 600 180",
]
for d in branches:
    shape_ops.tapered_stroke(doc, "antler", d, color="#b9986a",
                             size=18, size_end=1.2, steps=32,
                             preset="round_hard", opacity=1.0)
~~~

毛流用多条短锥形笔，从身体边缘向外、顺结构走；不要用一条等宽长线：

~~~python
import math
# 示例：沿背线随机挑 40 个点，各画一条 12~24px 的细锥形毛
for k in range(40):
    x = 580 + k * 5
    y = 430 + 18 * math.sin(k * 0.7)
    d = "M %d %d Q %d %d %d %d" % (x, y, x + 4, y - 14, x + 9, y - 24)
    shape_ops.tapered_stroke(doc, "stag_fur", d, color="#8a6a52",
                             size=4.5, size_end=0.6, steps=8,
                             preset="dry", opacity=0.35)
~~~

鹿角/毛流是两件事：鹿角用 round_hard 保住轮廓，毛流用 dry 低不透明度；
不要把毛流画成等宽短线，也不要把鹿角画成一条粗线。

## 4. 收边与光照：还是原来的算子

新增 helper 只负责“造形”，收边/光照仍用原来的 20 个算子：

~~~python
# 1) 给鹿角加一圈月光边缘（先选中鹿角层，再提亮）
ops_json = [
    {"op": "set_layer_hue", "layer": "antler", "hue": 40, "guidance": 0.2},
    {"op": "densify", "layer": "stag_fur", "k": 1.2},
]
# 2) 用 paint_cli.py edit 应用；或用 art_apply_ops（host 工具）
~~~

命令行：

~~~powershell
python scripts/paint_cli.py edit out/shape.plan.json --ops out/refine.ops.json --out out/shape.png --fast --save-plan out/shape.refined.plan.json
~~~

## 5. 宿主工具调用（最少的几个）

~~~json
{"plan": "out/shape.plan.json", "out": "out/shape.png", "gpu": false}
~~~

- art_paint：plan 只读，输出 PNG；同 plan 逐像素一致。
- art_apply_ops：plan + ops + out；要保留改后的 plan 就加 out_plan。
- art_edit_brief：plan + instruction + out；产出 brief.json 和缩略图，ops 仍由你写。
- compile_ops.py validate：先验 ops，再应用。

## 6. 风格是参数，不是规定

同一组几何，换参数就是另一种画法；选一组改到你的题材，不要照抄：

| 取向 | body 颜色/笔刷 | 鹿角 | 毛流 |
|---|---|---|---|
| 剪影优先 | #2a2a30 / flat，size 34 | #4a4038，size 16 | 不加，靠轮廓光 |
| 冷月厚涂 | #3f4a5a / bristle，size 28 | #9aa7b8，size 16 | #6f7f92 / dry，0.25 |
| 暖褐厚涂 | #7a5a46 / bristle，size 30 | #b9986a，size 18 | #8a6a52 / dry，0.35 |

“剪影优先”不是退让：主体结构没把握时，先把剪影画成立，比用毛流掩盖解剖更稳。

## 7. 常见错误

- polygon 少于 3 个点，或点不是 [x, y]：直接报错。
- spacing 太小、size 太大：stamp 数超过 250000，会报 too many stamps；先加大 spacing。
- size_end 大于 size：画出来是“由细到粗”；要鹿角尖细，size_end 应小于 size。
- 一个 fill_polygon 画完整只鹿：后面没法单独调头/腿；按块面分层。
- 把 tapered_stroke 当笔刷：它是“沿 path 生成普通笔触”，不是新 preset；仍可被 densify/光照处理。
