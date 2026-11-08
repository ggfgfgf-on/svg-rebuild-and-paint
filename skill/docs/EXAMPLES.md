# 示例：字段样板、驱动骨架与接口

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

## 2. 驱动骨架（接口，不是场景）

下面只演示 build script 怎么接上接口。坐标、层名、颜色都是占位；场景、层、part、pass 由你写。

~~~python
import sys
from pathlib import Path

SKILL = Path(r"<skill>")
sys.path.insert(0, str(SKILL / "scripts"))
import plan_io
import shape_ops

W, H = 1200, 900
doc = {"canvas": [W, H], "layers": [], "strokes": []}

def add(layer, points, size, color, opacity=1.0,
        preset="round_soft", hardness=0.5):
    doc["strokes"].append({
        "layer": layer, "preset": preset, "size": float(size),
        "opacity": float(opacity), "color": list(color),
        "hardness": float(hardness),
        "points": [[float(p[0]), float(p[1]),
                    float(p[2]) if len(p) > 2 else 1.0] for p in points],
    })

def fill(layer, polygon, **kw):
    shape_ops.fill_polygon(doc, layer, polygon, **kw)

def part(layer, centerline, widths, **kw):
    fill(layer, shape_ops.ribbon(centerline, widths), **kw)

# 画布底：不属于场景，只保证渲染结果不透明
fill("base", [(0, 0), (W, 0), (W, H), (0, H)],
     color=(24, 24, 24), size=80, spacing=12, preset="flat", edge=False)

# 层只在需要独立控制时拆；顺序即合成顺序
# 新结构用 fill/part；densify 只加现有笔触

# 单个块面：只演示一次 fill 调用，不是构图
fill("block", [(100, 100), (300, 120), (280, 260), (90, 240)],
     color=(120, 90, 70), size=16, spacing=6,
     preset="bristle", edge=True)

# 单个有体积的部件：widths 是全宽；起点可放进承托体内部
part("part", [(200, 220), (240, 320), (235, 420)], [36, 22, 10],
     color=(90, 80, 62), size=8, spacing=5, preset="bristle")

# 更多笔画继续 append；层只在需要独立控制时拆
# add("layer", [(x, y), ...], size, (r, g, b), opacity, preset, hardness)

plan_io.dump_plan(
    "out/plan.json", W, H, doc["strokes"],
    layer_blends={"base": "normal", "block": "normal", "part": "normal"},
    layer_order=["base", "block", "part"],
)
~~~

```powershell
python scripts/render_plan.py out/plan.json --out out/plan.png
```

### 2.1 落盘与验收命令

~~~powershell
python scripts/render_plan.py out/plan.json --out out/plan.png
# {"plan":"plan.json","ops":0,"out":"out/plan.png","size":[1200,900],"luma":...,"render_s":...}

python scripts/plan_io.py check out/plan.json
# {"canvas":[1200,900],"strokes":N,"layers":[...],"non_formula_presets":[],"count_bad":0}
~~~

25% 缩略图（Pillow；尺寸按当前画布取四分之一）：

~~~python
from PIL import Image
im = Image.open("out/plan.png")
im.resize((im.width // 4, im.height // 4), Image.LANCZOS).save("out/plan_25.png")
~~~

注意：`render_plan.py` 落盘是 RGB PNG，alpha 被丢弃；要检查透明/露底，用 `render_util.render_arr` 的 RGBA，或保证底层不透明。

这份骨架没有场景：它只保证接口接对。层怎么拆、画什么，读 [SHAPE_API.md](SHAPE_API.md) 和 [USAGE.md](USAGE.md) 后自己写。

## 3. 层与 blend（接口语义）

`blend` 是合成方式，不是层数规则：

| blend | 语义 |
|---|---|
| `normal` | 正常覆盖；后面的层压前面的层 |
| `screen` | 提亮；适合光、反射、亮部 |
| `softlight` | 压暗/增强对比；适合暗角、整体色调 |

层名不影响渲染；`layer_order` 和 `blend` 才影响合成。不传 `layer_order` 时按首笔出现顺序。

## 4. 驱动注释（可抄进 build script）

~~~python
import ops
import plan_io

# 层：只有需要独立调色/透明度/位置时才拆；顺序即合成顺序
# 光：同一现象同层；主光和轮廓光要分开调时再拆
# 新结构：用 fill / part / draw_path 建新层；densify 只在既有笔触旁加子笔触
# ribbon：widths 是全宽；起点可放进承托体内部，不是从轮廓外开始
# 渲染后看图；不对就改 driver，再渲染

# 已有层改 blend：用算子，不重建层
doc = ops.apply(doc, [{"op": "set_layer_blend", "layer": "layer", "blend": "screen"}])

# 显式层序（可含空层）；层名按场景换
plan_io.dump_plan("out/plan.json", W, H, doc["strokes"],
                  layer_blends={"base": "normal", "subject": "normal",
                                "light": "screen", "vignette": "softlight"},
                  layer_order=["base", "subject", "light", "vignette"])
~~~

这些注释只说明接口事实，不规定层数和顺序。

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
