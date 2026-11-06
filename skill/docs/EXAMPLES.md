# 示例：格式样板、起手顺序与层堆栈

> 读法：这是**案例集**，不是规范模板。参数语义见 [SHAPE_API.md](SHAPE_API.md)；入口与调参见 [USAGE.md](USAGE.md) 第 4 节。示例可以直接跑，但层名、层数、顺序、颜色都应按题材换——不要因为示例是 4 层就画 4 层。

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

## 2. 四步起手：铺底 → 立形体 → 细节 → 加光/压角

这是一条**常见起手顺序**，不是四道必须工序。它把“起稿”拆成四类互不干扰的动作，方便每轮只看一类变化。

| 步骤 | 解决什么 | 典型层 | 典型 blend |
|---|---|---|---|
| 1 铺底 | 不露底，先定整体明度/色温 | bed | normal |
| 2 立形体 | 主体轮廓与大的块面关系 | band | normal |
| 3 细节 | 边缘、纹理、局部结构 | band 或 detail | normal |
| 4 加光/压角 | 光晕、受光、暗角，把视线收回来 | glow / vignette | screen / softlight |

一个最小骨架（层名只是这一版的例子，按题材换）：

~~~python
import plan_io, render_util as RU

W, H = 1280, 960
strokes = []

def S(layer, pts, size, color, opacity, preset="round_soft", hardness=0.3):
    strokes.append({
        "layer": layer, "preset": preset,
        "size": float(size), "opacity": float(opacity),
        "color": [float(c) for c in color], "hardness": float(hardness),
        "points": [[float(x), float(y), 1.0] for x, y in pts],
    })

# 1) 铺底：先让底不露
S("bed", [(-40, H * 0.5), (W + 40, H * 0.5)], H * 3,
  [20, 24, 46], 1.0, "round_hard", 0.9)

# 2) 立形体：主体写进 band；这里只给一条，实际按造型写循环
S("band", [(W * 0.35, H * 0.75), (W * 0.52, H * 0.35),
           (W * 0.68, H * 0.75)], 180, [92, 68, 52], 1.0, "round_hard", 0.8)

# 3) 细节：边缘、纹理；可以和 band 同层，也可以单独 detail 层
S("band", [(W * 0.40, H * 0.62), (W * 0.50, H * 0.50)],
  24, [150, 112, 82], 0.5, "bristle", 0.5)

# 4) 加光/压角：screen 提亮，softlight 压暗角
S("glow", [(W * 0.50, H * 0.30), (W * 0.52, H * 0.28)],
  220, [255, 210, 140], 0.06, "round_soft", 0.2)
S("vignette", [(W * 0.5, H * 0.5), (W * 0.5, H * 0.5)],
  H * 1.1, [0, 0, 0], 0.05, "round_soft", 0.1)

blends = {"bed": "normal", "band": "normal",
          "glow": "screen", "vignette": "softlight"}
plan_io.dump_plan("start.plan.json", W, H, strokes, layer_blends=blends)
arr = RU.render_arr(plan_io.load_plan("start.plan.json")[0], gpu=False, nthreads=8)
print("strokes", len(strokes), "shape", arr.shape)
~~~

这段代码只演示“四类笔触分别落在哪一层”，不保证像；形体、颜色、笔数都要按题材另写。

### 什么时候不要四步

- 只画剪影/图标：bed + band 就够，glow/vignette 可以不要。
- 主体需要单独调色/加毛/加轮廓光：把 band 拆成 subject / fur / rim，或把 glow 拆成主光/轮廓光。
- 平涂风格：细节可以并进 band，不必为了“步骤完整”硬加层。
- 需要独立调透明度的光：单独一层 screen；和主体混在一起以后很难只改光。

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
