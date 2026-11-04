---
name: svg-rebuild-and-paint
description: >-
  Two things, both runnable and both measured. (1) Turn a bitmap (PNG/JPEG) into real vector SVG:
  potrace curve reconstruction per colour mask with 2-3x supersampling, flat-colour <path> geometry
  (no <image>, no base64, no gradients, no staircase when scaled), delivered as a static .svg or a
  single-file HTML step player, with a renderer-consistent per-pixel error report (mean / <=5 /
  <=10 / uncovered). The old pixel-run-length mode is DEPRECATED - kept only to reproduce historical
  mean<2 numbers on flat art, never as a default or a deliverable. (2) A deterministic thick-paint
  engine: 8-layer plans, brush-stamp rasterization (CPU + DirectCompute GPU backends), 30 fps video,
  plan-level diff and tile-level incremental repaint (bit-identical to a full rebuild), per-stamp
  colour dynamics, smudge / impasto lighting, PSD and KRA interop.
  Use it whenever the job is "turn this picture into SVG" OR "paint / render / animate this artwork",
  including plain "重绘这个 / 转成SVG / 画/渲染这个 / 出30fps视频" requests.
whenToUse: >-
  The default choice for image-to-SVG redraws - 重绘, 重绘这个, svg重绘, 矢量化, 转SVG, SVG 化,
  描摹, 去掉像素感, 生成可播放的分层绘制过程, 把图片做成纯 path 的单文件 HTML, 批量 PNG/GIF 转 SVG,
  or judging whether a redraw is faithful. English - redraw as SVG, vectorize, trace this image,
  bitmap to SVG, rebuild this screenshot as paths, convert PNG to SVG. Prefer this over generic
  vision or OCR skills when the deliverable is SVG artwork (those are for UI restoration, text
  extraction and element location, not for faithful raster-to-path reproduction).
---

# 像素级 SVG 重绘（与素材无关）

把任意位图重绘成**单文件自包含 HTML**：纯 SVG 平涂 `<path>` + 离散步骤播放器；用逐像素误差闭环验收，不靠目视判断。

### 第三方语义改图（多模态编译）已实现

**默认路径：当前会话的多模态模型自己编译（零 key）**

```
python scripts/compile_ops.py brief plan.json "把背景压深成墨绿夜色，花心加柔和高光" --thumb --out brief.json
# -> {prompt, thumb, canvas}；模型读 thumb + prompt 后直接写出 ops JSON
python scripts/compile_ops.py validate plan.json --ops ops.json
python scripts/render_plan.py plan.json --ops ops.json --out edited.png --gpu
```

实测（本会话模型，未用任何 API key）：luma 25.69 → 23.71，花心出现柔和高光、背景转墨绿。

**背景/局部改色必须用 raster 掩膜 + `--mask-mode all`**：`masks.from_image` 只给颜色连通块的
外接矩形，背景这种"包住主体"的形状会退化成整张画布（于是"只改背景"其实改全部）。
`masks.from_bool(bool_array)` 保存逐像素形状（raster 掩膜），选笔触时按点采样；
`--mask-mode all` 只选"整笔都在掩膜内"的笔触，跨边界的笔触被排除，主体才不会被整笔染色。
实测：背景 (13,25,16) → (3,18,13)、玫瑰 (153,1,11) → (150,1,11)（Δ<2/255），增量与整份重画 diff_px=0。

**可选路径：第三方端点**（给无人值守/批量/其它程序调用）

`compile_ops.py compile <plan> "<指令>" --thumb --provider deepseek|ark|openai [--image path]`：
把计划摘要 + 当前画面缩略图（base64 data URL）发给 OpenAI 兼容端点，拿回 ops JSON，
`validate_ops` 校验后 `apply` 渲染。两条路径共用同一套 ops 契约与 validate/apply。温度为 0、重试 2 次；端点拒图会自动退纯文本；
缺 key 直接报 `not configured` 并把 prompt 打出来（不静默猜）。

`tests/test_compile_model.py` 用本地 mock 端点验证真实 HTTP 往返：请求体里确实带 `data:image/png` 块、
返回按 ops 解析、缺 key 抛错。真实第三方 key 的生成质量尚未实测（本机没有对应 env 变量）。

## 什么时候就是本 skill（别绕路）

用户说「重绘这个 / svg重绘 / 转成SVG / 矢量化 / 去掉像素感」并给了图，**直接走本 skill**：

| 情况 | 归属 |
|---|---|
| 要一张 SVG 画（保真、可分层播放、带误差数字） | **本 skill**（`vectorize.py` / `build_svg.py`） |
| 要 UI 还原、按钮/表单，或从截图生成前端代码 | `vision-skills` |
| 要 OCR、元素定位、像素比对、取色 | `vision-skills` 或视觉工具 |
| 要"凭空画一张图"（无参考图） | 都不适用，手写 SVG |

`vision-skills` 里的 SVG 描摹是**通用素材工具**；本 skill 多了三件事它没有：**可度量的误差闭环**（mean / ≤5 / uncovered）、**分层步骤播放器**、**批量巡检与回归测试**。同样一张图，先跑这里的 `metric.py` 再决定要不要换路线。

## 契约

- 输入：PNG/JPEG，宽×高 ≤ 2MP。JPEG 先解码并**固化无损副本**，之后所有比较只对这份副本（解码器不同结果不同）。
- 输出：`out.html`（播放器）+ `out.preview.html`（同图全量、无 UI，几何评测专用）。`viewBox="0 0 W H"`，每个颜色类一组带 `data-step` 的 `<path>`，内联 JS 播放器（`?step=N` 直达，`data-total` 记录总步数）。
- 禁止：Canvas、位图/base64 内嵌、外链、渐变（`<linearGradient>`/`<radialGradient>`/`fill|stroke="url(...)"`）、任何 CSS transition。像素分支必须 `shape-rendering="crispEdges"`；曲线分支用 `geometricPrecision`。
- 可选后处理 `--edge-soften=N` 会引入一个 `feGaussianBlur` 滤镜，是唯一的 `url(#)` 例外，默认关闭；它不改变路径几何。

## 默认入口：先跑 auto.py（别手调参数）

**除非用户明确给了参数，否则不要自己猜 K / ss / turd。** 一条命令跑完并打印测量对比：

```bash
python <skill>/scripts/auto.py ref.png out.svg
```

它会依次跑 3 个预设（illustration / portrait / photo），每档都真实渲染 + 算 mean / ≤5 / uncovered，按 `score = mean + 0.35·log2(kb/100)` 选一个（精度与体积的加权，不是纯精度），把最好的复制到 `out.svg`，并输出：

```json
{"picked": "photo", "mean": 2.935, "kb": 11252,
 "candidates": [{"preset": "illustration", "mean": 3.343, "kb": 9866, "score": 5.66},
                {"preset": "portrait", "mean": 3.323, "kb": 30000, "score": 6.20},
                {"preset": "photo", "mean": 2.935, "kb": 11252, "score": 5.32}]}
```

| 预设 | K | ss | 额外 | 适合 |
|---|---|---|---|---|
| `icon` | 32 | 2 | — | 图标、扁平插画（`--try-all` 才跑） |
| `lineart` | 24 | 3 | — | 线稿、墨线（`--try-all` 才跑） |
| `illustration` | 128 | 2 | — | 二次元/角色画，均衡默认 |
| `portrait` | 96 | 2 | alpha 环 + 真实色 6 级 + refine 1 遍 | 柔光人像、皮肤过渡 |
| `photo` | 192 | 2 | — | 照片、写实 |

单独跑某档：`auto.py ref.png out.svg --preset portrait --keep runs/`（`--keep` 保留所有候选文件，便于人工对比）。

区域预算也能透传，同一套框会喂给每个候选（否则比较不公平）：

```bash
python <skill>/scripts/auto.py ref.png out.svg --region 150,200,330,430:384 --region-mask hand.png:256,3
```

`--region` / `--region-mask` 都可重复。实测 400×711 人像，illustration 预设：脸区 mean 8.17 → **4.98（-39%）**，体积 5.78MB → 6.94MB，背景不变（5.33）。输出 JSON 会多一个 `regions` 字段回显实际用的框。

**报错或结果明显不对时**：先看 `candidates` 里每档的 `mean`，不是看 `picked`。某档 `mean` 异常高通常说明该预设不适合这类图（例如线稿跑 photo），换预设比调参更快。

## 区域预算：脸用高色数，背景用低色数

```bash
python <skill>/scripts/vectorize.py ref.png out.svg 48 6 1.0 1 0.4 2 0 --region 150,200,330,430:384
```

`--region x0,y0,x1,y1:K[,SS]` 可重复；每块区域用自己的色数、可选自己的超采样**独立重描**，覆盖在全局层之上（内部用 `<g transform="translate(x0,y0)">` 定位，不动全局几何）。全局用低 K 省体积，只给主体/脸花钱。

**K 可以写绝对值，也可以写倍数**：`:384` = 固定 384 色；`:3x` = 全局 K × 3（预设换成 photo/K=192 时它自动变 576）。想“固定脸的精细倍数”用 `Nx`，想“固定输出体积”用绝对值。两者实测一致：全局 K=48 时 `:8x` 与 `:384` 得到完全相同的脸部 mean 5.92。

**非矩形区域**用遮罩：`--region-mask face.png:384,3`（白色=要重绘的像素，黑色=不动；遮罩按原图分辨率给，自动取包围盒后裁剪重描）。

**过程分支同样支持**：`--process-final ... --region 80,40,240,150:96` 会把区域层追加到过程最终画面之上（实测 320×180 冒烟正常，227KB）。

实测 400×711 人像（全局 K=48，ss=2）：

| 配置 | 体积 | 全图 mean | **脸部 mean** | 背景 mean |
|---|---|---|---|---|
| K=48 | 2.5MB | 8.23 | 11.22 | 7.72 |
| K=48 + 脸区 K=384（ss 继承 2） | **3.55MB** | 7.46 | **5.92（-47%）** | 7.72（不动） |
| K=48 + 脸区 K=384, **ss=3** | 4.28MB | 7.36 | **5.24** | 7.72 |
| K=48 + 脸区 K=384, **ss=4** | 5.1MB | 7.31 | **4.90** | 7.72 |
| K=48 + 遮罩区 K=384, ss=2 | 3.31MB | 7.59 | 6.79 | 7.72 |
| K=96（全局提升） | 4.02MB | 6.35 | 8.94 | 5.90 |

**同样的体积，区域预算把脸做到 5.93；全局加 K 只到 8.94。** 背景变好是全局加 K 的副作用，代价是脸仍然糊。人像/角色画应该用前者。

注意：区域按**原图像素坐标**，它不识别五官——**框/遮罩由模型或人给，脚本只执行**。多块区域依次叠上去，后写覆盖先写。

## 第四入口：iterate.py（渲染 → 定位偏差 → 打补丁）

前三个入口都是"给参数、生成一次"；这个入口**看着自己的输出改**。

```bash
# 自动档：最差分块自动加预算
python <skill>/scripts/iterate.py ref.png out.svg --base "48 6 1.0 1 0.4 2 0" --rounds 3

# 模型档：补丁由模型看完分块报告后手写，脚本只执行
python <skill>/scripts/iterate.py ref.png out.svg --base "..." --patch patch.json
```

补丁是数据，不是代码，所以模型可以直接产出：

```json
{"ops": [{"op": "region", "box": [150, 200, 330, 430], "k": 384, "ss": 2},
         {"op": "region", "box": [120, 440, 300, 560], "k": 192}]}
```

补丁支持四类 op：

| op | 作用 | 典型用途 |
|---|---|---|
| `region` | 局部加色数/超采样 | 脸、手、主体 |
| `style` | 用新参数**整幅重跑**（K/turd/alphamax/dil/blur/ss） | 换画风：平涂、描边、粗线 |
| `remap` | 改颜色：`from/to/tol` 或整幅贴合 `to_palette` | 统一色调、海报化、限定色板 |
| `draw` | 注入模型自己写的 path `d` | 补细节、加签名、**从零作画** |
| `drop` | 删路径：`ids:[..]` 或 `where:{fill:"#rrggbb"}` | 去掉描歪的线、噪点层 |
| `recolor` | 改路径颜色（同上选择器） | 局部修色 |
| `redraw` | 替换某条路径的 `d`（和可选 `fill`） | 改一根线的走向 |
| `reorder` | 把选中的路径移到最上层 | 修遮挡顺序 |

**路径句柄**：只有出现 `drop/recolor/redraw/reorder` 时才给每条 `<path>` 注入 `id="pN"`（发射顺序），默认输出**一字节不变**。`ids` 用的就是这个 N。

`redraw` 只换几何**不换层级**——它留在原 z 序上，所以“补一条盖住别人的线”要用 `draw`，“改一根已有线的走向”才用 `redraw`。

**从零作画**：`--blank 420x300` 从空白画布起步，内容全靠 `draw` 给（不需要参考图、不跑描摹）：

```bash
python <skill>/scripts/iterate.py - out.svg --base "2 6 1.0 0 0 1 0" --blank 420x300 --patch draw.json
```

实测 7 条 path / 0.8KB 出一张完整风景（天空、太阳、云、两层山、两只鸟）。
算子层的入口是 `draw_path`（见「计划空间改画」小节）：它把同一段 path 变成**计划里的笔触**，
所以从零加笔也能走 `guidance` / 掩膜 / 脏矩形增量，不必重跑描摹。**能画的是符号化的东西**：图标、logo、UI 元素、扁平插画、示意图；照片级和复杂人像仍然不行（token 带宽 + 没有先验）。

**默认是 ask 模式**（模型在环）：跑完一版就停下，吐决策点，不自己往下改。

```json
{"mode":"ask","mean":8.2331,"kb":2520.7,
 "worst_tiles":[{"box":[200,356,267,474],"mean":17.083,"suggest_k":96}],
 "preview_png":"out.preview.png",
 "patch_schema":{"ops":[{"op":"region","box":[100,100,200,200],"k":384,"ss":2}]},
 "next":"iterate.py ref.png out.svg --base \"48 6 1.0 1 0.4 2 0\" --patch <file>"}
```

模型看 `worst_tiles` + 预览图 → 写补丁 → 再跑一次 → 又拿到新决策点。

**补丁不跨轮累积**：每轮都是"底图参数 + 本轮全部补丁"重建，后面几轮必须把之前的区域一起带上（`--patch` 可重复给多个文件，按顺序叠加）；ask 输出里的 `applied_ops` 就是本轮实际生效的补丁，下一轮直接拼接它。


### 元素级预算（推荐，替代矩形/多边形）

```bash
# 自动把画面切成连通色块（元素），按面积+内部复杂度给每块独立色数
vectorize.py ref.png out.svg 8 6 1.0 1 0.4 1 0 --elements 16:800:384,2
#                              颜色数:最小面积:单元素上限[,ss]
```

边界落在真实色块轮廓上，所以**没有矩形接缝**。实测 ref-star.png：55 个元素 / mean 9.14 / 2.9MB（矩形区域方案 11.09，平铺 6.87）。
代价：分割错了会把一块底色当成元素高预算描（额头/嘴部会出现杂色条），需要调 `最小面积` 与 `单元素上限`。


### auto.py 也能用新预算（已透传）

```bash
auto.py ref.png out.svg --elements 16:800:384,2 --elements-refine 1
auto.py ref.png out.svg --tile 2x3:192,2 --palette-shared 192
auto.py ref.png out.svg --region-poly "40,20 280,20 280,160 40,160:128,1"
```

实测：`--preset illustration --elements 16:800:384,2` 在 ref-star 上 mean **5.14**（8.2MB），比我手调的 6.08 更好。

### 三个组合项：原来的坑与现在的修法

| 组合 | 原问题（实测） | 修法 | 修后实测 |
|---|---|---|---|
| `--elements` + `--palette-shared` | 实测三种做法都变差（强制 9.20 / snap 5.78 / 不共享 4.25） | **不兼容，直接忽略并告警** | 保持 4.25，不降质 |
| `--tile` + `--palette-shared` | 共享板让平铺变差（4.23 → 5.23），而接缝已由 2px 重叠修掉 | 忽略并告警 | 保持重叠修法（0.62–0.74×） |
| `paint_process.py` 透明源 | 透明孔被涂成 245,239,226，alpha 255 | 描摹仍用压平图，但输出整幅裁到 alpha clipPath（保留透明） | 透明孔 bbox 与源完全一致；`--bg` 可换压平色 |

`--region-feather` 对矩形区域原本也是空的（dilation 被裁切框夹住），现在改为扩框，矩形区域接缝同样被盖住。

## 笔感扩展（brush_ext）

`scripts/brush_ext.py` 补齐六项笔感与互操作能力，全部是确定性 numpy 实现（同输入同输出）：

| 函数 | 参数 | 作用 |
|---|---|---|
| `smudge_rgba(rgba, path, radius=16, amount=0.6, hardness=0.5, step=None)` | radius 拖色半径、amount 强度、hardness 边缘 | 沿笔迹把底色往前推（拖色），会拖动 alpha |
| `impasto_rgba(rgba, light=(-0.44,0.90,0.35), height=1.0, spec=0.6, shininess=24, ambient=0.55, combine="overlay")` | 光照方向、高度倍率、高光强度 | 用 alpha 当高度场做浮雕+高光，alpha 不变 |
| `apply_color_dynamics(strokes, hue=0.02, sat=0.15, val=0.12, seed=0)` | 色相/饱和/明度幅度、seed | 逐笔触确定性色彩抖动 |
| `grain_variant(tip, grain, amount=0.5, scale=1.0, seed=0)` | seed 决定纸纹相位 | 同一 grain 生成不同相位的笔尖变体 |
| `apply_velocity_response(strokes, gain=0.3, boost=0.0, ref=None)` | gain 越大越快越细淡 | 按笔速缩放尺寸/不透明度 |
| `read_kra(path)` / `read_psd(path)` | — | 读回分层。PSD 走 psd-tools → 内置 8BPS 解析器（8/16bit、raw+RLE、unicode 名、图层组）→ PIL 合成图兜底 |

```python
import brush_ext as bx
rgba = bx.impasto_rgba(bx.smudge_rgba(rgba, path, radius=18, amount=0.6), light=(-0.4, 0.9, 0.3))
strokes = bx.apply_velocity_response(bx.apply_color_dynamics(strokes, seed=7), gain=0.35)
```

验收：`python tests/test_brush_ext.py`（31 条）、`python tests/test_effects_pipeline.py`（5 条）、`python tests/test_stamp_color.py`（17 条，含 GPU）、`python tests/test_gpu_tile.py`（4 条）、`python tests/test_fx_gpu.py`（10 条，含精度与性能）。

### 效果如何参与增量失效

效果一律先算矩形，再只重算矩形（带 halo），不要整图重跑：

```python
r = bx.smudge_rect(path, radius=18)          # 拖色影响范围
r = bx.impasto_rect(layer_rgba)              # 浮雕影响范围（非透明区 +2px）
out, used = bx.apply_in_rect(img, r, lambda c, ox, oy: bx.smudge_rgba(
    c, path - [ox, oy, 0], radius=18), pad=30)          # 单块重算
out = bx.apply_effect_tiled(img, dirty_rects,              # 缓存脏区批量重算
                            lambda c, ox, oy: bx.impasto_rgba(c), pad=2)
```

- **impasto 是像素局部**的：分块（pad=2）与整图逐位相同，可直接吃缓存脏区列表。
- **smudge 依赖整条笔迹**：不能把一笔拆到两个矩形里分别拖，必须按「笔迹影响矩形」整体重算。
- 两者都不写回 painter 的图层缓冲，属于后处理；若同一帧既有 tile 重绘又有这些效果，顺序必须是「先 tile 重绘 → 再效果补算」。

### 逐 stamp 色彩（引擎级，CPU）

```python
p.set_color_dynamics(0.35)   # 同一笔内每个 stamp 确定性微调明度/冷暖
p.set_color_dynamics(0.0)    # 关闭：与旧行为逐位一致
```

- 改动在 `paint_core.cpp` 合成热路径：Stamp 多存一个序号，scratch 旁挂「每像素获胜 stamp 序号」，合成时用 `hash2i(seed, k, 16/17)` 抖动 RGB。
- 不变式（`tests/test_stamp_color.py`）：**只改 RGB，alpha 逐位不变**；确定性；关闭时与旧渲染逐位一致。
- 实测（947×1147、8 层、57,813 笔，**公式笔尖**）：关 2.41 s / 开(0.35) 2.97 s（+23%），RGB 最大差 51。位图笔尖慢一个量级：同规模 57.8k 笔的 `painting.plan.json` 整份重画 14.5 s（见「计划空间改画」小节）。
- **GPU 后端同样支持**（`paint_gpu.cpp`）：kernel 用 `InterlockedMax` 归并，所以把键打包成 `(alpha16 << 16) | (65535 - stamp序号)` —— alpha 占高 16 位保序，低 16 位存「最小序号」做 tie-break，over 阶段再用该序号从 stamps 缓冲取回抖动桶。这样上层与 CPU 一样是「先到先得」，实测同一笔 CPU/GPU 均值差 0.001（`tests/test_stamp_color.py`）。
- 关闭时走原来的 `asuint(a)` 分支，逐位不变；开启时才付 16bit alpha 量化代价（8bit 输出上看不出来，alpha 最大差 0）。

### GPU compute 版（paint_fx.dll）

smudge / impasto 有 DirectCompute 实现，全程 float32，**不做任何 8bit/16bit 量化**：

```python
bx.smudge_rgba(img, path, radius=24, amount=0.7, backend="gpu")
bx.impasto_rgba(img, backend="gpu")          # 也可直接用 paint_fx_native.*
```

| 画布 | impasto CPU → GPU | smudge CPU → GPU |
|---|---|---|
| 1024² | 0.059 s → 0.041 s（1.4×） | 0.053 s → 0.007 s（8.1×） |
| 2048² | 0.236 s → 0.059 s（4.0×） | 0.113 s → 0.024 s（4.7×） |
| 4096² | 0.951 s → 0.140 s（6.8×） | 0.308 s → 0.098 s（3.1×） |

- **精度**：与 CPU 版最大差 1.37e-6（smudge）/ 4.77e-7（impasto），是 float32 舍入（约 1/2800 个 8bit 步长），不是量化降级；alpha 语义不变，结果确定性可复现。
- 设备/缓冲按尺寸缓存复用（每次新建 D3D 设备要 0.1 s+，批量调用必须缓存），`paint_fx_native.clear_cache()` 可释放。
- 默认 `backend="cpu"`（CPU 是逐位基准）；大画布/批量改画时显式切 GPU。
- 验收：`python tests/test_fx_gpu.py`（一致性 + 1024/2048/4096 性能）。

### 计划级近似（跨后端可用）


`split_color_dynamics(strokes, segments=6, hue=0.03, sat=0.12, val=0.10, seed=0)` 把每笔按点列切成若干子笔触、逐段换色，效果等价于笔触内部逐 stamp 抖动，**不改** CPU/GPU kernel。代价是笔触数 × segments（GPU 侧按笔触批处理，注意批量）。

CPU 侧引擎级实现见上一节；GPU 侧仍需改 HLSL。

## 局部改画（incremental repaint）

改一块而不重画全图，三层能力：

| 模块 | 作用 |
|---|---|
| `scripts/plan_store.py` | plan 二进制快存：每层 `pts / offs / params / rids / sid`；`offs` 是 **float 索引**，末尾带总长度 |
| `scripts/paint_diff.py` | 差量：按稳定笔触 id 记录改参数 / 改几何 / 增删（含中段删除），跨版本结构差异也能还原 |
| `scripts/tile_inc.py` | 只清一个矩形、只重放与它相交的笔触；CPU/GPU 同一套接口，支持多块脏区 |

```python
import plan_store, paint_diff, tile_inc

plan, meta = plan_store.load("art.plan.npz")
d = paint_diff.make(plan, edited_plan, spec)      # 只记录改动，KB 级
paint_diff.save(d, "edit.diff.npz")
plan2 = paint_diff.apply(plan, paint_diff.load("edit.diff.npz"), spec)

tile_inc.rebuild(p, plan2, spec, {"band": [(320, 160, 479, 319)]}, base_of, gpu=True)
```

判定标准不是"看起来对"，而是 **局部重绘结果 == 整份重画结果逐位相同**：

    python tests/test_incremental.py

四个断言：差量 apply、单 tile、多 tile、缓存补片。

边界：

- 区域裁剪逐像素（`rv == region_id || rv == -2`）：跨区域笔触只更新目标区域，区域外保持原样；
- 同一后端逐位可复现；CPU 与 GPU 目前**不是**视觉等效：实测 99.0% 像素不同、max_delta 187（见 GPU 章节）；
- 成本与「脏区内的笔触数」成正比，与整幅尺寸无关。

### 独立工具（不参与默认管线）

| 脚本 | 用途 | 用法 | 依赖 |
|---|---|---|---|
| `seal_gaps.py` | 补相邻色块之间的细缝（只补薄/小块，不碰白色衣服） | `seal_gaps.py in.svg|in.html in.png out [max_comp]` | render.py 先出同尺寸 PNG |
| `steps.py` | 把带 `data-step` 的播放页导出成分帧图 | `steps.py player.html outdir --cols 4` | render.js |
| `smooth_svg.py` | vtracer 引擎的替代描摹（外部库） | `smooth_svg.py ref.png out.svg [precision speckle layer corner]` | `pip install vtracer` |

`seal_gaps.py` 现在也能从主入口调用：`vectorize.py ... --seal-gaps[=MAXCOMP]`（默认 24），只填薄缝，不改已有路径。

### 元素级再细化（`--elements-refine R`）

```bash
# 元素预算 + 只重描残差最大的 25%，可迭代 R 轮
vectorize.py ref.png out.svg 24 6 1.0 1 0.4 2 0 --elements 16:800:512,2 --elements-refine 1
```

每轮：渲染 → 按元素掩码量残差 → 最差 25%（误差 >6 级）把 K 翻倍、ss+1 重描 → 覆盖到原图之上。只重画没画好的部分，成本远低于整体重跑。

| 方案 | mean | 体积 |
|---|---|---|
| 一次性 K=48/ss2 | 8.23 | 2.5MB |
| 平铺 2×3 + 共享调色板 | 6.87 | 3.6MB |
| 元素（K=8 底图） | 9.14 | 2.9MB |
| 元素 + refine×2（K=8 底图） | 8.98 | 3.4MB |
| **元素 + refine×1（K=24 底图）** | **6.65** | 3.9MB |

### 接缝默认处理（有区域时自动生效）

- `--region-snap-tol 10`：区域颜色若底图能表达（距底图色 ≤10）就吸附成底图色 → 边界不跳色
- `--region-feather 2`：掩码外扩 2px，把边界推进到颜色一致处
- `--no-region-snap` / `--region-feather 0` 可关闭

### 矩形边界 / 接缝 / 不规则区域

区域默认**各自算调色板**，所以叠在低 K 底图上会看到矩形色差。三种解法：

```bash
# ① 整幅平铺：没有"低预算底图"，接缝只落在同等预算的相邻块之间
vectorize.py ref.png out.svg 8 6 1.0 1 0.4 1 0 --tile 2x3:192,2 --palette-shared=192

# ② 不规则区域：多边形（点用 x,y 空格分隔）或任意 PNG 遮罩
vectorize.py ref.png out.svg 8 6 1.0 1 0.4 1 0 \
  --region-poly "170,180 250,160 300,320 280,470 195,480 150,350:384,2" --region-feather 4
vectorize.py ref.png out.svg 8 6 1.0 1 0.4 1 0 --region-mask face.png:384,2

# ③ 共享调色板：多个区域都用同一份 palette，边界不再跳色
--palette-shared=192     # 取整幅图 192 色作为所有区域（含平铺）的公共调色板
--region-feather 4       # 掩码外扩 4px，把接缝推进到颜色一致的地方
```

实测：2×3 平铺 + 共享调色板，mean 6.87（比一次性 K=48 的 8.23 还低），3.5MB；只给脸做多边形区域（底图 K=8）时 mean 20.56，但脸是清晰的——预算花在哪儿一目了然。

临摹一张图的推荐节奏：低 K 底稿（`--base "8 6 1.0 1 0.4 1 0"`）→ ask 看最差块 → 按 `suggest_k` 给区域加预算 → 每轮带上全部区域 → 需要更锐利时再叠 `ink.py --style line --line-occlude` 线稿层。

**从零作画也走同一个闭环**（没有参考图，改为写/改自己的 path）：

```bash
# 第一轮：空画布 + 全部图层
iterate.py - yy1.svg --blank 420x600 --patch spec.json
# 之后每轮：以上一轮的 svg 为底稿，只发改动
iterate.py - yy2.svg --from-svg yy1.svg --patch fix.json
```

**定形 + 量比例**（没有参考图时唯一的"尺子"）：

```bash
python scripts/form.py yy4.svg --out form.json --guides preview.png
# ratios: head_w_over_h / eye_line / neck_over_head / shoulder_over_head
#         head_on_canvas / head_over_canvas_w / hair_over_face
# failed: 哪几条超出容差
```

`--guides` 写一张带「头高刻度尺 + 中轴 + 脸框 + 眼线」的预览图，只用于看，不进成品。比例不对时用 `scale` / `move` 补丁整体调形体，而不是重画每个点。

ask 在有参考和无参考两种情况下都会停住，输出 `preview_png`、路径数、颜色数，以及下一轮命令；`--base` 可省。想回到脚本自己挑块（无模型），加 `--mode auto`。

自动档规则：按 `--grid`（默认 8）分块算渲染误差，每轮挑最差 `--top` 块，预算取 `global_K × k_mult^round`（默认 2×，上限 `--k-max`），收益低于 `--min-gain`（默认 0.05）就停。已覆盖的块不会重复补。最终只保留最好的一轮。

实测 400×711 人像（base K=48 ss=2）：

| 轮次 | mean | 体积 | 补丁 |
|---|---|---|---|
| 0（无补丁） | 8.23 | 2.5MB | — |
| 1 | 7.48 | 3.1MB | 4 块 K=96 |
| 2 | **6.80** | 3.9MB | 4 块 K=192 |

模型补丁档（两个框：脸 384/ss2 + 上身 192）：8.23 → **6.91**，3.9MB，一轮完成。

## 前置分层：ink.py（线稿 / 黑白海报）

不是 vectorize 的开关，而是**给模型调用的辅助件**：一次只出一种层，打印 JSON（前景像素 / 路径数 / 体积），模型比完数字再决定要不要叠。

```bash
# 黑白海报层：实心块直出，最省
python <skill>/scripts/ink.py ref.png --style poster --method sauvola --out poster.svg

# 线稿层：边缘 → 细化 → 描边；dilate=0 是发丝线
python <skill>/scripts/ink.py ref.png --style line --lo 110 --hi 240 --dilate 0 --out line.svg

# 两层叠起来（poster 打底，line 压上）；不给 --out 时写 outdir/composed.svg，不覆盖单层
python <skill>/scripts/ink.py ref.png --style both --outdir layers

# 彩色 poster 层（k 色调色板，按面积分层）
python <skill>/scripts/ink.py ref.png --style poster --poster-colors 8 --turd 8 --out pc8.svg

# 遮挡/冗余裁剪：底图已经还原好的地方，线就不再画（可选再给前景遮罩）
python <skill>/scripts/ink.py ref.png --style line --line-per-stroke \
  --line-occlude base.svg --line-occlude-tol 10 --line-clip foreground.png \
  --compose base.svg --out comic.svg

# 彩色底 + 彩色线：先把彩色底生成出来，再把无背景的线层叠上去
python <skill>/scripts/vectorize.py ref.png base.svg 16 4 1.0 1 0.3 2 0
python <skill>/scripts/ink.py ref.png --style line --line-per-stroke --compose base.svg --out comic.svg
```

实测 400×711 人像：

| 调用 | 前景 px | 路径 | 体积 | 观感 |
|---|---|---|---|---|
| poster（sauvola） | 47,851 | 147 | 82.2KB | 黑白海报/木刻，实心块 |
| line（canny 60/160, dilate 1） | 41,908 | 121 | 70.8KB | **偏重**：暗部纹理糊成块 |
| line（canny 110/240, dilate 0） | 9,427 | 168 | 73.2KB | **真线稿**，发丝细线 |
| both 合成 | — | 268 | 153.0KB | 漫画感 |

**线不必是黑的**。三种取色方式：

| 参数 | 效果 |
|---|---|
| `--line-color "#334455"` | 整层统一色 |
| `--line-colors 4` | 在**线条像素**上按参考图聚类，一类线一种色（彩色线稿） |
| `--darken 0.85` | 彩色时压暗，保住"线"的分量感 |
| `--poster-color "#5a3d2b"` | 海报层也可以染色（单色印刷/棕褐） |

第四种是**逐线取色**：`--line-per-stroke` 把 mask 拆成连通块，每块单独描、单独取参考图上的中位色——**每条线颜色都不同**，不用先聚类。

`--line-source edges`（默认）直接描 Canny 边缘，线**更完整**；`skeleton` 走细化，能控制粗细但会切断细线。

实测 400×711 人像：

| 模式 | 路径 | 颜色数 | 体积 | 前景 px |
|---|---|---|---|---|
| 黑线（edges, dilate 0） | 231 | 1 | 109.7KB | 13,872 |
| 4 色聚类 | 774 | 4 | 222.7KB | — |
| **逐线取色（edges）** | 231 | **177** | 109.3KB | 13,872 |

**线宽分段**（不是全局加粗）：`--line-width auto` 给每段线单独算宽度——用该线处的 Sobel 梯度强度（强轮廓粗、纹理细），并加了三道保护：小闭环（眼睛/鼻孔）不加粗、加粗后会碰到邻线的不加粗、`area<150` 或跨度 `<40px` 的不加粗。实测 238 段线里只有 17 段变成 3px，体积 +2.5KB（102.2KB），其余保持 1px。

**完整性的两个杀手**：`--dilate 1`（把细线加粗成块）和 `--min-stroke-px 25`（把小笔画整条丢掉）。现在默认是 `dilate=0` + `min-stroke-px=1`，产出与"裸 Canny 直接描"一致（参考：239 路径 / 115.4KB / 14,013 px）。

注意：逐线取色比 4 色聚类**体积小一半**（94.3KB vs 222.7KB）——聚类会把同色但不相连的线段反复描，逐线则每个连通块只描一次。

**参数坑**：柔光照片默认阈值（60/160）会把头发纹理全当边，细化后再膨胀就是一片黑。线稿必须先 `--dilate 0` 看骨架，再按需加粗；阈值下限抬到 110 以上才干净。

## 归因：attribute.py（别让模型猜 id）

```bash
python <skill>/scripts/attribute.py ref.png out.svg --top 8
```

做法：把整幅图**再渲染一次**，每条 `<path>` 换成唯一索引色 → 得到"这个像素归谁"的地图；再用真实渲染与参考图算误差，**只把像素记在它真正可见的那条路径上**。一次渲染出全表。

每行给：`id` / `fill` / `px` / `mean` / `sum` / `share` / `inner` / `ring` / `guess` / `gain` / `suggest_fill` / `box`。

| 字段 | 含义 |
|---|---|
| `guess=shape` | 最优填充色与现值差很远，或轮廓误差 > 内部误差 → **位置/形状错** |
| `guess=colour` | 轮廓正常、内部整体偏色 → **换色即可** |
| `gain` | 换成 `suggest_fill` 能回收多少误差总和（按它排序） |

**受控验证**：把画好的场景里太阳 path 挪到别处再跑归因——$id=2$ 排第一，mean 126.26，`guess=shape`，`suggest_fill` 直接给出“这块底下其实是背景色”的证据。

**真实人像上的发现**：48 色量化后，最差的 path 是**颜色类**（bbox 横跨整幅），误差集中在轮廓（`ring > inner`）——所以换色几乎没用（mean 8.23 → 8.13），该加的是预算（`--region`），不是手术。**路径手术只对手绘/线稿类有意义**，对量化照片没有“一条线”可修。

闭环用法：`attribute.py` 看排名 → 组补丁 → `iterate.py --patch` → 复测（mean 与 `total_err_sum` 都看）。

## 脚本（A→G 全链路已固化）

**默认用 `vectorize.py` 做 SVG 重绘；`build_svg.py` 的像素矩形模式已弃用**，只保留作逐像素误差参考，不再作为默认或交付。`paint_process.py` 是显式可选分支，用户没有点名“作画过程 / 回放”时不要调用；但 `vectorize.py --process-final` 可复用它的**最终画面算法**（默认关闭，无回放）。

```bash
# 默认：SVG 矢量重绘（高配基准 K=384 / 3× 超采样，可继续扫参）
python <skill>/scripts/vectorize.py ref.png out.html 384 6 1.0 1 0.4 3 0

# 常规档：K=192 / 2× 超采样
python <skill>/scripts/vectorize.py ref.png out.html 192 6 1.0 1 0.4 2 0

# 静态 SVG 交付：输出名用 .svg，自动去掉 data-step / data-total
# 播放标记只属于 HTML 分支；过程模式才按绘画阶段分 step
python <skill>/scripts/vectorize.py ref.png out.svg 128 6 1.0 1 0.4 1 0
# K 不给时默认 128 并打印提示；交付档一律显式写 256~384

# 补缝：把层与层之间的细缝用最近色填上（dilate=0 或低 K 有明显白缝时用）
# 只填"细"缝（距离变换 ≤1.5px）和小噪点；要求有不透明底色 rect，透明图会明确跳过
python <skill>/scripts/vectorize.py ref.png out.html 128 6 1.0 0 0.4 1 0 --seal
python <skill>/scripts/seal_gaps.py out.svg shot.png out-sealed.svg   # 也可独立对已有产物跑

# 可选边缘后处理（默认关闭）：feGaussianBlur 柔化最终边缘，不改变几何；sigma 约 0.15~0.6
python <skill>/scripts/vectorize.py ref.png out.svg 192 6 1.0 1 0.4 2 0 --edge-soften=0.35

# 慢速版：几何 alpha 环软化色阶断层（默认关闭，与 --edge-soften 互斥）
python <skill>/scripts/vectorize.py ref.png out.svg 384 6 1.0 1 0.4 3 0 --alpha-soften 2 --alpha-strength 0.25
python <skill>/scripts/svg_postprocess.py out.svg out-soft.svg --soften 0.35
# 注意：--edge-soften 与 --seal 同用时，seal 会跳过（柔化必须是最后一层）

# 过程算法只出 final（默认关闭）：不生成批次/播放器，只把最终画面写成静态 SVG
python <skill>/scripts/vectorize.py ref.png final.svg --process-final --process-palette=256 \
  --process-line-direction=line-direction.json
# 过程 final 的追踪参数全部可调，这些是参数自身的默认值：
#   --process-base-scale=0.34 --process-mid-scale=0.52 --process-detail-scale=0.85
#   --process-min-areas=24,8,2  --process-blurs=1.0,0.5,0.18
#   --process-focus-box=0.10,0.08,0.90,0.92 --process-focus=1.0,4,0,1
#   --process-dil=1 --process-turd=1 --process-alphamax=1.0 --process-ss=1.0
# --process-from-front 默认开启：继承前排 turd/alphamax/dil/ss，以及 --full-palette /
# 显式 --palette-sample。显式 --process-* 优先；显式给了 --process-*-scale 时不再乘 ss。
# blur 不继承（过程是逐层 1.0/0.5/0.18）；sk_thr/dark_k/med 没有过程对应。
# 旧版固定行为复现加 --no-process-from-front；要显式控制倍率用 --process-ss=N。
# 过程全像素色板：--process-full-palette（等价 --process-palette-sample=0）；
# 也可 --process-palette-sample=N 指定抽样像素；--no-process-full-palette 关闭继承。
# 色数上限默认 1024：--process-palette-max=N（paint_process.py 直连用 --palette-max=N）。
# <=256 色无论是否 full 都用整图 PIL median cut。

# 无损压缩（默认关闭）：绝对路径改相对坐标、去掉重复属性；不删路径、不改几何
python <skill>/scripts/vectorize.py ref.png out.svg 192 6 1.0 1 0.4 2 0 --compact
python <skill>/scripts/svg_compact.py out.svg out-compact.svg
# 可与 --process-final / --edge-soften 组合；--compact-precision=N 控制小数位（默认 6）

# 极致色板：全量像素做 median cut（Windows x64 原生 palette_core.dll）
# 默认是抽样 24 万像素 + cKDTree 全量分配；--palette-sample=N 可调抽样数
python <skill>/scripts/vectorize.py ref.png out.html 384 6 1.0 1 0.4 3 0 --full-palette

# 已弃用：像素矩形，仅历史/逐像素误差对照；运行会打印 DEPRECATED
python <skill>/scripts/build_svg.py ref.png out.html [K] [sketchThr]   # A→F，默认 K=2048 sketch=16

python <skill>/scripts/render.py out.html shot.png W H "?step=11&v=1"  # 精确尺寸截图（零依赖，调系统 Edge/Chrome）
node   <skill>/scripts/render.js  out.html shot.png W H "?step=11&v=1"  # 同上，走 playwright-core（需要它时用）
python <skill>/scripts/metric.py  ref.png shot.png                     # mean / ≤3 / ≤5 / ≤10 / ≤20 / uncovered
python <skill>/scripts/steps.py   out.html outdir                      # 逐帧截图 + 拼版 + 单调性/空步检查

# 形态三：平滑矢量的另一条路（vtracer 样条，颜色更多、路径上万）
python <skill>/scripts/smooth_svg.py ref.png out.svg [precision] [speckle] [layer] [corner]

# 形态四（特殊可选，默认不启用）：画师分层过程模拟
# 只有当用户明确要求“作画过程 / 过程回放 / 绘画图灵测试”时才使用；
# 平时的 SVG 交付走 vectorize.py；像素/autotune 已弃用，不要再作为默认。
python <skill>/scripts/paint_process.py ref.png out.html --size 876x1904 --duration 58 --title "她与金线" --palette 256
# 阶段：起稿勾线 → 铺大色 → 分区平涂 → 二分光影 → 深入细化 → 遮罩细化 → 闭塞塑形 → 藏色 → 罩染调色 → 提亮补色 → 收线高光
# 输出 out.html + out.preview.html；支持 ?static=1&ui=0、?step=N
# 这是算法过程模拟，页面必须保留「AI 生成 / 算法路径模拟」标注，不能包装成真人作画录像
# 注：以上数值是基准建议，不是固定档位；最高档需按目标（静态保真 / 过程可信）和预算扫参，取 Pareto 最优点。
```

依赖：Python 3 + numpy + Pillow + scipy + potracer（曲线分支）；原生 DLL 的 ctypes 绑定集中在 `scripts/native_loader.py`（两个脚本共用，缺失/DLL 报错时自动回退纯 Python，结果曲线一致：实测 Δmean 0.02）；`--full-palette` 另需 Windows x64 的 `scripts/native/palette_core.dll`，缺失会回退纯 Python 全量 median cut（很慢）；`--palette > 256` 另需 scikit-learn。**截图默认 `render.py`，零依赖**——直接调系统 Edge/Chrome 的 `--headless --screenshot`，找不到时看 `BROWSER_BIN` 环境变量；`render.js`（playwright-core）只在需要它时作为备选，按 `PW_CORE` → cwd/DSH_HOME/DSH_CHECKOUT 向上找 `.pnpm/playwright-core@*`。

回归测试：`python <skill>/tests/run_tests.py`（首次基线用 `--record`）。用例 = 平涂源逐像素、照片源逐像素、照片源曲线、**透明 PNG 曲线**、**曲线 + 边缘柔化**、**曲线 + 无损压缩**、**过程算法 final + 压缩**、**过程算法 final 可调追踪参数**、**过程算法 final 从前排继承**、**过程算法 final 全像素色板**；断言 `uncovered` 不超基线、mean 不超基线 +1.0，以及"单文件/无位图/无渐变（可选柔化滤镜除外）/有路径"四条结构检查。

透明图的 uncovered 口径不同：只统计**源图 alpha≥250 的像素**里被渲染成纯白的部分（alpha 128~249 是抗锯齿描边，本来就该柔化），并允许 ≤8 px 的孤立噪点（turdsize 会丢弃 alpha>250 的零星碎片）。

> 注意渲染器影响指标：同一份 HTML 用 playwright 与 Edge CLI 各截一张，mean 相差约 1.6——所以一个项目内必须固定同一个渲染器再做横向比较。

`autotune.py` 停止规则 = 「`≤5/255` 占比增益 < `--gain` 个百分点」，每档打印一行 JSON（`K/classes/paths/MB/mean/≤5/≤10/uncovered`）。注意它驱动的是**已弃用的像素模式**，只作历史误差对照，不再作为默认调参入口。

## SVG 重绘模式（默认） + 可选过程模拟（默认不启用）

同一张 750×434 照片级立绘的实测（源 `ref-girl.png`）：

| 模式 | 产物 | mean | ≤5 | 观感 | 适合 |
|---|---|---|---|---|---|
| 像素精确（`build_svg.py`，**已弃用**） | 游程矩形 4133 path / 4.06MB | **2.16** | 96.7% | 与源几乎一致，**放大有像素台阶** | 仅逐像素误差对照 |
| 真矢量（`vectorize.py`，K=192 + 2× 超采样） | 193 条曲线 path / 7.36MB / 11 步 | 8.35 | 44.6% | 平涂插画，**无台阶**，细节简化 | "要矢量感"、可缩放插画 |
| 真矢量高保真（K=384 + 3× 超采样，**高配基准**，可继续扫参） | 385 条曲线 path / 19.7MB（750×434 源） | **6.36** | 61.0% | 接近原图，暗部有层次 | 能接受大文件的交付 |
| vtracer 样条（`smooth_svg.py`） | 12 034 path / 3.47MB | 7.20 | 65.1% | 接近原图但极碎 | 高保真矢量化 |

> 表内是 750×434 源。876×1904 的 K=384/3× 实测 **385 层 / 139.7MB / 约 50s**；尺寸不同，别把 19.7MB 当成这张图的体积。默认抽样色板与全量色板在这张图上 mean 分别是 5.4917 / 5.4885，差距很小；追求极致再用 `--full-palette`。

> K 是**颜色层数**，不是绘画阶段数。过程模式的 11 个阶段是 `paint_process.py` 专有；`vectorize.py` 的 384 层是 384 个色块路径。静态 `.svg` 不写 `data-step`，只有 HTML 播放器/过程模式需要播放标记。

### 指令编译评测（30 条）+ 视觉框选样例

```
python scripts/eval_instructions.py plan.json examples/batch/expected.json examples/batch/compiled.json
```

结果：**30/30 正确**（schema 全通过 + 算子集合 + 受影响层集合一致），产物在 `examples/batch/`。
注意这 30 条是**自拟的、指向明确的**指令（用来验证 schema 与映射稳定性），不代表开放语义的天花板；
这批评测覆盖的是 9 个改画算子，`draw_path`（从零加笔）另由 `tests/test_draw_ops.py` 覆盖——
含歧义/指代/复合目标的指令还需要另建更难的一套。

**加笔评测（10 条「加点什么」）**：同一套 eval 也跑 `draw_path`：

```
python scripts/eval_instructions.py plan.json examples/batch/expected_add.json examples/batch/compiled_add.json
```

结果：**10/10 正确**（schema 全通过 + 算子集合 + 受影响层集合一致，`failures: []`）。
覆盖：右下角金叶、花心高光、左下角叶子、茎上亮边、背景弧线、金色叶脉、水珠、蝴蝶轮廓、签名、飘落花瓣。
诚实标注：`compiled_add.json` 是我按 schema 人工写的（本机没有模型 key，无法现场调模型），
所以它验证的是**schema + 层/算子映射**，不是模型对开放指令的命中率；要测模型就配好 key 后重跑
`compile_ops.py compile --thumb`，把输出存成 compiled_add.json 再 eval。


**视觉框选（多模态掩膜）**：直接看图给多边形，不写坐标规则。

```
examples/mask_rose_head.json     # {"type":"polygon","points":[...]} 围着玫瑰花头
examples/ops_rose_magenta.json   # 4 个算子：bed/band 转品红、highlight 偏紫、band 加密
python scripts/instruction_demo.py plan.json --ops examples/ops_rose_magenta.json \
       --mask-file examples/mask_rose_head.json --feather 24
```

实测：选中 4,056 笔，脏矩形 (275,0)-(775,504)，编辑 2.4 s、重算 8.65 s（bed/band 铺满全图，
又是"改大层"的代价），**增量 == 整份重画 0 px**。

**颜色连通域掩膜**（`masks.from_image`）：按颜色 + 容差找像素 → 连通域 → 最大块包围盒 → rect 掩膜，
不必逐笔回读像素；`tests/test_masks_image.py` 断言只选中色块内的笔触。
另加了包围盒预筛：多边形掩膜对 5 万笔逐笔算距离从 5.2 s 降到 **2.4 s**。

### 改画 → 30fps 视频（`paint_video.py`）

```
python scripts/paint_video.py plan.json --ops examples/ops_rose_magenta.json \
    --mask-file examples/mask_rose_head.json --feather 24 \
    --frames 600 --fps 30 --groups 48 --edit-at 0.78 --out rose_edit.mp4
```

先按计划把画"画起来"，到 `--edit-at` 指定进度时对脏矩形执行一次 `clear_rect + 重画`，
最后若干帧继续补完未改动层。实测（900×1100、57,812 笔、位图笔尖）：

| 项 | 数值 |
|---|---|
| 帧数 / 帧率 / 时长 | 600 / **30 fps** / 20.0 s（帧数固定，不丢帧） |
| 渲染 + 存帧 | 54.0 s |
| x264 编码 | 2.53 s |
| 总耗时 | **59.8 s** |
| 文件 | 1.38 MB |
| 失败条件 | 末帧与"整份重画改后计划"必须逐位相同——实测 **0 px**，否则脚本报错退出 |

注意：这里没有用 `imageio_ffmpeg.write_frames`（本机沙箱下它的管道句柄会失效，WinError 6），
改成"先存 PNG 序列、再调 ffmpeg CLI"，编码参数 `-c:v libx264 -pix_fmt yuv420p -crf 18`。

### 难指令集（20 条：可表达 vs 应当拒绝）

```
python scripts/eval_instructions.py plan.json examples/batch/expected_hard.json examples/batch/compiled_hard.json
```

结果分两版（都在仓库里，可复现）：

| 版本 | 文件 | 正确率 | 说明 |
|---|---|---|---|
| v1 | `examples/batch/compiled_hard_v1.json` | **19/20** | #19 把色板写成字符串 `"深蓝到浅青"` → 被 `validate_ops` 拒掉，**像素没被碰** |
| v2（修正版） | `examples/batch/compiled_hard.json` | **20/20** | 修正后 schema_ok=20、failures=[] |

v1 保留作案例：**模型会在最不起眼的地方（色板类型）出错，而校验层是唯一能挡住它的东西**。
另：子代理自报 20/20，我没有直接采信，是本地重跑 eval 复核后才写进文档的。

设计要点：10 条可表达 + 10 条**不可表达**（变成水彩 / 换成纯白背景 / 把玫瑰变成牡丹 /
恢复上一次修改 / 更通透 / 更贵一点），后者期望模型输出空数组表示拒绝，而不是硬凑算子。
实测模型确实全部拒绝（6/6 类），说明"知道自己的边界"这件事是可以被量化的。

> 说明：复核期望时有 4 条（id 7/9/10/18）属于判断题，已放宽为 `any_of`（多种合理答案都算对）；
> 例如「除了高光其余都提亮」模型用 7 个算子逐一实现，是**比我原期望更好**的答案。

```
python scripts/compile_ops.py compile plan.json "把花改成蓝的" --provider deepseek --out ops.json
```

`compile --provider` 支持 deepseek / ark / openai（OpenAI 兼容），温度 0、重试 3 次；
无 key 或调用失败时把完整提示词打到 stderr 并 exit 2（回退到 agent 编译）。key 只从环境变量读。

### 统一入口（`paint_cli.py`）

```
python scripts/paint_cli.py edit plan.json --ops examples/ops_rose_violet.json \
    --mask-file examples/mask_rose_head.json --feather 24 --out a.png --video a.mp4
python scripts/paint_cli.py edit plan.json --instruction "把花改成蓝色" --provider deepseek --out a.png
python scripts/paint_cli.py compile plan.json --instruction "..." --provider ark --out ops.json
python scripts/paint_cli.py video plan.json --ops ops.json --mask-file m.json --out a.mp4 --frames 600
python scripts/paint_cli.py eval plan.json examples/batch/expected_hard.json examples/batch/compiled_hard.json
```

一条命令走完：编译（`--provider`）或读产物（`--ops`）→ schema 校验 → 掩膜选笔 → 增量渲染 →
可选 30fps 视频。实测 900×1100/57,812 笔：**整条 28.4 s**（含 600 帧视频 59.7 s 时为分开计时）。

**视频链路踩过的两个坑（`tests/test_paint_video.py` 抓出来的，都是真 bug）**：

1. **脏矩形低估**：用「笔触中心 ± size/2」算脏矩形，比引擎真实落笔范围小（引擎是 `size*0.7+2`），
   边缘残留旧颜色 → 末帧与整份重画差 312 px。改用 `tile_inc.bboxes` 的引擎口径后归零。
2. **重复叠加**：编辑发生在绘制中途时，重算已画过的笔触、后面的分组又画一遍，半透明叠加两次 →
   差 322 px。修法是把绘制安排在 `--draw-until`（默认 0.8）之前全部画完，`--edit-at`（默认 0.85）
   之后才做 `clear_rect + 重画`，保证编辑后不再有落笔。

### 改色（红玫瑰 → 蓝玫瑰）：按颜色选笔，不要用几何掩膜

这张画里**大笔触同时盖住花头和背景**，几何掩膜（多边形/矩形）选笔只有两种结果：漏掉大部分红色笔触
（红斑上飘蓝点），或把背景一起染。所以要先按**颜色**选出红色笔触，再用**区域**排掉画面下方的茎/叶：

```
python scripts/paint_cli.py edit plan.json --ops examples/ops_rose_blue.json \
    --mask-file examples/mask_red_head_poly.json --feather 0 --hard-mask --fast --out rose_blue.png
```

- 掩膜必须是**复合掩膜** `{"type":"and","masks":[{"type":"color","rgb":[190,30,45],"tol":150},{"type":"polygon",...}]}`：
  只用颜色掩膜时它是**全图**范围的，画面下方偏红的茎、叶缘、萼片会一起被选中——实测叶子下沿出现
  **565 个蓝像素**（这就是"下面叶子被涂蓝"的原因）。取交集后同一指标 **565 → 0**，萼片恢复绿色。
- 该复合掩膜选中 **2,878 笔**；全库红色系 3,023 笔，其余约 160 笔是暗红/棕，被 tol 挡下 → 花心褶皱留一点红。
- `--hard-mask` 是改色的必要条件：颜色掩膜的权重是「距离 → 1..0」的软权重，不二值化时 hue 只旋转一部分，
  同一组算子会得到**洋红**而不是蓝（实测：软权重 → 品红，硬权重 → 蓝）。
- `--hard-mask` 是改色的必要条件：颜色掩膜的权重是「距离 → 1..0」的软权重，不二值化时 hue 只旋转一部分，
  同一组算子会得到**洋红**而不是蓝（实测：软权重 → 品红，硬权重 → 蓝）。
- 算子：`set_layer_hue`(218) + `set_layer_saturation`(0.85)，`guidance: 1` 才精确命中。
  让子代理按 `compile_ops.py prompt` 的提示词真编译同一条指令，产出的 8 个算子与
  `examples/ops_rose_blue.json` **逐字段相同**——"模型能编译"与"手写没超纲"同时成立。

**走不通的思路（记录在案，别重走）**：试过用 Photoshop 的 `color` 混合模式罩一层蓝（保留底下明暗），
但本引擎只实现了部分混合模式。探针（底层红 220,30,30 + 顶层蓝 30,60,220）：

| blend | 合成中心像素 | 结论 |
|---|---|---|
| normal | 30,60,220 | ✓ |
| multiply | 26,7,26 | ✓ |
| screen | 224,83,224 | ✓ |
| overlay | 193,14,52 | ✓ |
| softlight | 197,16,69 | ✓ |
| hue / saturation / luminosity | 220,30,30（底层不变） | ✗ 未实现，退化为无操作 |
| color | 30,60,220（等价 normal） | ✗ 未实现 |

所以「罩染层」不成立，改色只能落在笔触层（这也解释了为什么它天然是"改笔触"而不是"改图层"）。

色相速查（实测渲染）：**218 蓝 / 275 紫罗兰 / 285 蓝紫 / 300 洋红**。注意红(0) → 275 跨过蓝区，
沿最短弧走的是红→紫的反方向，不要以为"红+蓝=紫"就能用 240 凑出紫。

### 默认关闭的可选开关

**`--edge-soften=SIGMA`：边缘柔化后处理（legacy，保留可用）。** 新交付优先用下面的 `--alpha-soften`；这个开关留着只是因为快速出图时它更省事（一步模糊，不用扫参）。 它直接作用于已完成 SVG（或 HTML 里的 SVG），只增加一个 `feGaussianBlur` 和一层包装，不改路径坐标、不改层序。适合“放大很多倍不要让 1px 阶梯很扎眼”的交付；副作用是整体轻微变软。想真正提高轮廓分辨率，应该提高 `ss`（如 3×），`--edge-soften` 只是最终视觉后处理。`svg_postprocess.py` 可独立对已有 SVG 跑，参数为 `--soften N`。

**`--alpha-soften=N`：几何式边界软化（默认 0 = 关）。** 对每个色带在**外侧**补 N 圈 `<path>`，纯几何实现，不引入滤镜或渐变，因此与 `--edge-soften` 互斥（同开直接报错）。两种模式：

| 模式 | 开关 | 环用什么颜色 | 治什么 | 实测（400×711 / K=96 / ss=1） |
|---|---|---|---|---|
| 混合 | `--alpha-soften 2` | 色带色 × alpha | 台阶（观感） | mean 6.06→**6.40**，强边 48,114→**40,757（-15%）**，23.8MB |
| 真实局部色 | `--alpha-soften 2 --alpha-true-color=6` | 环内参考像素按亮度切 6 段，每段用该段真实均值色 | 台阶 + 误差 | mean **5.65（-0.41）**，强边 43,873（-9%），63MB |

配套开关：

| 参数 | 默认 | 说明 |
|---|---|---|
| `--alpha-width=PX` | 1.0 | 每圈宽度；实测 2 会明显变差（混合模式 mean 10.26） |
| `--alpha-strength=A` | 自动：混合 0.25 / 真实色 0.7 | 环的透明度。真实色模式带一点透明，能把子级间的残差再抹一层 |
| `--alpha-ramp` / `--no-alpha-ramp` | 自动：混合模式开、真实色模式关 | 只在参考图**缓变区**（5×5 跨度 6–72 且细梯度 ≤14）补环。混合模式下每千条边的 mean 代价从 0.082 降到 0.046；真实色模式则相反，全边界覆盖才够 |
| `--alpha-refine=N` | 0（关） | 生成 → 渲染 → 剔除"加了反而更差"的环像素 → 重建，重复 N 次。用 **3×3 局部窗口**比较误差（逐像素比较会把所有环都判死，实测一次删掉 50 万像素）。K=32 皮肤区实测（tol 默认 1.0）：mean 5.30 → **5.15**，体积 43.7 → 38.6MB；tol 0.5 可到 5.12 / 35.7MB，tol 3 为 5.24 / 43.2MB |
| `--alpha-tol=T` | 1.0 | refine 的容差：局部均值允许变差的量（越小越激进，0.5 时多删一半环、mean 再降 0.03） |

**怎么判断"台阶变弱"？别用强边计数。** 真实色 6 级在同一张皮肤图上把原图 12.05 万条 2–4 级细台阶恢复到 12.89 万，同时把 >20 的硬跳变从 1.39 万压到 1.16 万——观感明显更连续；但"强边（>10）计数"反而从 30,153 变成 30,585，因为 8–12 这一档被算进了"强边"。判台阶要看**分档直方图**（>20 / 12–20 / 8–12 / 4–8 / 2–4），不能只看一个阈值。

**实测：真实柔光人像**（同一张立绘裁 760×900 皮肤区，K=32，色阶断层肉眼可见）：基准 mean 5.81 / 皮肤误差 7.49 / 强边 30,153；`--alpha-true-color=6` → 5.30 / 6.79（-9.4%）/ 30,585，43.7MB；混合 2 环 → 5.85 / 7.63 / 25,854（-14%），12.8MB。同一张图 K=96 时基准已是 3.68，混合 2 环反而 +0.27（收益不明显）。**所以它只在色阶断层明显的低 K（约 ≤64）才值得开；K 高时直接加 K 更划算。**

**为什么混合模式会抬高 mean？** mean 是逐像素 RGB 偏差，而环把边界像素推向邻色：参考图那里本来就是纯色时属于净损失，只有真是渐变时才赚。想要 **mean 下降**必须用真实局部色模式——它补的是该处缺失的中间色，等价于在边界临时提高色阶密度。但注意：单纯降 mean 用 K 更划算（K=192 → 4.82 / 9.6MB，比真实色环的 5.65 / 63MB 更好更小），环的价值只在「不想整体加层、只想修边界」。

**`--process-final`：用过程模式算法只出最终图。** 默认关闭；打开后不执行普通颜色层描摹，而是调用 `paint_process.py` 的同一套平涂/二分/细化/罩染/收线算法，直接输出静态 SVG。不生成 11 阶段、884 批次、播放器或 `data-step`。可选 `--process-palette=N`（默认 256；若给了位置参数 K 则用 K）和 `--process-line-direction=FILE`。`--process-palette` 为 257–上限（默认 1024，可用 `--process-palette-max=N` 提高）时默认只在约 8 万像素上拟合 k-means；`--process-full-palette` 或 `--process-palette-sample=0` 改为全部像素，`--process-palette-sample=N` 指定抽样像素。同一张图、同一 palette/线稿参数下，最终画面与过程模式的完成帧一致；区别只是没有过程。输出 `.svg` 得到静态 SVG，输出 `.html` 得到无播放器的静态 HTML。

过程 final 的追踪参数现在全部暴露；参数自身默认值仍是 `--process-base-scale/--process-mid-scale/--process-detail-scale`（0.34/0.52/0.85，1.0=原图分辨率）、`--process-min-areas=24,8,2`、`--process-blurs=1.0,0.5,0.18`、`--process-focus-box=0.10,0.08,0.90,0.92`、`--process-focus=scale,min_area,blur,dil`（1.0,4,0,1）、`--process-dil=1`、`--process-turd=1`、`--process-alphamax=1.0`、`--process-ss=1.0`。`--process-from-front` **默认开启**：自动把普通模式前面的 `turd/alphamax/dil` 继承到过程层（`dil` 同时用于 region 和 focus），`ss` 只在没有显式给任何 `--process-*-scale` 时作为统一倍率继承，`--full-palette` 和显式 `--palette-sample` 也会继承；显式 `--process-*` 始终优先，`--no-process-from-front` 回到旧版固定值。`blur` 不继承（过程是逐层 1.0/0.5/0.18），`sk_thr/dark_k/med` 没有过程对应。`--process-min-areas` 是过程版的碎块过滤（角色类似普通模式的 `turd`），`--process-turd` 则直接传给 Potrace。实测 876×1904、384 色：旧版固定行为（`--no-process-from-front`）为 54,887 path、mean 11.8843；显式 0.50/0.75/1.25 + `--process-turd=6` 为 103,991 path、压缩后 29.72MB，mean 10.8098，脸部边缘密度 34.31%→34.70%；同一无头渲染约 3.0s→7.0s，所以它是“细节换速度”，不是压缩提速版。同一组 0.50/0.75/1.25 + 384 色下，全像素色板为 104,841 path / 29.57MB / mean 10.7668 / 脸部边缘 35.03% / 6.9s，抽样色板为 103,991 path / 29.72MB / mean 10.8098 / 34.69% / 7.0s；全像素只值这么一点，别指望质变。

**`--compact`：无损路径数据压缩。** 默认关闭；把 `M/L/C/Z` 绝对坐标改写成相对坐标、去掉每个 path 上重复的 `shape-rendering`，**不删路径、不改层序、不改 fill/opacity/fill-rule**。实测 876×1904 过程 final：28,550,352 → 18,121,227 字节（-36.5%），渲染 2.92s → 2.39s，逐像素 mean 0.0213/255（只是相对坐标的浮点抗锯齿差，不是几何变化）。它不解决“被完全遮住的 path”问题：同一文件全遮挡剪枝只找到 954 / 50,927 条（1.9%），单独做遮挡剪枝省不到 0.4MB；把 topmost 画面重描成 192 条 path 虽然能到 20.4MB，但 mean 8.43/255，不算无损，所以没有默认启用。

`paint_process.py` 是**过程演示分支**，不参与上面的误差竞赛：先描线稿，再按连通色块平涂/二分/细化，脸部与手足再用遮罩裁切后单独高分辨率矢量化，最后藏色、罩染、提亮补色与收线。实测 876×1904（palette 0 烟测）：35,838 path / 26.44MB / 836 batches / 11 阶段；palette 256 仍是 28MB 量级。旧 256 色 mean 11.83 / ≤5 25.56% 是线稿层改版前的参考值，改版后未重测 mean。`--palette` 从原图采全局色板：默认 256，`512/1024` 走 k-means（需 scikit-learn，512 实测 mean 10.81 但 74,674 path），`0` 关闭；257 色以上默认在约 8 万像素上拟合，`--palette-full`/`--palette-sample=0` 改为全像素，`--palette-sample=N` 指定抽样像素；色数硬上限默认 1024，可用 `--palette-max=N` 提高。原生追踪（Windows x64）默认启用：`scripts/native/trace_core.dll`（potrace 1.16，GPL）；同尺寸 438×952 从 40.4s 降到 2.61s，876×1904/256 色生成约 10.5s；`vectorize.py` 也走同一 DLL（876×1904/K48 约 5.8s；K=384/3× 约 45s）。DLL 缺失或 `PAINT_NATIVE_TRACE=0` 时自动回退纯 Python，输出 SVG 不变。它换的是"能看懂作画顺序"，不是像素保真；静态高保真用 `vectorize.py`，像素模式已弃用。

### 过程模式线稿：agent 决策，脚本执行

skill 不替每张图决定“哪里是主体”。它提供 step0 线稿、区域过滤、描边、DLL 参数、`--line-only` 和验收；区域与取舍由 agent 看图写。

**流程**

1. 看参考图，判断“大形 / 次级结构 / 纹理细节”。
2. 写这张图专用的 `line-direction.json`，不要复用上一张图的区域。
3. 用 `--line-only` 只出线稿（约 1.5MB、2–3 批），看 `?step=1`。
4. 缺哪块补哪块：只调该区域 `bbox / min_diag / density_exempt`，不要全局调一个阈值。
5. 层级满意后再跑全量。

```bash
python scripts/paint_process.py ref.png line.html --size 876x1904 --palette 0 \
  --line-direction line-direction.json --line-only
python scripts/render.py line.html line-step1.png 876 1904 "?step=1&ui=0"

python scripts/paint_process.py ref.png process.html --size 876x1904 --duration 58 \
  --palette 256 --line-direction line-direction.json
```

```json
{
  "density_percentile": 52,
  "default_min_diag": 34,
  "trace": {"turdsize": 6, "alphamax": 1.0, "precision": 2},
  "style": {"color": "#33445c", "stroke_width": 0.5,
             "sketch_opacity": 0.95, "accent_opacity": 0.8},
  "regions": [
    {"name": "subject", "bbox": [0.08, 0.05, 0.92, 0.95], "min_diag": 28, "density_exempt": true},
    {"name": "background_detail", "bbox": [0.0, 0.0, 1.0, 0.35], "min_diag": 70}
  ]
}
```

| 字段 | 作用 |
|---|---|
| `density_percentile` | 局部密度分位；越低越严格，密集线进 `line-accent`。 |
| `default_min_diag` | 未命中区域的最小 bbox 对角线，按 876px 宽基准缩放。 |
| `trace` | DLL 的 `turdsize / alphamax / precision`，可按图调。 |
| `style` | 线色、线宽、起稿/收线透明度；线宽也按宽度缩放。 |
| `regions[].bbox` | 归一化 0..1，按顺序匹配，先命中先算。 |
| `regions[].min_diag` | 该区域最小结构；更小的进 `line-accent`。 |
| `regions[].density_exempt` | true 时不受密度过滤，适合眼窝/鼻翼/手部结构。 |

`name` 随意：人像可写 `face/hands`，风景可写 `sky/foliage`，产品可写 `label/edge`。skill 不内置任何语义区域。

**线稿规则**

- 线稿是 `fill="none"` 的描边，不填充；不会出现大色块填充区。
- 起稿只放“大形 + 指定保留的小结构”；过小结构、密集纹理留到 `line-accent`。
- 起稿层在颜色层下面，收线层在最上面；这是过程演示，不是像素保真模式。
- 全量仍约 27–30MB；`--line-only` 只用于看线稿，不是交付文件。

互斥关系要讲清楚：`uncovered=0 + mean<2` 只有像素矩形能做到（台阶是游程几何的本质）；曲线拟合必然偏离像素，所以 README 的"平涂路径 + mean<2"对照片级源**不可能同时成立**，只对平涂源成立。用户说"有像素感"就切 `vectorize.py`，并把 mean 从 2.16 变到 11.8 的代价一起说明。

`vectorize.py` 管线：median-cut 调色板 → 每色掩码 → **膨胀 1px**（否则层间留白缝）→ **2× 超采样后描摹再把坐标除回去**（曲线更顺、边界更准，实测 mean 11.8→9.37）→ potrace 拟合曲线 → 按色族分批、族内按面积从大到小叠放 → 底层垫主色 `<rect>` → 每层一个 `data-step`。

关键调参（同一张照片级源，逐项实测）：

| 改动 | mean | 说明 |
|---|---|---|
| K=128, 无超采样 | 11.8 | 基线 |
| + 2× 超采样 | 9.37 | 只增加描摹分辨率，路径数不变 |
| + K=192 + 色族排序 | 8.35 | 体积 7.36MB |
| + K=384 + 3× 超采样 | **6.36** | 体积 19.7MB / 385 层，暗部误差 6.96→4.75 |

**分层色板（暗区单独切）是可选旋钮，不是必要修复**：暗部丢层次的真解是提 K + 超采样（仓库 §B/§5 的唯一路径，实测把暗部误差 6.96→4.75）。`vectorize.py` 的第 10 个参数 `dark_k` 只在对暗部有额外要求时再开，K=192 的对照：单色板 mean 6.87/暗部 5.40 → 96/96 分层 mean 7.25/暗部 4.42（整体略升换暗部更细）。默认关。

**调色板不要换 Lab**（实测否决）：同一张图 K=192，RGB median-cut 的 mean 6.87 / 暗部 5.40，Lab 空间是 8.43 / 6.05——Lab 的 L 跨度大会让切分偏向亮度层，暗区反而分到更少色阶。仓库坚持 RGB median-cut 是对的；暗部层次的唯一有效手段就是提 K（或对暗区单独再切一层色板，属未验证扩展）。

瓶颈不在拟合参数，而在**颜色数**：平涂矢量每少一档颜色，渐变区就多一条能看见的色带。README 的 mean<2 只有像素矩形能达到。

## 七阶段

1. **A 归一化** — 无损 RGB，记录 W,H；每次任务一个 ref/ 目录，源/预览/截图/diff 分开。
2. **B 调色板** — median-cut（脚本内置），**不要 k-means**（照片级分布会留 30+/255 的坏中心）。K 从 1024 起步翻倍，判据是「≤5 占比」与 mean，不是 K 本身；增益 <3pp 即停。
3. **C 打标** — 最近中心全图赋值（cKDTree）。
4. **D 几何** — **逐像素游程矩形**（`M x y h w v1 h-w Z`，整数坐标，`crispEdges`）。按构造精确复现掩码，互不重叠所以不需要 z-order；未覆盖恒为 0。单 `<path>` 子路径 ≤300（否则渲染器静默截断，P3）。
5. **E 步骤语义** — **必须按色族推进**：先把 K 个调色板中心聚成 ~8 个色族，按色族总面积排序（背景族=1，其余 2..11）；**按单类面积排序会得到噪声帧**（详见 P8）。末步必须全量；亮肤色判定排在「近白」之前。
6. **F 播放器** — 只切 `class`，零过渡；`#art [data-step]{display:none}` + `.on{display:inline}`；`data-step` 打在 `<path>` 上而不是包 `<g>`；步数从 `data-total` 读，别硬编码。
7. **G 验收** — 无头浏览器截图 → `metric.py` → 按瓶颈归因（§调参决策树见 references/manual.md）。

线稿层（可选，`sketchThr>0`）：对亮度做梯度阈值取边缘游程，单独一层 `data-step="0"`，只在 step 0 显示（`#33445c` @0.55）。它是独立的「起稿」帧，不参与 1..N 的累加，所以单调性检查从第 2 步起算。

## 验收（必须贴实测数字，不许写「已验证」）

| 指标 | 目标 |
|---|---|
| uncovered（渲染纯白 ∧ 源不纯白） | **0** |
| mean 通道误差 | **< 2/255** |
| ≤5/255 像素占比 | ≥ 90% |
| ≤10/255 像素占比 | 100%（**平涂源**；见反锯齿例外） |

实测（本机，两档源）：

| 源 | K | 产物 | mean | ≤5 | ≤10 | uncovered |
|---|---|---|---|---|---|---|
| 1280×800 平涂插画 | 1024 | 1134 path / 4.05MB | 0.43 | 99.72% | 99.75% | 0 |
| 750×434 游戏立绘（照片级+AA） | 4096 | 4085 path / 4.06MB | **2.16** | **96.67%** | 99.89% | 0 |
| 1200×675 透明 PNG（RGBA，74% 透明），曲线 | 192（2× 超采样） | 193 path / 7.55MB | **3.74** | **83.03%** | 91.53% | 3 |

透明源的两条硬规则（都踩过）：① 直接把 RGBA `convert("RGB")` 会把透明区变成**黑底**，必须先按 alpha 合成白底再量化；② 透明模式输出**不带底色 `<rect>`**，SVG 背景真透明（验收方法：把 SVG 垫在纯洋红/棋盘格上截图，角落像素必须是底色本身）。

**反锯齿例外（重要）**：源图带 AA 边缘时「100% ≤10」不可达——整数游程无法复现亚像素渐变，只能逼近。此时换口径写清：`uncovered=0、mean<3、≤5 ≥95%`，并说明瓶颈在源图 AA 与量化上界，而不是硬凑数字。

## 截图三要素（缺一必错）

1. 缩放强制 1:1（系统 DPI 会把右缘整条裁白）；
2. 窗口高 ≥ 图高 + 120（视口不够会在画面中部整体截断，伪装成内容缺失）；
3. URL 带版本号（`file://` 缓存会让指标「冻结」）。

`render.js` 已内置这三条，且尺寸取自 `viewBox`（`steps.py` 同理，不靠截图反推）。

## 逐帧验收（steps.py）

对 `0..data-total` 逐个渲染，输出每步非白占比、`monotonic_violations`、`flat_tail_steps`、拼版 `contact-sheet.png`。判据：

- `final_cover` 必须等于全量渲染（否则有 path 被漏掉）；
- `monotonic_violations` 为空（步骤只能累加；step 0→1 因线稿层单列，不计）；
- `flat_tail_steps` 为 0（末尾若连着多步占比相同 = 步骤阈值没铺满，P8）；
- 目视拼版：每一步应该像「多刷一类颜色」，而不是随机噪点或整块缺失。

## 失败模式对照

| 症状 | 根因 | 修复 |
|---|---|---|
| 全图边界细亮线 | 像素中心 vs 格点差半像素 | 整数边界矩形，或坐标 −0.5 |
| 一侧固定宽度全白 | 截图继承 DPI 缩放 | 强制 scale=1 |
| 某大色域整块缺失 | 单 path 子路径过多被静默截断 | 分块 ≤300 |
| 横向/纵向整块缺失 | 无头窗口小于画面 | 窗口 ≥ 图高+120 |
| 中间步像随机噪点 | 按单类面积排序（类极碎） | 按色族排序 |
| 末尾两步画面相同 | 步骤阈值铺不到末步 | 步数按 data-total 动态算 |
| 指标随 K 变大反而变差 | 几何方案对碎片敏感 | 换游程矩形 |
| 平涂区色差大 | 聚类欠收敛 | median-cut |
| 改参数指标不变 | file:// 缓存 | 版本号 |
| uncovered>0 但目视全覆盖 | 类均值四舍五入成纯白 | 类内非纯白时钳到 254 |
| 不同工具比较结果不同 | JPEG 解码器不一致 | 固化无损副本 |
| 矢量版整张只剩一个底色 | potracer 约定 True=白底（描 0 值区），掩码没取反 | `Bitmap(~mask)` |
| 矢量版层间白色描边/缝隙 | 相邻层各自拟合，边界不重合 | 掩码膨胀 1px + 底层垫主色 rect |
| 矢量中间步几乎无变化 | 用非白占比当进度指标（底色覆盖全画布） | 改看相邻步像素差 `step_delta` |
| 透明 PNG 输入后背景变黑块 | RGBA 直接转 RGB，丢掉 alpha | 先按 alpha 合成白底再量化 |
| 透明 PNG 输出仍带白底/方块 | 透明模式照样铺底色 rect | 检测 alpha 后跳过底色 rect，透明像素标 `-1` 不参与描摹 |
| 大 K 曲线分支 `ZeroDivisionError` | 调色板有空类（无像素选中的中心）面积 0 | 先剔空类，面积加权再除 `max(1, area)` |
| 透明图 uncovered 虚高 | 拿半透明描边像素当"漏画" | 只统计 alpha≥250 的像素 |

## 本仓库踩过的坑

- **PIL `Image.quantize(colors=K)` 上限 256 色**，K=1024 直接抛错——必须自己写 median-cut。
- 用 `%s` 拼 SVG 到 HTML 时，JS 里的 `cur%12` 会被当格式符 → `TypeError: not enough arguments`。用 `str.replace` 占位符。
- 技能目录在 checkout 内，`package.json` 的 `"type":"module"` 会让 `render.js` 的 `require` 报 ESM 错误——`scripts/package.json` 已声明 `commonjs`。
- 460~4000 类、1~4k path、4MB 单文件属正常量级（游程编码体积增长很缓），不要因此降 K。
- 纯白保护：某类均值四舍五入成 `#ffffff` 但类内含非白像素时，渲染出的纯白会被记成 uncovered（实测 34px）——钳到 254 即归零。
- 透明图的"白底"和"透明"在 PNG 里看不出来：验收必须换底色（洋红/棋盘格）截图，角落像素 = 底色才算真透明。
- 透明源的对照图必须是**白底合成版**（浏览器截图不带 alpha），否则 black-vs-white 会把 mean 拉到几十。

完整原始手册（伪代码 + 调参决策树 + 坐标约定）：`references/manual.md`。

## 计划空间改画（指令 → 算子 → 确定性重渲染）

对应扩散编辑里的「文本条件 + CFG + SDEdit + 掩膜」，全部落在可见的 plan doc 上：

| 扩散概念 | 这里的实现 | 旋钮 |
|---|---|---|
| 文本编码器 | `compile_ops.py`：多模态把指令编译成算子 JSON（落盘、schema 校验、`--recompile` 才重编） | — |
| cross-attention 注入条件 | 算子直接改 doc 字段（层/颜色/尺寸/笔触数） | — |
| CFG 引导强度 | 每个算子的 `guidance ∈ [0,1]` | 0 = 逐字节不变，1 = 精确命中 |
| SDEdit 噪声强度 | `redraw(fraction, jitter, order)` | fraction = 重画的笔触比例；`order` 选谁先改 |
| masked inpainting | `masks.py`：rect / point / polygon / color + 羽化权重 | `feather` |
| 从零绘制（不读参考图） | `draw_path` 算子：SVG path（M/L/H/V/C/Q/T/Z）展平成笔触追加到指定层 | `d`/`color`/`size`/`guidance` |

只允许这 11 个算子（别的键一律拒绝，防模型瞎编）：`set_layer_hue`、`set_layer_saturation`、`set_layer_brightness`、`scale_stroke_size`、`swap_brush`、`densify`、`erase`、`redraw`、`recolor_palette`、`draw_path`、`tone_map`。

**draw_path（从零加笔）与语义改画是同一条链**——它同样受 `guidance` 控制、同样能算掩膜与脏矩形：

```json
{"op": "draw_path", "layer": "band", "color": [235,190,80], "size": 9, "opacity": 0.95,
 "d": "M 690 1040 C 740 1000 780 1030 820 990 C 850 960 870 980 885 950"}
```

实测（真实 57,812 笔计划，加一枝金叶）：

| 项 | 数值 |
|---|---|
| 增量 vs 整份重画 | **0 px**（`incremental_vs_full_diff_px=0`） |
| 脏矩形重建 | **0.35 s**（只重算 band 层里路径覆盖的笔触） |
| 编辑（算子执行 + 重打包该层） | 1.13 s（层大，重打包是主要成本） |
| guidance=0 | 计划逐字节不变（`tests/test_draw_ops.py`） |

`redraw` 的 `order` 语义（都可复现，同 seed 两次逐字节相同）：

| order | 谁先改 |
|---|---|
| `random`（默认，向后兼容） | 稳定哈希抽样，等价「随机 N 笔」 |
| `coverage` | bbox 面积大的先改（大笔触/背景层优先） |
| `layer` | 计划里靠前的层先改 |
| `zorder` | 后画/上层的先改 |

测试：`tests/test_draw_ops.py` 断言 draw_path 只加笔、guidance=0 不变、脏矩形框得住、order 三种优先级、坏输入被拒。

**tone_map（色调映射，专门修「整幅偏暗」）**：改的是笔触颜色，所以 shadow/texture 两层 multiply 的压暗会被一起抬起来：

```json
{"op": "tone_map", "gain": 1.8, "lift": 0.05, "gamma": 1.25}
{"op": "tone_map", "layer": "shadow", "gain": 1.4}
```

- `gain`：亮度乘数（>0）；`lift`：加白（0..1，抬黑场）；`gamma`：>1 提亮中间调；`layer` 省略 = 所有层。
- 恒等：`gain=1, lift=0, gamma=1` 逐字节不变；`guidance=0` 不变；guidance 0.5 = 旧值/新值各一半（`tests/test_tone_map.py` 全 PASS）。

实测（900×1100 / 57,812 笔，全部层 + shadow/texture 再 ×1.25）：

| 项 | 数值 |
|---|---|
| 平均亮度 | **11.2 → 47.9**（源图 60–73） |
| 增量 vs 整份重画 | **0 px** |
| 编辑（改 57,812 笔颜色 + 重打包） | 3.94 s |
| 脏矩形重建 | 2.49 s |


**本会话模型就是多模态编译器（不需要 API key）**：上面那条视觉 grounding 的链路我直接用自己跑通了——
编译模型 = 当前会话模型，输入 = `render_thumb` 的图 + 计划摘要，输出 = ops JSON，再走 validate → apply → GPU 增量重渲染。

| 指令（看图后写的） | 编译出的 ops | 结果 |
|---|---|---|
| 整幅太黑，提亮别洗白 | `tone_map gain 1.8 lift .05 gamma 1.25` + shadow/texture ×1.25 | luma 11.2 → **47.9**，diff 0 px |
| 暗部太死黑，抬黑场 | `tone_map layer=shadow gain 1.5 lift .03` | 全图 luma 11.2 → 12.7（局部暗部抬起） |
| 高光太冷，调暖 | `set_layer_hue highlight 40 + sat 0.35` | 高光由冷白转暖粉，图上可见 |

子代理同样能跑（继承同一模型、能读图），只是这次没必要再走一遍；要"第二意见"时可以 fork 一个子代理独立编译同一张图对比。
上一段"没 key 所以未验证"的说明对**外部 API 端点**仍然成立，但"多模态看图编译"这件事本身已经在本会话里验证过。

**视觉 grounding（把预览图接进编译）**：编译默认只喂文本摘要，颜色/明暗只能靠编号猜。
现在可以把渲染缩略图一起交给多模态模型：

```
python scripts/compile_ops.py compile plan.json "高光太冷了，调暖一点" --thumb --out ops.json
python scripts/compile_ops.py compile plan.json "..." --image shots/current.png --out ops.json
python scripts/paint_cli.py edit plan.json --instruction "..." --thumb --gpu --out a.png
```

- `--thumb`：用 GPU（不可用自动 CPU）把当前计划渲成 ≤720 px 的预览再发；`--image`：直接用现成图。
- 图片压成 data URL 走 OpenAI 兼容的 `image_url` 块；端点不支持图片时（报错含 image/400）自动回落纯文本重试。
- 离线测试：`tests/test_vision_grounding.py`（消息构造 / 图片压缩编码 / body 结构 / 缩略图非全黑），**全 PASS**。
- 未验证：真实多模态调用的效果——本机没有 API key，只验证到"请求体构造正确"这一层。


```
python scripts/compile_ops.py summarize plan.json                  # 计划摘要（喂模型）
python scripts/compile_ops.py prompt plan.json "把花改成蓝色" > prompt.txt
python scripts/compile_ops.py validate plan.json ops.json          # schema 校验
python scripts/compile_ops.py apply plan.json ops.json out.json --mask "{...}" --feather 32
python scripts/ops_demo.py plan.json --layer highlight --hue 260 --brightness 1.25 --rect "200,150,500,700" --feather 40
```

实测（900×1100 / 8 层 / 57,812 笔的真实玫瑰计划 `painting.plan.json`，位图笔尖，CPU）：

| 项 | 时间 |
|---|---|
| 计划摘要 / 选掩膜 / 算子执行 | 0.04 s / 0.03 s / **0.05 s** |
| 打包成数组快表（`plan_store.pack`） | 1.25 s |
| 整份重画（57.8k 笔位图笔尖） | **14.5 s**（其中绘制 13.6 s，笔尖注册 0.04 s） |
| **只重算脏矩形**（500×718，单层） | **0.995 s → 14.6×** |
| 增量 vs 整份重画 | **逐位相同（0 px）** |
| 脏矩形之外 vs 改画前 | **0 px** |

端到端（编译产物 `examples/ops_highlight_cool.json` + `instruction_demo.py`，同一份计划）：

| 步骤 | 时间 |
|---|---|
| 读 103 MB JSON + schema 校验 | 1.59 s |
| 掩膜 + 算子 + 只重新打包改动层 | **0.087 s** |
| 只重算脏矩形（单层，500×718） | **0.065 s** |
| **编辑到出新像素（不含读盘）** | **0.15 s** |
| 整份重画对照 | 14.2 s → 增量**逐位相同（0 px）** |

```
python scripts/instruction_demo.py plan.json --ops examples/ops_highlight_cool.json --rect "200,150,500,700" --feather 40
python tests/test_e2e_edit.py
```

**改哪一层决定增量收益**（同一条链路，两次真实编译产物）：

| 改动 | 受影响笔触 | 编辑 | 重算 | 对比整份 |
|---|---|---|---|---|
| `highlight`（局部高光，`ops_highlight_cool.json`） | 1,619 | 0.087 s | **0.065 s** | ≈90× |
| `bed`（全幅底稿，`ops_bed_dim.json`） | 9,735（脏矩形≈全图） | 0.70 s | 7.53 s | 1.7× |

结论：增量收益取决于**受影响笔触数/脏矩形面积**，不取决于"改画"这个动作本身。改底层等于改全图，
改局部小层才是数量级提升——所以掩膜要尽量紧，指令要尽量落在局部层上。

慢在原生逐笔光栅化本身，不在 Python 装配：整份 14 s 里绘制 13.6 s、打包 1.25 s、笔尖注册 0.04 s；
要整份提速只能换 GPU 后端，要局部提速就是上面这条增量路径（0.15 s）。

坑（已修在 `plan_io.resolve_preset`）：批量解析位图笔尖名时若每笔都调 `load_catalog`，复杂度是 O(笔触数 × 278 笔尖)，5.7 万笔会卡 8 分钟以上；必须把 catalog 传进去复用。

测试：`python tests/test_ops.py`、`python tests/test_compile_ops.py`。


## GPU 后端：一直实现了，是我两次误判

时间线（都在仓库里，可查）：

| 提交 | 内容 |
|---|---|
| `15a1f06` | painting engine + incremental repaint，**含 paint_gpu.cpp / paint_gpu.dll**（DirectCompute） |
| `5a43f20` | GPU 逐 stamp 色彩（打包键 alpha16\|stamp-index），**cpu/gpu mean diff 0.001**，加 `tests/test_gpu_tile.py` |

现有 GPU 回归测试（长期在仓库里，之前的"跑全部测试"清单却漏了它们）：

```
python tests/test_gpu_tile.py     # gpu tile rebuild == gpu full；gpu vs cpu mean=0.0000
python tests/test_stamp_color.py  # gpu per-stamp 色彩 dev=0.001，alpha 完全一致
python tests/test_gpu_blends.py   # 本次新增：normal/位图笔尖/混合模式 的差异快照
python tests/test_fx_gpu.py       # 效果层：impasto 1.5–5.7x、smudge 3–9x
python scripts/native/gpu_probe.exe / gpu_bench.exe
```

本机（RTX 5070 Laptop）：`gpu_probe` OK；`gpu_bench` 3,000,000 stamps R=20 → 3.77 G pixel-ops / 236.5 ms（15.9 G/s）。
全幅渲染（900×1100 / 57,812 笔）：CPU **13.74 s** vs GPU **3.62 s**（3.8×）。

### 一致性范围（修正后）

| 场景 | mean\|Δ\| | px>4 | 结论 |
|---|---|---|---|
| normal 混合 + 公式笔刷 | **0.000** | 0 | 逐位一致 |
| normal 混合 + 位图笔尖 | **0.000** | 0 | 逐位一致（修复后） |
| multiply / screen / overlay / softlight | **0.000** | 0 | 逐位一致 |
| 整张玫瑰计划（57,812 笔，8 层） | 0.028 | 707 / 990,000（0.07%） | 实质等价（浮点累加顺序差） |

**"GPU 与 CPU 混合模式不一致"是误判——真正的 bug 在 CPU 侧。** `paint_create` 已经自带 layer 0 作为背景，
而 `new_painter` / `render_plan` 又 `add_layer("normal")`，于是：文档里的第 i 层被画进 painter 的第 i+1 层，
而混合模式数组是从第 2 层开始的 → **每个混合模式都右移了一位、最后一层永远空着**。
CPU 的所有多混合层计划（含玫瑰、蓝玫瑰、视频）都因此少渲染了 multiply/screen/overlay/softlight 效果。

GPU 的 `pg_create` 不建隐式层，同一份代码在 GPU 上恰好对齐，所以它一直是对的——对比时显得"GPU 不一样"，
实际是 CPU 走岔了。修复方式：两个后端统一约定「layer 0 = 背景」（CPU 直接用隐式层，GPU 显式补一层），
之后混合模式逐位一致，整张计划只剩笔尖取整带来的 0.36% 像素差。

已经排除的：混合公式本身。两端源码逐行对照（`paint_gpu.cpp` 154–205 的 `blend3`/`cs_composite` 对比
`paint_core.cpp` 63–100 的 `blend_px` 与 989–996 的合成）数学一致，乘法/滤色/叠加/柔光的表达式相同、
预乘与反预乘也相同。所以下一步不是改公式，而是做单笔/不透明上层的逐像素对照，定位是常量缓冲字段、
纹理格式还是视图绑定顺序的问题（`pg_composite_layer` 里已有一处"先解绑 UAV 再绑 SRV"的注释，说明这条路径踩过坑）。

**精度说明（别再想用 fp16 凑）**：GPU 的笔尖一直是 `StructuredBuffer<float>`（float32），不存在量化；
之前位图笔尖 0.202 的差是**笔尖裁剪范围**不对——CPU 只画半径 1.3R 的圆内
（`stamp_band` 的 `R2 = R*R*1.69`），GPU 少了这一步，方框四角多画一圈像素。
补上 `if (dx*dx + dy*dy > s.z*s.z*1.69f) continue;` 后，位图笔尖 **mean 0.000、px>4 = 0**，**没有降低任何精度**。
剩下整张计划 0.07% 的像素差（max 4 灰阶以内）来自两端 stamp 位置/累加的浮点顺序，与存储精度无关。


### GPU 现状与缺口（2026-08 校准）

已达标（全部有实测）：
- 26 种混合模式 CPU↔GPU 全部 mean ≤0.017 / px>4 ≤49（hardmix 的 49 px 是阈值等号边界）；
  其中 hue/saturation/color/luminosity 是这一轮才在 GPU 补上的，且修了 CPU 端 `B_HUE` 少写
  `hh = sh` 导致 hue 等于 no-op 的老 bug。
- 位图笔尖 mean 6e-6（个别像素 1/255）；动态参数已改为**每实例**（原先写文件级 static 全局）。
- `GpuPainter` 现在补齐 `stroke` / `stroke_batch` / `layer_rgba` / `load_brush`，
  与 `Painter` 同签名；实测单笔合成与 layer_rgba 逐位相同（tests/test_gpu_api.py）。
- GPU 创建失败会**自动回落 CPU**，并由 `p.gpu_used` 通知 tile_inc（不会按 GPU 的 offsets 切错）。

仍不完善：
1. 整张计划 0.07%（707/990,000 px >4）的差，**已定位到两个功能差异**（不是 stamp 位置）：
   - `set_color_dynamics`：已去掉 4+4 位桶，改成 composite 里用 `(seed=0, k)` 直接重算 hash（与 CPU
     `jitter_color` 同式同常数）。量化误差消失，但两端 **stamp 展开各写一套**，同一像素的"获胜 stamp 序号"可能不同
     → 仍差 ~520 px（R 通道最大 64）。彻底对齐需要共享展开。
   - `set_wet`：GPU 在批次开头 CopyResource 快照，CPU 的湿边是另一套语义 → 单笔 560 px / 546 px>4。
   其余路径（无 dynamic、无 wet、位图笔尖、26 种混合、单笔、批量、layer_rgba）都已逐位一致。
2. `pg_composite_incremental` 仍无独立回归（测试只覆盖 full 与 dirty-rect 重建）。
3. 只在 RTX 5070 Laptop 上验过，跨显卡/跨驱动确定性未测。
4. paint_gpu.dll 需本机 VS BuildTools + clang 重编（build_gpu.bat / build_paint.bat）。
5. `paint_hatch` **不是后端功能**：它是纯几何（mask → 折线），走 CPU DLL 的静态函数，
   与 painter 后端无关；之前列成"GPU 缺失"是分类错误，已改正。

### 条纹定位：是 shadow 层的"格子填充"，不是 band 扫描线

之前把条纹归因到 band 的扫描线摆笔是错的。逐层单独渲染定位到 `shadow`：

- `shadow` 共 5,591 笔，**每笔是一条 860 点的长路径，bbox 约 38×35 px，preset=flat，size 33.6**
  ——即把画面切成 38px 左右的格子、每格用一条蛇形长笔填满；格子边界在提亮后显成网格线。
- 单层渲染直接可见：格子内深、格线浅。

三种缓解都试过、都不根治：

| 手段 | 结果 |
|---|---|
| band 位置抖动（shift 1.4 / wiggle 0.3） | 条纹不变（说明源头不是 band） |
| shadow 位置抖动（shift 2.0 / wiggle 0.5） | 条纹变毛躁，仍在 |
| shadow 每笔绕 bbox 放大 1.06 | 网格线不变（路径与笔径同步放大，缝没消） |
| shadow 笔径 ×1.18 | 网格线变淡，但格子更大更明显 |
| shadow 层 erase（诊断用） | 网格消失，整图变平 |

根治要在计划生成侧改 shadow 的填充方式（连续覆盖 / 相邻格重叠一定像素），或换掉 flat 刷的格填策略，
需要原始参考图 + planner 参数重跑；`scripts/jitter_strokes.py` 目前只是诊断工具（`--expand` 可试重叠）。

**已落地的修复（计划空间）**：`scripts/rebuild_shadow_layer.py` 把 shadow 层重采样成连续软笔：

- 默认参数（`cell=12, size_k=5.0, op_scale=0.12`）：按 12px 网格放 size≈60 的重叠软笔并加确定性抖动，
  重叠比 ≈5，格纹与"气泡"边界都消失；实测整图 luma 45.6 → **45.2**（tone 参数不变）。
- `--mode random` 会留软斑，已弃用为诊断选项。
- **planner 默认 `--shadow-mode drop`**：直接删掉格填 shadow（`painting_clean.plan.json`，52,141 笔），
  再用 `tone_map` 补亮度；实测清晰度 lap 436 -> 444、且无格无噪点。`smooth` 会重采样成软笔但**会糊**
  （60px 软笔把 38px 色块场抹平，lap 掉到 266，肉眼即"糊"），`keep` 保留原格填。
- 纸纹（texture 层）贡献实测极小：erase 掉后 mean_abs=0.236/255、max=5、px>2 仅 883（占 0.09%），
  所以画面上那层"纸感"不是它，无需默认关闭；真要关用 `erase layer=texture` 或 `--tex-op 0`。

`tests/test_rebuild_shadow.py`：同 seed 逐字节相同、只换 shadow、混合模式保留、坐标离开 16px 格点，全 PASS。
根治仍建议在 `plan_region` 的 shadow 填充里做相邻格重叠；重采样是"不需要参考图的可用修复"。



### 亮度基准：对齐 11-03 02:44 的 `rose-raster-process.mp4`

那条 24s/30fps 的旧 raster 过程视频末帧就是"不糊、不偏亮"的基准（luma **22.3**）。
它的观感 = **没有格填 shadow 层 + 没有 highlight 白点**，也不需要额外提亮。

**辅助函数统一在 `scripts/render_util.py`**：`luma_of` / `target_luma_from_image` /
`render_arr` / `render_luma` / `draw_full` / `render_layer_rgba` / `strokes_to_rgba` / `send_strokes`。
calibrate_tone、rebuild_shadow_layer、planner_8layer、instruction_demo 都走这一份，不要再各写一套渲染/发送路径
（instruction_demo 换成 `draw_full` 后输出与改前逐像素相同：diff_px=0）；
`tests/test_render_util.py` 锁住亮度口径、单层 alpha、确定性。

**CLI 参数统一在 `scripts/cli_util.py`**：`add_common_args`（`--gpu/--threads/--quiet`）、
`add_target_args` + `resolve_target`（`--target-image` / `--target-luma` 互斥，缺省即报错，不给默认常数）、
`write_json` / `load_json`。calibrate_tone、rebuild_shadow_layer、instruction_demo 都改用它（instruction_demo 只输出报告，传 `quiet=False`），
`--quiet` 会关掉逐轮标定日志（只留最终 JSON）。`tests/test_cli_util.py` 覆盖默认值、互斥与报错。

**全流程一条命令**：`scripts/plan_pipeline.py` 串起 planner → 报告 → 标定 → 最终图：

```
python scripts/plan_pipeline.py rose5-full.png regions2.npz --out demo \
    --target-image rose5-full.png --gpu          # --dry-run 只打印命令
```

planner 侧新增两个开关：`--report <path>`（写 canvas/层/混合模式/笔触数/luma/产物路径的 JSON）
和 `--calibrate <参考图>`（跑完用参考图反解 tone gain，写 `<plan>.tone.json`）。
pipeline 再把 tone 应用到计划、渲染 `<前缀>.final.png`，并把 `final_luma` 并回报告。
`tests/test_plan_pipeline.py` 锁住路径派生、命令拼装（无 target 就不带 `--calibrate`）、dry-run 不落盘、合并保留 planner 字段。

**出图一条命令**：`scripts/render_plan.py plan.json --ops a.json --ops b.json --out final.png --gpu`
把多个 ops 顺序作用后整份渲染（不需要掩膜/增量重建；`--ops` 可重复，后者作用于前者结果）。
标定完的 ops 直接喂进来即可：

```
python scripts/calibrate_tone.py ... --out tone_calibrated.json
python scripts/render_plan.py plan/painting_clean.plan.json --ops tone_calibrated.json \
    --out _shots/final_calibrated.png --gpu
# -> luma 22.32（与标定报告 achieved_luma 22.323 一致）
```

**highlight 层默认去掉（`--highlight-mode drop`）**：1,838 个 `splat_dots` 是接近白色的
screen 点，几乎全落在叶子上——叶子 +16.7 luma（57.6 vs 40.9），玫瑰只 +1.4（34.5 vs 33.1），
而 11-03 02:44 的旧 raster 出图根本没有这层。默认 drop 后叶子亮度 40.9 对齐视频的 39.1；
若要"绿色高光"可 `--highlight-mode keep` 再叠
`set_layer_hue highlight 100 + set_layer_saturation 0.9 + tone_map(layer=highlight) gain 0.55`
（实测叶子裁剪区 32.1，色相对、仍偏粉笔感）。

**`edit_loop.py` 支持多步 recipe（一个指令里多个区域/多组操作，顺序作用）与内联 ops**：
`--ops-json`（可传 `-` 从 stdin 读，绕开 shell 引号）`--recipe recipe.json`（`{"instruction":…,"steps":[{name,seeds|mask_file,box,ops}]}`）。
实测"背景换暖夜色 + 抬叶子高光"：步1 背景 luma 23.98→13.04（区内 Δ10.87，玫瑰区 Δ0.84），
步2 叶子高光仅 +0.51 luma —— 因为 highlight 层只有 1,838 个点，调色能抬的上限由笔触数量决定，
要更明显必须生成笔触而不是调参。
**两个真 bug（修完）**：① `masks._decode_bits` 的缓存键是 `id(mask)`，对象回收后 id 复用会解出**别的掩膜**；
② `edit_loop` 度量用 uint8 相减，下溢回绕把 Δ10 报成 Δ155（现在先转 float，并有回归断言）。

**六个缺口组件（本轮补齐，全部性质断言，不锁像素值）**

| 缺口 | 组件 | 实测 |
|---|---|---|
| 区域重规划 | `scripts/planner_region.py`：默认 `--mode cover --thr 0.9 --ring auto`（外扩环 = 目标层笔触中位尺寸×0.5，上限 24，不用固定魔法数），按覆盖率判定 + 原位替换保绘制顺序 | 玫瑰区替换 2,009 笔：区内 Δ2.09、边环(30px) Δ0.275、**区外 Δ0.000** |
| 软权重着色 | `ops._targets` 的强度 = guidance×掩膜权重，本来就走插值；raster feather 修好后边界是连续权重 | `set_layer_hue guidance=0.5` 色相落在中间值，不跳变 |
| 实例级选择（**进 ops 语义**） | op 里可直接写 `selection`：`mask` / `mask_file` / `polygon`（模型自己画）/ `instance`（模型给种子点，长出掩膜并验证）/ `all`。另外 `replan_region` 已是正式算子，所以"照参考图重画这片区域"也是一条 op | 玫瑰实例 tone_map：区内 Δ7.22、区外 Δ0.029；**叶子改用模型画的多边形**（色相生长分不开同色背景）→ 验证通过（面积 4.72%、1 连通块），tone_map 区内 Δ9.14；`replan_region` 同参数重跑幂等（区内 Δ0.00） |
| 实例自纠错 | 验证失败时 `pick_instance` 输出 `hints`（下一轮改什么）+ `--attempt-log` JSONL 记录每轮；`box_fill` 专门检测"掩膜≈手画框" | 叶子三轮：①泄漏面积 0.77 → ②收紧后仍占框 0.94 → ③按 hints 改多边形 → 通过（面积 0.047、fill 0.73） |
| 光影分解 | `ops.split_shading` / `ops.set_light` / `ops.relight_dir`（方向光，笔画级能量守恒：30.81→30.81；但渲染后叶区仍会变暗 12%，因为 ambient=overlay / highlight=screen / glaze=softlight 是像素级非线性） | `relight_dir` 用球面伪法线并按选区均值归一：光从左(180°) 左半 37.8/右半 19.7；光从右(0°) 左 26.6/右 34.4，**亮面跟着方位角翻边**；归一前换方向会顺带压暗 15%（36.22→32.46），归一后 36.22→36.80 |
| 验收门 | `scripts/accept.py`：schema/确定性/区外不变/方向/主体 | 白玫瑰一例：确定性通过、区外 Δ0.003、区向 +83.44 |
| 多步链示例 | `recipe_leaf.json`：split_shading → relight_dir → set_light(warm) → tone_map，均带同一掩膜，经 `edit_loop --recipe` 执行 | 4 步逐步度量：luma 36.22→36.22→36.80→39.35→44.04，叶外 Δ≤0.29；掩膜注册 `leaf_left`、谱系 `e36fe8c80` |
| 编辑谱系 | `scripts/lineage.py`（append-only JSONL + 快照 checkout）、`scripts/mask_registry.py`（掩膜 id + 来源） | 父子链、tree、checkout 恢复快照均有断言 |

另有 `edit_loop --zoom x0,y0,x1,y1` 输出局部放大图（`zoom.jpg`）供模型看细节。

**"模型画掩膜"是复用多模态的主路径**：`pick_instance` 支持 `polygon`（模型自己描边）和
`polygon + refine`（grabCut 边界吸附，零外部模型，`out_band` 允许向外长）。
叶子实测：**轮廓质量由模型描边决定，抓边只做微调**——粗 9 点轮廓与自绘 14 点轮廓 IoU 只有 0.751，
吸附后与自绘的 IoU 0.770；换紧 14 点轮廓后吸附 IoU 0.943、边缘贴合 1.73→2.86（`edge_fit` 字段）。
`out_band` 太大反而外溢（46.8k → 55.0k px）。边缘贴合度是参考指标，不当通过/不通过（叶脉也是强边缘）。
**描边必须迭代，不能一遍定稿**：`grid` 读数 → 描点 → 看**局部 3× 放大**（上/下/右各一块）→ 改点 → 再验。
实测叶子：r1 面积 0.0718（上边界高 10–18px、右下偏 10–15px、左下欠 15–25px）→ r2 0.0707 → r3 0.0692，
但 3× 放大看仍是"平滑椭圆"而不是叶子。**手画点收敛不到边缘上（我目测误差 5–20px）**，于是加
`pick_instance.py snap`：沿法线 ±8px 找最强梯度 + 平滑 → 面积 0.0678、fill 0.75、bbox [97,534,489,763]、
edge_fit 0.9→1.94、平均位移 5.6px，3× 放大后绿线贴着叶缘。`--search 20` 会跳到相邻亮叶上（右侧凸出）。`edge_fit` 指标在这张图上不可靠（r1 1.51 → r2 1.09：
叶子边缘亮、背景更暗，梯度反而不高），**判据只能是局部放大目视**。

**模型的参与手段 = 格子编号，不是像素坐标**：`pick_instance tiles` 画带编号的网格，
`tile-prompt --pos D2,E2 --neg A1,H1` 直接把编号换成点（代码做坐标换算）。ops 里对应
`{"tiles": {"image","roi","cols","rows","pos":[...],"neg":[...],"ring","model","components"}}`，
edit_loop 的 step 也认 `"tiles"`。实测：8×6 格、20 正 14 负 → 对人工描边 IoU **0.928**、
area 0.0574、单连通；真实计划上选中 **1357 笔触**。两次坐标算错的根因（看图→口算 x,y）
从此不存在。

**prompt 已是 ops 选择类型**：`{"prompt": {"image","pos","neg","ring","radius","model","components","bridge"}}`
和 `{"segment": {"image","roi","seeds","ring","passes","hue_k","sat_q","chroma_scale"}}` 都能直接写进 op，
**参数全部暴露**（不再是把阈值写死在我脑子里）。edit_loop 的 step 也支持 `"prompt": {...}` 直接当掩膜。
实测：真实计划里 prompt 选中 1370 笔触；3 步链 luma 39.99→34.58→41.40，叶外 Δ0.01–0.10。

**多峰判别器**：GMM（numpy EM，k=3）比单高斯稳：16 个负点仍 IoU 0.906/area 0.0558，
单高斯在同样负点数下坍缩到 area 0.709（整图变前景）。负点数量不该决定成败——这正是单高斯的病。

**提示点分割 `pick_instance prompted`（模型语义参与的正道）**：`--pos`/`--neg` 传照片坐标点，
函数只做"判别 + 走廊精修 + 负点硬排除"，没有拟合常量（唯一阈值是似然比 0）。实测：
7 正 + 8 负 → 对人工描边 IoU **0.936**；负点加到 16 个 → 单高斯坍缩（area 0.709~0.9968，整图变前景）。
所以**判别器要按多峰建模**（每类 GMM/多直方图），且负点要落在真背景上——不是越多越好。

**小图与空样本要守卫**：`refine_mask` 在 60×60 合成图上 grabCut 抛
`!bgdSamples.empty() && !fgdSamples.empty()` 断言（前景/背景确定样本不足）→ 已加"样本 <20px 直接返回 + try/except"。
`edit_loop` 写 metrics.json 时 NaN 不是合法 JSON（下游 json.load 直接炸）→ 写盘前把 NaN/Inf 换成 null。

**坐标纪律（血泪）**：看放大图落点前，必须先让 `grid_overlay` 把**照片坐标**网格画在图里。
我两次把"显示坐标"当照片坐标（屏幕 x=(photo_x-80)×3），负点全打在目标外，白跑两轮；
还有一次把负点打在叶子自身的暗边上，把叶底切掉（area 0.0427）。

**"调常量"不等于"修好"**：`segment`（色度 Otsu 版，0.932）的 hue_k/sat_q/chroma_scale/ring
是我拿人工描边当标准答案调出来的，玫瑰只到 0.643 → **只作辅助，不得代替人工/模型判断**。

**自动分割用 `pick_instance segment`（色度 Otsu + 色相 MAD 窗 + 自洽两遍 + 走廊精修）**：
关键是**色度 s·v**，不是亮度。实测这张图叶子 chroma 0.14 / hue 0.25，背景 chroma 0.05 / hue 0.41
（AUC：chroma 0.948、hue 0.95 反相），信号一直够强——错在参数化：我原先用 hue≈0.33 单侧容差、种子还落在亮边上。
现版无手工点（只给 ROI）对人工描边 IoU **0.932**；玫瑰（红到白多色）只有 0.643，**多色主体仍要给种子**。

**人类给的轮廓直接采纳，不要再"优化"**：红/绿笔画是细线且可能有 1px 断口，用**最近邻串珠**（贪心走最近点，断口跳最近）
+ `fillPoly` 填充；不要用 floodfill 判内部——1px 缝隙会让外部灌进来，内部判成 0 像素（我因此误判"线未闭合"，
实测串珠后 3468/3522 点全部走通、最大断口 0.5px）。采纳后边界距笔画 0–2px，面积 0.0614、bbox [109,540,520,781]。
亮度/色差边界在同一片叶子上会朝相反方向漂（亮缘外偏 8–13px，压梗暗部被切），**不能用来覆盖人的判断**。

**描边必须先有坐标参照**：`pick_instance.py grid --image X --box x0,y0,x1,y1 --out g.jpg` 输出带像素网格的
放大裁剪，模型按网格读数描边，再用**填充**叠图自查。同色低对比的叶子用这条路径一次到位：
20 点轮廓 → 面积 0.0718、fill 0.75、bbox [95,519,510,748]，用到 ops 上叶内 Δ11.67 / 叶外 Δ0.179。
反面教训（都是我的操作错误，不是"图不可分"）：不看坐标目测描点会错位；只画轮廓线的回显会掩盖错位
（曾把背景当成叶子用）；Canny 在这张图只出 fill=0.08 的细圈，k-means 候选全是背景/碎片。

**所以编辑走笔触级选择，不依赖照片掩膜**：`{"sel_box":[x0,y0,x1,y1],"sel_hue":[h,tol],"sel_sat_min":s,"sel_val_min":v}`
（`sel_` 前缀避免与算子参数撞名；hue 是 ops._hsv 的 0..1 归一化值，绿≈0.33）。
叶子实测：命中 1,946 笔，框内 Δ3.72、框外 Δ0.027。

**draw_path 的笔触可以真删**：`draw_path` 加 `tag`，`erase` 用 `select_tag` 精确删；
实测加笔后与基准差 0.459，删掉后差 **0.0000**，笔触数 29163 → 29164 → 29163。

**比较对象要选对**：plan 里的坐标是 2 位小数，planner 出图用的是内存全精度，两者差异有 2.02 的基线；
重规划效果要和 `render(plan)` 比，不要和 planner 自己出的 PNG 比，否则会把基线差算成泄漏。
渲染本身跨进程是确定的（同一文件两次渲染 mean_abs 0.0000）。

**改夜色要动光影，不是全局套一层暖色**：全局 hue+压暗会把整幅压成单色棕
（实测玫瑰 41.8→66.5 反而更亮、色彩尽失）。正确姿势是**按亮度分离**：从渲染图取 luma 分位做掩膜，
暗部（≤35 分位，约 40% 画布）加暖压深、高光（≥97 分位，约 5%）提亮、中间调轻降曝光。
实测同一张图：阴影 15.9→10.0、高光 88.7→99.6、玫瑰 41.8→43.8、叶子 57.9→60.8（主体完好）。
`edit_loop` 的 recipe 步骤现在支持 `"all": true`（全画布，不引入硬掩膜边界）。
注意笔触级掩膜是硬的：暗环/叶缘硬边仍会出现，真正的再打光要 albedo/shading 分解或重打光模型。

**"白玫瑰中间残留一道红"的根因**：不是选择漏了，而是**只给 bed/band 脱了色**。
按层统计红色笔触：band 721/721、bed 52/52、**highlight 539/539**、ink 20/20 全部被选中，
但 `tone_map` 只压亮度、不改色相，highlight 层的偏红高光笔触被压暗后仍是红色 → 提亮（gain 0.65→0.93）
对比变强才看出来。补上 `set_layer_saturation highlight 0.06 / ink 0.10` 后残留红像素 359 → **0**。

**语义改图闭环脚本 `edit_loop.py`**：一次调用 = 一个迭代（掩膜→按层取权重→应用 ops→渲染→逐区域度量→
对照图 + mask overlay + ops 全部落盘到 `--out-dir/<tag>/`）。实测"玫瑰换白"两轮迭代：
it1 gain 1.45 → 玫瑰 luma 34.9→178.1，it2 gain 1.18 → 34.9→163.3，两轮 `outside mean_abs_change` 0.01 / 0.00
（掩膜隔离干净）；模型看 `sheet.jpg` 判断该往哪调，没有任何查表。

**语义改图闭环（模型视觉提示 + 辅助函数）**：模型看图给种子点和框 →
`visual_mask.py` 用 **HSV 色相门控 + 连通性 + 框硬约束** 长出像素级 raster 掩膜（纯 RGB 阈值在暗部会失效：
深红 (67,0,1) 与深绿 (8,18,16) 逐通道差仅 45~59，会把背景吃进主体）→ 叠洋红 overlay 让模型自己验收边界 →
`instruction_demo --mask-file --mask-mode all` 应用 ops → 逐区域度量。实测："把玫瑰换成白玫瑰"：
玫瑰 (184,4,10)→(220,217,217)，叶子 (21,35,18) 逐字节不变（luma 31.6→31.4），背景 luma 16.6→17.5。
没有任何"指令→参数"映射表：ops 是模型看着当前渲染临场写的。

**外观参数三层（默认值也是数据，不是代码）**：`scripts/native/look.json` 是默认 profile，
优先级 `--ops`（单次） > `--look <file>`（本次） > `native/look.json`（仓库默认） > 恒等。
改 JSON 即改默认出图，不用改代码、不用发版；实测 `painting_clean`：恒等 25.69 → profile gain 0.95 得 24.37
→ 再叠 `--ops` gain 0.85 得 20.61（`render_plan.py --look/--ops`）。要换亮度基准不手改数字：
`calibrate_tone.py --target-image <ref>` 求出 gain 再写回 profile。

**亮度不硬编码**：用 `scripts/calibrate_tone.py` 从参考图现场反解 gain：

```
python scripts/calibrate_tone.py plan/painting_clean.plan.json \
    --target-image _shots/vid_last.png --base-ops tone_base_nohl.json \
    --out tone_calibrated.json --gpu
# -> solved_gain 0.9174, achieved_luma 22.323 (target 22.28, abs_error 0.041), 6 次渲染
```

做法是渲染器实测 luma + 几何二分（亮度对 gain 近似幂律），参考图换了 gain 就跟着变；
`tests/test_calibrate_tone.py` 验证 roundtrip（已知 gain 0.62 -> 反解 0.6203）。
历史教训：手写 gain 1.3 + lift 0.02 + gamma 1.1 会到 43.1（约两倍亮），是没标定，不是渲染错。

**texture 层 tile 合并**（消除方块接缝）：计划里 texture 是 81 笔 96px 的 `02_rough-canvas` tile 铺满画布，
在 normal/提亮后能看到方块边。`scripts/merge_canvas_tiles.py` 把它们合并成一张覆盖全画布的纹理笔：

```
python scripts/merge_canvas_tiles.py plan.json plan_fixed.json   # 81 笔 -> 1 笔
```

实测：81 → 1 笔（size 1417.8 覆盖对角线），整图平均亮度 **11.2 → 11.3**（基本不变），
方块接缝消失；`tests/test_merge_tiles.py` 断言合成 1 笔/覆盖画布/颜色取均值/其它层与混合模式不动。

### 导出：.kra / .psd 现在带混合模式（以前 8 层全写 svg:src-over）

`export_layers.write_kra` 之前把每层写死 `composite-op="svg:src-over"`、`write_psd` 写死 `norm`，
于是 Krita/PS 里看到的预览和引擎按计划渲出来的不是同一张（计划里 `shadow`/`texture` 是 multiply、
`ambient` overlay、`highlight` screen、`glaze` softlight）。

现在两个导出函数都接受 `blends`（层名→模式 或与层并行的 list），映射表 `KRA_BLEND`/`PSD_BLEND`；
不传 `blends` 时仍回落到 normal（向后兼容）。`planner_8layer.py` 已把 `spec` 里的模式传进去。
实测导出一份真实计划：

```
bed svg:src-over | band svg:src-over | ink svg:src-over | shadow svg:multiply
ambient svg:overlay | highlight svg:screen | glaze svg:soft-light | texture svg:multiply
```

`tests/test_export_blends.py`：kra 的 composite-op 逐层正确、不传 blends 时仍是 src-over、
psd 写出 `8BIMmul `、softlight/overlay/未知模式映射，全 PASS。

### 亮度口径：画面偏暗不是 GPU 的锅（实测）

同一份计划，CPU 与 GPU 各渲一次整图：

| 项 | CPU | GPU |
|---|---|---|
| 平均亮度 mean-luma（0–255） | 11.17 | 11.21 |
| 差异 | — | `diff_px=38,535`、`px>4=707`、`mean=0.028` |
| 耗时 | 11.26 s | 2.29 s |

结论：GPU 与 CPU 的亮度差 **0.04/255**，不存在"GPU 把图压暗"。
真正的原因是计划本身：源图 mean-luma ≈ **60–73**，而这份计划渲染出来只有 **11**——
`shadow` 与 `texture` 两层都是 multiply，叠完把整体压掉约 5 倍（这正是油画暗调的来源，不是 bug）。
修法已落地：新算子 `tone_map`（gain/lift/gamma，可逐层）实测把 11.2 抬到 **47.9**（gain 1.8 + lift 0.05 + gamma 1.25，再对 shadow/texture 各 ×1.25），
增量 == 整份重画 **0 px**。只调 `set_layer_brightness` 没用（texture×1.6 + shadow×1.35 + bed×1.15 只到 12.5）——
因为那是乘 HSV 的 V，对 multiply 层的暗色几乎无效；要动 gamma/lift 才抬得动黑场。

## 性能现状（900×1100 / 57,812 笔 / 8 层，实测 3 次取中位）

| 后端 | 整图（900×1100 / 57,812 笔 / 8 层） |
|---|---|
| CPU（8 线程） | 10.6–11.9 s |
| GPU | 2.2–2.3 s（4.8–5.2×） |

- GPU 阶段：expand ~0.50 s / upload ~0.28 s / splat ~2.0 s（含分组）/ composite ~0；`disp` 计数 19,056。
- 视频 600 帧在这版重构后**没有重测**（链路与整图相同，旧值 24.9 s + 2.6 s 编码仅作历史参考，不要当成本版实测）。

### 提速是怎么来的（以及一个不能走的捷径）

- **实测瓶颈是 dispatch 次数**：整图 57,812 笔 = 57,812 次 `Dispatch`（prof 里 `disp` 计数），每笔约 44 µs。
  证据：把 `InterlockedMax` 换成普通写、把 cos 换成查表、提升分支1的除法，splat 都没有变化；
  把 SRV/UAV/CB 设置提出循环只省 ~10%。所以不是 ALU/原子/分支发散。
- **每笔参数烘进 stamp 记录**：每个 stamp 24 个 float（x,y,R,α / dir,kLocal / rgb,opacity /
  branch,lut_slot,cosn,kw / tw,tc,thr,region_id / tip 参数），`cs_splat` 和 `cs_over` 都从记录里读参数，
  一次 dispatch 可以画多笔。scratch 的 key 高 16 位放 α、低 16 位放「获胜 stamp 下标」，over 才能找回该 stamp 的颜色。
- **不能**把一整批笔触用一次 over 画完：那样每像素只保留 α 最大的 stamp，丢掉 source-over 的逐笔顺序语义
  （第一版这么写，整图 97% 像素错，alpha 混合结果全变）。
- 正确做法是**按覆盖不重叠分层**：新笔触的组号 = max(它覆盖格上已有组号) + 1，格大小 16 px（bbox 划格，保守）。
  这保证任意两个真正重叠的笔触都落在不同组、且组的先后与笔触先后一致；同组内像素互不冲突，一次 over 等价于逐笔叠加。
- 结果：dispatch 57,812 → **19,056**（平均 ~6 笔/组，组数受画面重叠深度限制），整图 3.8 → **2.2–2.3 s**（CPU 的 4.8–5.2×）。
- 精度代价：α 量化到 16 bit，换来下标编码位。整图 CPU↔GPU `diff_px=38,535 / px>4=707 / mean=0.028`，
  与旧路径（38,467 / 707）同级；26 种混合模式、stamp 颜色、tile、API、实例隔离测试全过。
- 继续提速的边际很小：splat 2.0 s 里约一半是分组 CPU 开销 + 1.9 万次 dispatch，再快需要 per-pixel 有序列表
  或降低画面重叠深度，收益不足以再动结构。

## 为什么以前看起来"没问题"（关键证据）

用 `git worktree` 把最早的预编译 DLL 提交 `558b7a5` 取出来，跑同一个混合探针（用**那个提交自己的 DLL**）：

| 混合 | mean\|Δ\| | px>4 |
|---|---|---|
| normal | 0.000 | 0 |
| multiply | 3.091 | 5,094 |
| overlay | 2.572 | 5,200 |
| softlight | 1.124 | 5,007 |

也就是说：**混合模式的对齐从来没成立过，不是后来回归的。** 之前"通过"的测试只覆盖 normal：

| 测试 | 实际覆盖 |
|---|---|
| `test_gpu_tile` | 两层**都是 normal**；gpu tile == gpu full 逐位、gpu vs cpu mean 0.0000 |
| `test_stamp_color` | 单层逐 stamp 色彩（mean dev 0.001），不涉及图层混合 |
| `test_fx_gpu` | 效果层（impasto/smudge），不涉及计划图层混合 |

文档里原来那句"CPU 与 GPU 之间存在约 1 像素的边缘判定差（视觉等效，非逐位）"也从未在混合模式上量过，
属于以偏概全；已换成上面的实测表。

### 我在这里犯的三个错（记录，防止再犯）

1. 只读到 `paint_native.py` 里 `set_color_dynamics` 的注释「GPU 后端暂不支持」，就写成"引擎没有 GPU 后端"——
   那句话只针对这一个功能。
2. 发现整帧差异后，写成"GPU 与 CPU 成体系不等价"——没做分项隔离。
3. 隔离出"只有混合模式不一致"后，又下结论"混合模式从来没对齐过"——没做最小复现，**真因是 CPU 侧的图层基准错位**，
   GPU 反而是对的。
   三次都是同一个毛病：**没先跑最小复现/现成测试就下结论**。现在 `test_gpu_*` 已进必跑清单，
   `test_gpu_blends` 里也留了这张探针的历史与真因。




## 辅助函数索引（改哪一层，先看这里）

| 脚本 | 职责 | 关键契约 |
|---|---|---|
| `planner_region.py` | 只在掩膜内按参考图重画笔触，按层合并 | 默认 `cover/0.9/auto-ring`；原位替换保绘制顺序；直接写回 JSON，不经 dump_plan |
| `pick_instance.py` | 模型自选实例（种子点或 polygon）→ 长掩膜 → 客观验证 → 失败给 hints | `verify` 拦空/过小/过大/多块/贴边/占满框；`--attempt-log` 记每轮 |
| `accept.py` | 编辑验收门（性质断言） | schema/确定性/区外不变/方向；`--subject-tol` 给定时才查主体幅度，默认不查 |
| `lineage.py` | 编辑谱系账本（append-only JSONL + 快照） | `record/tree/checkout`；无快照不可 checkout |
| `mask_registry.py` | 掩膜 id + 来源 | `add/list/get`；掩膜按 id 拷进注册表目录 |
| `visual_mask.py` | 种子点 → 掩膜（色相门控 + 连通生长） | 支持 `--negative/--add-mask/--sub-mask` |
| `edit_loop.py` | 一步编辑 + 产物 + 度量 | `--ops-json -` 从 stdin；`--recipe` 多步；`--zoom` 局部放大 |
| `ops.py` | 语义算子层 | selection 可内联 `mask/mask_file/polygon/instance/all`；`replan_region` 是算子 |
