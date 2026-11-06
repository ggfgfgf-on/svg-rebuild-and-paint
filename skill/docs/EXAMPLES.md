# 形状能力调用示例（新增）

这份文档只讲 **shape_ops 两个 helper 怎么快速调用**，不是画面结构模板；参数语义、约束与层序见 [SHAPE_API.md](SHAPE_API.md)。
原有示例（examples/demo.plan.json 的 bed / band / ambient / highlight）和
USAGE §4.6 / §6.1 的结构都不动；这里不提供“照着拆层”的替代结构。

- fill_polygon：用普通笔触填一个多边形（岩台、剪影、块面）。
- tapered_stroke：沿 SVG path 画一条由粗到细的笔道（枝干、鹿角、毛流）。

它们不是新笔刷，也不改渲染器；产物仍是可 diff、可 densify、可光照的普通笔触。

## 1. 最小调用

~~~python
import sys
from pathlib import Path

sys.path.insert(0, r"<skill>\scripts")
import plan_io
import render_util as RU
import shape_ops

W, H = 400, 300
doc = {"canvas": [W, H], "layers": [], "strokes": []}

# 一个块面：点按顺序给，凹多边形也可以，不用闭合
shape_ops.fill_polygon(doc, "block", [[90, 80], [300, 70], [320, 220], [80, 210]],
                       color="#6b4f3f", size=22, opacity=1.0,
                       preset="bristle", spacing=8, jitter=0.35)

# 一条锥形笔：从粗到细；size_end 应小于 size
shape_ops.tapered_stroke(doc, "branch", "M 250 120 C 265 80 290 55 315 40",
                         color="#b9986a", size=14, size_end=1.5,
                         steps=28, preset="round_hard")

out = Path(r"<out>")
plan_io.dump_plan(str(out / "shape.plan.json"), W, H, doc["strokes"],
                  layer_blends={"block": "normal", "branch": "normal"})
arr = RU.render_arr(plan_io.load_plan(str(out / "shape.plan.json"))[0],
                    gpu=False, nthreads=8)[..., :3]
from PIL import Image
Image.fromarray(arr.astype("uint8")).save(out / "shape.png")
~~~

要点：

- 这只是“一块 + 一枝”的调用演示，不是一张画的构图；层怎么分仍按 §6.1。
- spacing 默认是 size 的 0.45 倍；想更实就减小，想更透就增大。
- fill_polygon 默认沿多边形边补一圈（edge=True），减少圆笔触的锯齿边。

## 2. 参数与边界

| 参数 | 作用 | 典型 |
|---|---|---|
| polygon | 多边形顶点 [x, y] | ≥3 个点；可凹；自动闭合 |
| size | 笔刷直径 | 块面 12–40；细节 2–8 |
| spacing | 撒点间距 | 默认 0.45×size；越小越实、越慢 |
| jitter | 位置/方向抖动 | 0.2–0.5；0 = 机械网格 |
| edge | 是否补边 | True 减少锯齿；平涂大底可设 False |
| d | SVG path | 与 draw_path 同一套解析 |
| size_end | 末端笔径 | 鹿角尖 1–2；枝干 2–4 |
| steps | 分段数 | 16–40；越大越连续、笔触越多 |

## 3. 和原有算子的关系

helper 只负责“造形”，收边/光照/细化仍用原来的 20 个算子：

~~~python
# 先造形，再用原有 ops 收边/调色/加细节
ops_json = [
    {"op": "set_layer_hue", "layer": "branch", "hue": 40, "guidance": 0.2},
    {"op": "densify", "layer": "block", "k": 1.2},
]
~~~

命令行：

~~~powershell
python scripts/paint_cli.py edit out/shape.plan.json --ops out/refine.ops.json --out out/shape.png --fast --save-plan out/shape.refined.plan.json
~~~

## 4. 宿主工具调用

~~~json
{"plan": "out/shape.plan.json", "out": "out/shape.png", "gpu": false}
~~~

- art_paint：plan 只读，输出 PNG；同 plan 逐像素一致。
- art_apply_ops：plan + ops + out；要保留改后的 plan 就加 out_plan。
- art_edit_brief：plan + instruction + out；产出 brief.json 和缩略图，ops 仍由你写。
- compile_ops.py validate：先验 ops，再应用。

## 5. 风格是参数，不是规定

这些只是参数组，不是风格门槛；选一组改到你的题材：

| 取向 | 块面颜色/笔刷 | 锥形笔 |
|---|---|---|
| 剪影优先 | #2a2a30 / flat，size 34 | #4a4038，size 16 |
| 冷月厚涂 | #3f4a5a / bristle，size 28 | #9aa7b8，size 16 |
| 暖褐厚涂 | #7a5a46 / bristle，size 30 | #b9986a，size 18 |

“剪影优先”不是退让：主体结构没把握时，先把剪影画成立，比用毛流掩盖解剖更稳。

## 6. 常见错误

- polygon 少于 3 个点，或点不是 [x, y]：直接报错。
- spacing 太小、size 太大：stamp 数超过 250000，会报 too many stamps；先加大 spacing。
- size_end 大于 size：画出来是“由细到粗”；要鹿角尖细，size_end 应小于 size。
- 把 fill_polygon 当“层结构模板”：它只填一个块面；层怎么分按 §6.1，别照抄示例里的层名。
- 把 tapered_stroke 当笔刷：它是“沿 path 生成普通笔触”，不是新 preset；仍可被 densify/光照处理。
