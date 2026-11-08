# 示例：格式样板、起手顺序与层堆栈

> 读法：这是**可选案例集**，不是默认阅读路径，也不是规范模板。默认先读 [USAGE.md](USAGE.md) 第 4 节和 [SHAPE_API.md](SHAPE_API.md)；[STRUCTURE.md](STRUCTURE.md) 是可选的结构验收参考。本文件的层名、层数、顺序、颜色都按题材换。

## 1. 最小格式样板：examples/demo.plan.json

旧文档把它叫“格式样板——照它改就是自己的计划”。更准确地说，它是**字段样板**：照它改 canvas / layers / strokes 的**形状**，不要照抄它的层结构。

现有文件：480×320，4 层，359 笔。

| 层 | blend | 笔数 | 用到的笔刷 | 在这张图里做什么 |
|---|---|---:|---|---|
| bed | normal | 68 | round_hard | 不透明底 |
| band | normal | 186 | round_hard / round_soft | 主体块面 |
| ambient | screen | 24 | round_soft | 环境光晕 |
| highlight | screen | 81 | round_soft | 细节高光 |

~~~json
{
  "canvas": [480, 320],
  "layers": [
    {"name": "bed", "blend": "normal"},
    {"name": "band", "blend": "normal"},
    {"name": "ambient", "blend": "screen"},
    {"name": "highlight", "blend": "screen"}
  ],
  "strokes": [
    {"layer": "bed", "preset": "round_hard", "size": 18.45,
     "opacity": 1.0, "color": [39, 44, 92], "hardness": 0.9,
     "points": [[-20, 2, 1.0], [240, 2.2, 1.0], [500, 2, 1.0]]}
  ],
  "count": 359
}
~~~

最小改动方式：

~~~python
import plan_io, render_util as RU

doc, w, h = plan_io.load_plan("examples/demo.plan.json")
# 换画布、换层、换笔触；不要直接改技能里的 demo 文件，写出自己的 plan
doc["canvas"] = [960, 680]
plan_io.dump_plan("my.plan.json", *doc["canvas"], doc["strokes"],
                  layer_blends={"bed": "normal", "band": "normal",
                                "ambient": "screen", "highlight": "screen"})
arr = RU.render_arr(plan_io.load_plan("my.plan.json")[0], gpu=False, nthreads=8)
print(arr.shape)
~~~

要照抄的只有字段：canvas 是数组、layers 先声明、strokes 顺序即绘制顺序、points 是三元组。层名和层数不是格式的一部分。

## 2. 一个完整 driver（示例，不是模板）

黄昏的红色小船。这个 driver 是 1000×750 / 18 层 / 22,496 笔；层名、构图、笔数、颜色都不是模板。

~~~python

import sys, math, random
from pathlib import Path
SKILL = Path(r"D:\DSHwork\flower\_gh_skill\skill")
sys.path.insert(0, str(SKILL / "scripts"))
import plan_io, shape_ops

Path("out").mkdir(exist_ok=True)
W, H = 1000, 750
rng = random.Random(11)
doc = {"canvas": [W, H], "layers": [], "strokes": []}

def add(layer, points, size, color, opacity=1.0, preset="round_soft", hardness=0.5):
    doc["strokes"].append({
        "layer": layer, "preset": preset, "size": float(size),
        "opacity": float(opacity), "color": list(color), "hardness": float(hardness),
        "points": [[float(p[0]), float(p[1]), float(p[2]) if len(p) > 2 else 1.0] for p in points],
    })

def fill(layer, polygon, **kw):
    shape_ops.fill_polygon(doc, layer, polygon, **kw)

def part(layer, centerline, widths, **kw):
    fill(layer, shape_ops.ribbon(centerline, widths), **kw)

def taper(layer, d, size, color, opacity=1.0, size_end=1.0, steps=20, preset="liner", hardness=0.5):
    shape_ops.tapered_stroke(doc, layer, d, color=color, size=size, size_end=size_end,
                             steps=steps, opacity=opacity, preset=preset, hardness=hardness)

def circle(cx, cy, r, n=16):
    return [(cx + r * math.cos(2 * math.pi * i / n), cy + r * math.sin(2 * math.pi * i / n)) for i in range(n)]

# base + sky
fill("base", [(0, 0), (W, 0), (W, H), (0, H)], color=(15, 20, 30), size=110, spacing=14, opacity=1.0, preset="flat", edge=False)
fill("sky", [(0, 0), (W, 0), (W, 370), (0, 370)], color=(28, 39, 57), color2=(198, 129, 92), gradient_angle=270, size=72, spacing=10, opacity=0.92, preset="round_soft", edge=False)
for i in range(24):
    y = 35 + i * 11
    x = rng.uniform(-80, W)
    w = rng.uniform(180, 520)
    fill("sky", [(x, y), (x + w, y - 5), (x + w + 90, y + 13), (x - 20, y + 18)],
         color=(48 + i * 2, 54 + i * 2, 74 + i * 2), color2=(92 + i * 2, 92 + i, 108 + i),
         size=26, spacing=9, opacity=0.18, preset="round_soft", edge=False)

# far bank + water
fill("far_bank", [(0, 350), (W, 342), (W, 382), (0, 392)], color=(30, 40, 42), color2=(58, 62, 57), size=22, spacing=6, opacity=0.9, preset="bristle")
fill("water", [(0, 365), (W, 358), (W, H), (0, H)], color=(38, 54, 58), color2=(118, 105, 84), gradient_angle=90, size=68, spacing=10, opacity=0.82, preset="round_soft", edge=False)
for i in range(130):
    y = 380 + i * 2.7
    x0 = rng.uniform(0, W)
    ln = rng.uniform(15, 130)
    add("water_ripple", [(x0, y), (x0 + ln, y + rng.uniform(-1, 2))], rng.uniform(1.2, 3.8),
        (142, 138, 118), rng.uniform(0.05, 0.16), "liner", 0.4)

# sand bank
fill("bank", [(0, 475), (120, 455), (230, 470), (300, 510), (270, 610), (160, 680), (0, 690)],
     color=(72, 66, 52), color2=(140, 118, 84), gradient_angle=250, size=38, spacing=8, opacity=0.97, preset="bristle")
for i in range(45):
    x, y = rng.uniform(0, 270), rng.uniform(480, 675)
    add("bank_detail", [(x, y), (x + rng.uniform(-10, 16), y + rng.uniform(-3, 4))],
        rng.uniform(1.5, 4), (168, 142, 98), rng.uniform(0.08, 0.25), "liner", 0.4)

# boat: hull, inner shadow, gunwale, planks
hull = [(330, 480), (395, 465), (650, 465), (720, 482), (700, 520), (620, 548), (420, 548), (355, 520)]
fill("boat_hull", hull, color=(92, 27, 28), color2=(205, 86, 57), gradient_angle=285, size=18, spacing=5, opacity=0.97, preset="bristle")
inner = [(362, 490), (405, 479), (640, 479), (690, 490), (668, 515), (592, 531), (430, 531), (382, 515)]
fill("boat_inner", inner, color=(37, 31, 27), color2=(79, 47, 38), gradient_angle=90, size=15, spacing=5, opacity=0.94, preset="round_soft")
taper("boat_rim", "M 334 480 C 430 468 520 466 650 465 C 690 466 710 473 719 482", 8, (232, 177, 122), 0.85, 2.5, 30, "liner", 0.7)
for k in range(3):
    y = 496 + k * 12
    taper("boat_plank", f"M 370 {y} C 450 {y + 4} 610 {y + 4} 690 {y - 2}", 2.2, (238, 158, 101), 0.35, 0.9, 22, "liner", 0.5)
# mast + rigging
part("boat_mast", [(523, 470), (523, 285)], [13, 7], color=(58, 42, 32), size=9, spacing=4, preset="bristle", hardness=0.7)
taper("boat_mast", "M 523 300 L 650 360", 2.2, (76, 65, 50), 0.6, 1.0, 18, "liner", 0.5)
taper("boat_mast", "M 523 300 L 395 355", 2.2, (76, 65, 50), 0.6, 1.0, 18, "liner", 0.5)
# post
part("post", [(125, 470), (122, 645)], [26, 14], color=(56, 48, 38), size=10, spacing=4, preset="bristle")
# reflection: broken vertical-ish streaks under boat
for i in range(65):
    x = rng.uniform(350, 715)
    y0 = 552 + rng.uniform(0, 10)
    ln = rng.uniform(12, 75)
    add("reflection", [(x, y0), (x + rng.uniform(-8, 8), y0 + ln)], rng.uniform(1.6, 5.2),
        (154, 57, 43), rng.uniform(0.05, 0.18), "liner", 0.35)

# foreground reeds
for side in (-1, 1):
    for i in range(26):
        if side < 0:
            x = rng.uniform(-10, 210)
        else:
            x = rng.uniform(800, W + 20)
        base_y = rng.uniform(570, 735)
        h = rng.uniform(150, 390)
        lean = rng.uniform(-55, 55)
        d = f"M {x:.1f} {base_y:.1f} C {x + lean * 0.2:.1f} {base_y - h * 0.35:.1f}, {x + lean * 0.7:.1f} {base_y - h * 0.72:.1f}, {x + lean:.1f} {base_y - h:.1f}"
        taper("reeds_front", d, rng.uniform(3.0, 7.0), (48, 62, 46), rng.uniform(0.45, 0.8), rng.uniform(0.6, 1.2), 14, "liner", 0.4)
        if rng.random() < 0.4:
            fill("reeds_front", circle(x + lean, base_y - h, rng.uniform(4, 7)),
                 color=(101, 84, 52), size=5, spacing=3, opacity=0.75, preset="round_soft")

# glow + mist + vignette
add("glow", [(790, 290), (808, 296)], 220, (255, 187, 126), 0.10, "round_soft", 0.2)
fill("mist", [(0, 385), (W, 380), (W, 430), (0, 438)], color=(176, 170, 152), color2=(120, 119, 108), size=36, spacing=9, opacity=0.10, preset="round_soft", edge=False)
add("vignette", [(500, 375)], 1250, (8, 10, 15), 0.18, "round_soft", 0.1)

plan_io.dump_plan(
    "out/boat.plan.json", W, H, doc["strokes"],
    layer_blends={"base": "normal", "sky": "normal", "far_bank": "normal", "water": "normal",
                  "water_ripple": "screen", "bank": "normal", "bank_detail": "normal",
                  "boat_hull": "normal", "boat_inner": "normal", "boat_rim": "screen",
                  "boat_plank": "screen", "boat_mast": "normal", "post": "normal",
                  "reflection": "screen", "reeds_front": "normal", "glow": "screen",
                  "mist": "screen", "vignette": "softlight"},
    layer_order=["base", "sky", "far_bank", "water", "water_ripple", "bank", "bank_detail",
                 "boat_hull", "boat_inner", "boat_rim", "boat_plank", "boat_mast", "post",
                 "reflection", "reeds_front", "glow", "mist", "vignette"],
)
print("strokes", len(doc["strokes"]))
~~~

```powershell
python scripts/render_plan.py out/boat.plan.json --out out/boat.png
```

这个 driver 里没有“必须先铺底、再立形体”的步骤：层是按“什么需要独立调色/透明度/位置”拆的，顺序由合成需要决定。换成别的题材时，层和调用都可以不一样。

## 3. 五层常用堆栈：bed → band → ambient → highlight → glaze

旧文档写过“这个顺序保证：底永远盖满、光永远在形体之上、暗角最后收视线”。这句话描述的是**这套顺序的目的**，不是唯一正确层表。

| 顺序 | 层 | blend | 作用 | 什么时候需要 |
|---:|---|---|---|---|
| 1 | bed | normal | 不透明底 | 几乎总是需要 |
| 2 | band | normal | 主体/大块面 | 几乎总是需要 |
| 3 | ambient | screen | 环境光、体积光 | 需要有“光在空气里”的感觉时 |
| 4 | highlight | screen | 受光边缘、碎光 | 需要把注意力拉到主体时 |
| 5 | glaze | softlight | 暗角、整体罩染 | 需要收视线/统一色调时 |

和 demo 的关系：demo 是前四层（没有 glaze）；要压暗角就再加一层 glaze，或者把暗角并进已有的 softlight 层。

变体：

| 目标 | 层结构 | 说明 |
|---|---|---|
| 平涂剪影 | bed / band | 两层就成立；不要为层数硬拆 |
| 带环境光的主体 | bed / band / ambient | 光晕和主体分开调 |
| 带受光边缘 | bed / band / ambient / highlight | 轮廓光独立一层 |
| 带暗角 | 上面 + glaze | 或把 glaze 并在 ambient 的 softlight 里 |
| 主体要单独细化 | bed / band / fur / rim / ambient / highlight / glaze | 毛、轮廓光拆开；不是必须全用 |

层名也可以换成角色名：base / subject / light / tone。名字不影响渲染，**顺序和 blend 才影响合成**。

## 4. 从示例改成自己的计划

不要先问“示例有几层”，先问三个问题：

1. **哪些东西需要独立调色/透明度/位置？** 需要独立调 → 单独一层；不需要 → 并进相邻层。
2. **哪些东西是同一种光？** 同一种 screen 光可以同层；主光和轮廓光要分开调就拆层。
3. **哪些是底/主体/暗角？** 底必须不透明；主体正常合成；暗角最后压。

几个改法：

| 从 | 改到 | 为什么 |
|---|---|---|
| demo 四层 | 加 glaze | 需要暗角收视线 |
| demo 四层 | 去掉 ambient/highlight | 平涂剪影不需要光层 |
| bed/band/glow/vignette | bed/band/ambient/highlight/glaze | 光从“一团”拆成环境光+受光边缘 |
| 单一 band | band/fur/rim | 毛和轮廓光要单独调 |
| 任意层名 | base/subject/light/tone | 名字随意，角色清楚即可 |

改完仍用同一套入口：写 strokes → dump_plan（必要时传 layer_order）→ render_arr 看结果。

两个常改接口：

~~~python
import ops

# 已有层的 blend：用算子，不要重建层
doc = ops.apply(doc, [{"op": "set_layer_blend", "layer": "band", "blend": "screen"}])

# 显式层序（可含空层）；不传则按首笔出现顺序
plan_io.dump_plan("my.plan.json", W, H, strokes,
                  layer_blends={"bed": "normal", "band": "normal",
                                "ambient": "screen", "highlight": "screen"},
                  layer_order=["bed", "band", "ambient", "highlight"])
~~~

## 5. shape_ops：最小调用与接到层里

fill_polygon / tapered_stroke 只负责往某一层追加普通笔触；它们不决定层结构。

### 5.1 最小调用（旧 EXAMPLES 保留）

这段来自旧 EXAMPLES.md，只演示“一块 + 一枝”的调用，不是构图模板；把代码里的 `SKILL` / `OUT` 换成本机路径即可。

~~~python
import sys
from pathlib import Path

SKILL = Path(r"<skill>")
OUT = Path(r"<out>")
sys.path.insert(0, str(SKILL / "scripts"))

import plan_io
import render_util as RU
import shape_ops
from PIL import Image

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

OUT.mkdir(parents=True, exist_ok=True)
plan_io.dump_plan(str(OUT / "shape.plan.json"), W, H, doc["strokes"],
                  layer_blends={"block": "normal", "branch": "normal"})
arr = RU.render_arr(plan_io.load_plan(str(OUT / "shape.plan.json"))[0],
                    gpu=False, nthreads=8)[..., :3]
Image.fromarray(arr.astype("uint8")).save(OUT / "shape.png")
~~~

要点：

- 这只是“一块 + 一枝”的调用演示，不是一张画的构图；层怎么分按第 4 节。
- spacing 默认是 size 的 0.45 倍；想更实就减小，想更透就增大。
- fill_polygon 默认 edge=True，沿多边形边补一圈，减少圆笔触锯齿。

### 5.2 接到这些层里

~~~python
import shape_ops

# 主体块面写进 band；holes 挖洞，pressure 写压力
shape_ops.fill_polygon(doc, "band", outer, holes=[hole],
                       color="#6b4f3f", size=18, spacing=7,
                       pressure=0.8, preset="bristle")

# 枝干/毛流写进 detail 或 band；宽度用 size/size_end，压力另算
shape_ops.tapered_stroke(doc, "detail", "M 250 120 C 265 80 290 55 315 40",
                         color="#b9986a", size=14, size_end=1.5,
                         pressure=1.0, pressure_end=0.4, steps=28)

# 有体积的部件：中心线 + 宽度 -> 多边形，再 fill；不是一条 taper
leg = shape_ops.ribbon([(250, 120), (270, 180), (265, 240)], [26, 18, 9])
shape_ops.fill_polygon(doc, "leg", leg, color="#6b4f3f", size=10,
                       spacing=6, preset="bristle")
~~~

参数语义、holes/pressure 的边界见 [SHAPE_API.md](SHAPE_API.md)。

## 6. 最小验收

示例只保证“能跑、能改、可复现”，不保证“像”。验收至少看三件事：

~~~python
plan_io.dump_plan("check.plan.json", W, H, strokes, layer_blends=blends)
doc2, _, _ = plan_io.load_plan("check.plan.json")
arr = RU.render_arr(doc2, gpu=False, nthreads=8)
print("alpha min/max", arr[..., 3].min(), arr[..., 3].max())
# 同参数再 dump + render，逐像素应一致；alpha 全 0 说明底没盖住
~~~

像不像由参考图、形体和调参决定；示例不替代 [USAGE.md](USAGE.md) 第 5 节的调参手册。

## 7. 凭空画：两条路线（可跑）

| 路线 | 文件 | 实测（本机） |
|---|---|---|
| A 解析场 | `examples/realistic_sky_sea.py` | 9,199 笔 / 4 层 / 960×640 / plan 1.50 MB / 出图 0.24 s |
| B 目标图采样 | `examples/target_sampling.py` | 29,351 笔 / 2 层 / 900×620 / plan 4.79 MB / 出图 0.62 s |

```powershell
python examples/realistic_sky_sea.py sky_sea.png sky_sea.plan.json
python examples/target_sampling.py sampled.png sampled.plan.json
```

> 本节三个脚本（含 §8 的 `incremental_video.py`）都在 `examples/` 下，是**可运行示例**，不是技能入口；生产入口仍是 `paint_cli.py` / `render_plan.py` / host `art_paint`。示例可以复制改，但不承诺作为稳定 CLI。

两个脚本都打印一行 JSON（笔数 / 层数 / 画布 / plan 字节 / 耗时）。这些数字是自查基线：同参数复跑应在同一量级，plan 字节数应稳定；耗时不稳多半是机器负载，不是脚本。

选路线的取舍见 [USAGE.md](USAGE.md) §4.6：A 的形体可改，B 更接近参考图但形体由目标图定死。

## 8. 增量出片（30fps 过程片）

```powershell
python examples/incremental_video.py sky_sea.plan.json sky_sea.mp4 600 30
# {"frames":600,"fps":30,"duration_s":20.0,"size_mb":0.32,"strokes":9199,"render_s":5.97}
```

原则：每笔全片只画一次（`Painter.stroke_batch`），每帧只 `composite` 一次。逐帧用 `render_arr` 重画前 k 笔是 Σk 次落笔，同一量级计划实测 94.1 s（12,701 笔）。API 与参数见 [USAGE.md](USAGE.md) §5.4。

## 9. 返回值自查（不设硬门槛）

- `plan_io.dump_plan` → 写文件并返回笔数；写完 `load_plan` 再渲一次，应逐像素一致（`maxdiff=0`）。
- `render_util.render_arr` → `(H,W,4)`；**alpha=0 处没落笔**，要白纸底就自己按纸色合成。
- `paint_video.py` → JSON 含 `last_frame_vs_full_render_diff_px`，非 0 会直接非零退出。
- `selfcheck.py` → 每条 `[OK/FAIL] 名称 秒 详情`，末行 `RESULT OK|FAIL`。

字段清单见 [USAGE.md](USAGE.md) 附录 B。

## 10. form.py 人像比例示例

这是一个**人像示例**；接口与参数见 [FORM.md](FORM.md)。默认配色和默认规则只适合类似的人像 SVG。

~~~powershell
python scripts/form.py portrait.svg --out form.json --guides preview.png
~~~

默认配色（可在自己的 --parts 里覆盖）：

~~~json
{
  "face": "#f7d8bc",
  "hair": "#2f1d19",
  "outfit": "#b8382c",
  "inner": "#f4efe6",
  "gold": "#d8a94a",
  "skin_dark": "#ecc0a0",
  "iris": "#a8442a"
}
~~~

默认规则（可在自己的 --rules 里覆盖）：

~~~json
[
  ["head_w_over_h", 0.82, 0.13, "头宽/头高"],
  ["eye_line", 0.58, 0.10, "眼线位置"],
  ["neck_over_head", 0.42, 0.12, "颈宽/头宽"],
  ["shoulder_over_head", 1.80, 0.40, "肩宽/头宽"],
  ["head_on_canvas", 0.26, 0.12, "头中心纵向位置"],
  ["head_over_canvas_w", 0.34, 0.08, "头宽/画布宽"],
  ["hair_over_face", 1.80, 1.20, "发量/脸面积"]
]
~~~

注意：--rules 是 form.py 专用格式，不是 structure_specs/*.json。

## 11. structure_metric / candidates 命令示例

接口与返回值见 [STRUCTURE_METRIC.md](STRUCTURE_METRIC.md)。

~~~powershell
# 参考剪影比较
python scripts/structure_metric.py out/cand.plan.json --layers stag --ref ref.plan.json --ref-layers stag,stag_detail --out out/cand.structure.json

# 自己的 spec + landmark 标签；没有 ref 时使用
python scripts/structure_metric.py out/cand.plan.json --layers stag --spec my-spec.json --guide out/guide.png --out out/cand.structure.json

# 多候选联系表/排序
python scripts/structure_candidates.py out/cand_*.plan.json --layers stag --ref ref.plan.json --ref-layers stag,stag_detail --thumb 320 --cols 3 --sheet out/candidates.png --out out/ranking.json
~~~

loop 退出码：

~~~powershell
python scripts/structure_metric.py out/cand.plan.json --layers stag --spec my-spec.json --require-pass
python scripts/structure_candidates.py out/cand_*.plan.json --layers stag --ref ref.plan.json --require-best
~~~
