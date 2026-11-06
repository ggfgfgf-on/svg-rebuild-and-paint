# shape_ops 接口参考（参数语义）

这份文档讲 scripts/shape_ops.py 两个 helper 的**参数含义、约束和组合方式**，不是画法步骤。
它们不是 ops 算子，也不改渲染器；只往 plan 追加普通笔触。

先读这一句：fill_polygon 给的是“块面的第一层”，不是“块面画完了”；tapered_stroke
给的是“一条有粗细变化的笔”，不是“鹿角/腿自动成形”。要体积、颜色变化、关节，
需要用参数组合多次调用，或换用原有算子。

## 0. 共同契约

### 0.1 输入输出

两个函数都接收同一个 plan doc：

~~~python
doc = {"canvas": [W, H], "layers": [], "strokes": []}
shape_ops.fill_polygon(doc, ...)
shape_ops.tapered_stroke(doc, ...)
~~~

- doc 必须至少有 canvas / layers / strokes；layers 可为空。
- 函数**原地追加** doc["strokes"]，并返回同一个 doc；不新建 plan 对象。
- 不会修改已有笔触；不会删除/重排层。
- 目标层不存在时自动 add_layer；已存在时不改 blend。
- 产物是普通笔触，字段与 draw_path 相同；可继续 densify/relight/light_rig/tone_map。
- 不是像素填充，也不是新 preset；不要写进 ops JSON。

### 0.2 坐标系与单位

- canvas 像素坐标，原点左上，x 向右，y 向下。
- polygon 和 path 都是 canvas 坐标；不要传归一化 0..1。
- size/spacing/spread/edge_inset 的单位都是像素。
- 角度单位是度；0 为 +x，90 为 +y（向下）。

### 0.3 层与合成顺序

- helper 按调用顺序建层；层的混合模式只在建层时生效。
- `dump_plan` 默认按“首笔出现顺序”生成 layers，不读取 `doc["layers"]` 的数组顺序；
  可传 `layer_order=[...]` 显式指定（可含没有笔画的空层）。
- `render_plan` 严格按 `doc["layers"]` 合成；所以要么按最终合成顺序调用 helper，
  要么 dump 时传 `layer_order`。
- 不要依赖变量名、dict 顺序或 `add_layer` 的调用顺序。

### 0.4 确定性

- 同 doc、同参数、同 seed 逐字节相同；抖动来自稳定哈希，不用 Python hash()。
- guidance=0 时函数直接返回，doc 逐字节不变。
- 0<guidance<1 时只按比例缩 opacity，不缩几何。

### 0.5 性能

- fill_polygon 的笔数约等于 多边形面积 / spacing²（加边圈）。
- tapered_stroke 的笔数 = steps × copies × 子路径数。
- 撒点超过 250000 会报错；先增大 spacing 或 size。

## 1. fill_polygon

~~~python
fill_polygon(doc, layer, polygon, color=(217, 180, 90), color2=None, size=8.0,
             opacity=1.0, preset="round_soft", spacing=None, jitter=0.35,
             angle=0.0, gradient_angle=0.0, hardness=0.7, blend="normal",
             tag=None, guidance=1.0, seed=0, edge=True, edge_inset=0.0)
~~~

### 1.1 它到底做什么

1. 在多边形 bbox 里按 spacing 铺网格；
2. 每个格点做 jitter，再用点在多边形内测试；
3. 在多边形内的点画一小段笔触（默认长度 0.2×size）；
4. 可选沿多边形边补一圈边笔；
5. 每笔一个颜色（color 或 color→color2 的插值）。

它不是扫描线填充，也不是矢量填充；边缘由笔刷半径和边圈决定。
凹多边形可以；带洞多边形不支持；自交多边形行为未定义。

### 1.2 参数

| 参数 | 类型/默认 | 含义 | 约束/影响 |
|---|---|---|---|
| doc | dict | plan | 原地追加；必须含 canvas/layers/strokes |
| layer | str | 目标层 | 不存在则建层；已存在不改 blend |
| polygon | [[x,y],...] | 多边形顶点 | ≥3 点；自动闭合；可凹；不支持洞 |
| color | rgb/hex | 起始颜色 | 0..255 或 "#rrggbb" |
| color2 | rgb/hex/None | 终点颜色 | 给定时按 gradient_angle 逐笔插值 |
| size | float=8 | 笔刷直径 | ≥0.5；边圈用 0.9×size |
| opacity | float=1 | 单笔不透明度 | 0..1；重叠会叠加，不是整层 alpha |
| preset | str | 笔刷名 | 必须是 paint_native.PRESETS 里的名字 |
| spacing | float/None | 撒点间距 | 默认 0.45×size；越小越实、越慢 |
| jitter | float=0.35 | 位置/方向抖动 | 0..1；0 是规则网格 |
| angle | float=0 | 笔触方向基准 | 度；也控制隔行错位方向 |
| gradient_angle | float=0 | color→color2 方向 | 度；只影响颜色，不影响笔向 |
| hardness | float=0.7 | 笔刷硬度 | 0..1；越大边缘越硬 |
| blend | str="normal" | 新层混合 | 只在建层时生效 |
| tag | str/None | 笔触标签 | 可被 select_tag 选中 |
| guidance | float=1 | 引导强度 | 0 不变；<1 只乘 opacity |
| seed | int=0 | 抖动种子 | 确定性 |
| edge | bool=True | 是否补边圈 | True 减少圆笔触锯齿 |
| edge_inset | float=0 | 边圈内缩像素 | 0=边笔中心在边界；可设 ~0.5×size 防越界 |

### 1.3 color2 的真实语义

color2 不是像素渐变，而是**每笔一个颜色**：

- t = 点在 gradient_angle 方向上的投影，归一化到多边形 bbox 的投影范围；
- 每笔颜色 = color + (color2-color)×t；
- 颜色分辨率由 spacing 决定；spacing 大时会出现色带，需要更细就减小 spacing。
- color2=None 时所有笔同色。

这对“主体受光/背光”有用，但它只改颜色，不改变笔触大小/形状；
体积仍要靠多次 fill（不同 polygon/opacity）或后来的 relight。

### 1.4 opacity 的真实语义

opacity 是**单笔**不透明度，不是整层统一 alpha。
在笔触重叠区会叠加：opacity=0.5 的密集 fill 仍然可能接近不透明。
要整层统一透明度，应在渲染/合成阶段处理，或降低 spacing 密度/减少重叠。

### 1.5 edge 与 edge_inset

- edge=True 时，每条多边形边按 size×0.18 的间距补小段笔触；
- 边笔中心默认落在边界上，圆笔半径会向外溢出约 size/2；
- edge_inset 把边笔整体沿内法线平移，减少溢出；内法线方向由多边形 signed area 判断；
- 凹角/尖角处是逐边平移，不做 miter 连接；极端尖角可能仍有缺口；
- 要完全控制在剪影内，可 edge=False，自己用更小的 polygon 或后续收边。

### 1.6 组合时看哪些参数（不是步骤）

一次 fill 只产生一遍笔触。要改变块面的外观，调这些维度：

- polygon：覆盖范围与轮廓；凹多边形可以，洞不支持。
- color / color2 / gradient_angle：颜色与方向；color2 是逐笔插值，不是像素渐变。
- size / spacing / jitter：笔触密度与规则性；spacing 越小越实、笔数越多。
- hardness / edge / edge_inset：边缘软硬与边圈内缩。
- opacity：单笔透明度；重叠会叠加，不是整层 alpha。
- layer：需要独立调整时换层；只画剪影时一层就够。

遍数、顺序、颜色和层名都不固定；这些参数可以单独或组合使用，按题材决定。

## 2. tapered_stroke

~~~python
tapered_stroke(doc, layer, d, color=(217, 180, 90), size=10.0, size_end=1.0,
               steps=24, opacity=1.0, preset="round_soft", blend="normal",
               tag=None, guidance=1.0, seed=0, copies=1, spread=0.0,
               hardness=0.7)
~~~

### 2.1 它到底做什么

1. 用与 draw_path 相同的解析器把 d 变成折线；
2. 按弧长等分成 steps 段；
3. 每段画一笔普通笔触，笔径从 size 线性收到 size_end；
4. copies>1 时沿每段法线并行复制，spread 控制间距；
5. 相邻段圆头重叠，形成连续锥形。

它是“沿路径的宽度变化”，不是新笔刷，也没有压力/倾斜。

### 2.2 参数

| 参数 | 类型/默认 | 含义 | 约束/影响 |
|---|---|---|---|
| doc | dict | plan | 原地追加 |
| layer | str | 目标层 | 不存在则建层 |
| d | str | SVG path | 同 draw_path；A 弧线近似；可多子路径 |
| color | rgb/hex | 颜色 | 所有段/副本同色 |
| size | float=10 | 起点笔径 | ≥0.5 |
| size_end | float=1 | 终点笔径 | 要尖端细就 < size；大于 size 是反向锥 |
| steps | int=24 | 分段数 | 2..512；越大越连续、笔数越多 |
| opacity | float=1 | 单笔不透明度 | 重叠会叠加 |
| preset | str | 笔刷名 | 必须是合法 preset |
| blend | str="normal" | 新层混合 | 只在建层时生效 |
| tag | str/None | 笔触标签 | select_tag 可用 |
| guidance | float=1 | 引导强度 | 0 不变；<1 只乘 opacity |
| seed | int=0 | 保留 | 当前无随机抖动；结果确定 |
| copies | int=1 | 并行副本数 | 1..64；用于厚度/体积 |
| spread | float=0 | 副本法线间距 | 像素；0 时副本重叠 |
| hardness | float=0.7 | 笔刷硬度 | 0..1 |

### 2.3 笔径插值

对第 k 段（共 n 段），取段中点 t=(k+0.5)/n：

w(t) = size + (size_end - size) × t

每段用这个 w 作为该笔的 size。相邻段圆头重叠，视觉上是连续锥形；
但它是分段常宽，不是解析锥形；steps 越大越平滑。

### 2.4 copies 与 spread 的真实语义

- copies=1：一条中心线。
- copies>1：沿每段法线偏移 off=(c-(copies-1)/2)×spread，复制整条锥形。
- spread 是副本之间的法线距离，不是总宽度；厚度由 copies、spread 与笔径共同决定，没有固定比例。
- 副本同色同宽；要不同颜色/宽度，分多次调用，不要指望 copies 自动分层。

### 2.5 与 draw_path 的区别

draw_path 是一条 path = 一笔，笔径恒定；tapered_stroke 是一条 path = 多段笔触，笔径变化。
两者都往同一层追加普通笔触，可混用；选择取决于你要恒定笔径还是变化笔径。

## 3. 组合与分层（语义，不是配方）

- fill_polygon 产生“面”的笔触；tapered_stroke 产生“有宽度变化的线”的笔触。
- 块面的体积/颜色变化，通过 polygon、color、color2、gradient_angle、opacity、edge 等参数组合实现；遍数与顺序由题材决定。
- 线的厚度，通过 copies/spread 或同一条 path 多次偏移调用实现；参数没有固定配方。
- 需要独立调整颜色/透明度/位置时，把不同遍写到不同 layer；只画剪影时一层就可以。
- 层数不是质量指标；不要为了“分层”硬拆。

## 4. helper 的边界（不是引擎缺陷）

shape_ops 只把普通笔触追加进 plan，不改变渲染器，也不替代原有 20 个算子。下面几条是 helper 的刻意边界或接口约定；其中层序已经补了显式入口：

- **单环**：fill_polygon 用单个 ring 做“点在多边形内”；带洞/多环剪影拆成多个 polygon 或多个层。引擎没有“路径布尔填充”这一步。
- **color2 逐笔**：plan 里一笔只有一个 color；像素级渐变要靠更密的 spacing/更多笔触近似，不是一次 fill 的像素插值。
- **体积/光**：helper 不生成 height/normal；要光照用原有 relight_dir / light_rig / tone_map 叠在产物上。2D 合成没有真实颜料厚度，这是引擎模型，不是 helper 漏做。
- **压力**：渲染器会读 points 的 pressure（实测 0.2 vs 1.0 会改变 alpha）；helper 当前固定写 1.0，所以它本身不生成压力曲线。tapered_stroke 的宽度锥形用 size 插值，不依赖 pressure；要压力曲线直接写 points，或扩展 helper。
- **倾斜**：plan 没有倾斜笔尖字段，渲染器是 2D stamp，没有这个参数；这是引擎边界，不是 helper。
- **层序**：dump_plan 不给 layer_order 时按首笔；传 layer_order 可显式指定（可含空层）；render_plan 严格按 doc["layers"] 合成。helper 建层仍按调用顺序。
- **边圈**：edge 是沿多边形边逐段 stamping，不是 miter 路径偏移；凹角/尖角可能留缺口。复杂轮廓用 edge=False + 多 polygon，或自己补边。
- **blend**：helper 只在新建层时使用 blend；已有层不会被改。要改已有层，直接改 doc["layers"] 里该层的 "blend" 再 dump/load；没有 set_layer_blend 算子，也不需要重建。

这些不是 20 个算子的缺陷；算子的完整能力见 [API.md](API.md)。

## 5. 验证方式

~~~python
plan_io.dump_plan(path, W, H, doc["strokes"], layer_blends={...})
arr = RU.render_arr(plan_io.load_plan(path)[0], gpu=False, nthreads=8)
# 同参数再跑一遍，ops.canonical(doc) 应相同
~~~

契约测试：python tests/test_shape_ops.py

| 契约 | 测试 |
|---|---|
| 填充不露底、确定性 | `test_fill_polygon` |
| guidance=0 不改 doc | `test_fill_guidance_zero` |
| 锥形笔径、确定性 | `test_tapered_stroke` |
| color2 渐变 + hardness | `test_fill_gradient_and_hardness` |
| copies/spread 副本数、笔径 | `test_taper_copies_and_hardness` |

不另设“画法示例”文档：参数行为由这些测试执行验证；需要看调用方式，读函数签名与 docstring，不要复制某个题材的完整构图。
