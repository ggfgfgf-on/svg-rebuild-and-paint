# Plan ⇄ SVG 双向桥

把厚涂 `plan.json` 转成可逐笔编辑的 SVG，再读回 plan。

- 一个 stroke = 一个 `<path>`；
- 一个 layer = 一个 `<g>`；
- `data-*` 保存 size/opacity/color/hardness/preset/tag/pressure；
- 读回时恢复 strokes、layers、blend、tag、pressure。

## 基准（本机，44,589 笔 / 17 层 / 1200×900）

| 方向 | 耗时 | 产物 |
|---|---:|---|
| plan → SVG | **0.19 s**（native 二进制）/ **0.18 s**（文件直通）/ 0.41 s（Python） | 13.9–14.9 MB SVG |
| SVG → plan | **0.21 s total**（pyd 直转）/ **0.29 s**（文件直通）/ 0.50 s（Python） | 8.7 MB plan |

两个方向都在亚秒级；这是本机 44,589 笔 / 17 层 / 1200×900 的单次测量，不是跨机器基准；计时不含浏览器渲染。native 版是 Windows DLL（RapidJSON + pugixml）；文件直通版在 DLL 内读 plan、写 SVG / 读 SVG、写 plan，避免 Python JSON。

## CPython `.pyd`（dict 直读，Windows）

`plan_svg_ext.pyd` 直接读 `PyObject*`，不再 `json.dumps`：

| 操作 | 耗时（44,589 笔） |
|---|---:|
| `plan_svg_ext.pack_plan(doc)` | **0.019 s** |
| `plan_svg_native.plan_to_svg(doc)` | **0.157 s** |
| `unpack_plan(bytes) -> dict` | **0.207 s** |

`plan_svg_native.pack_plan(doc)` 检测到 `.pyd` 时优先走它；否则回退 C++ JSON 路径。

构建：

~~~powershell
powershell -File scripts/native/build_plan_svg_ext.ps1
~~~

`.pyd` 同时覆盖 dict → PSVB 和 PSVB → dict；反向不再经过 JSON。

## DLL 打包/解包（文件级）

44,589 笔实测：

| 操作 | 耗时 | 产物 |
|---|---:|---|
| `pack_plan_file(plan.json, plan.psvb)` | **0.057 s** | 2.8 MB PSVB |
| `unpack_plan_file(plan.psvb, plan.json)` | **0.117 s** | 7.1 MB plan JSON |

这两个都是 DLL 直接读/写文件，不经 Python 45k 循环。

## 轻量 XML 扫描器（bridge SVG）

bridge 输出带 `data-bridge="plan_svg"` 时，native 不再走 pugixml DOM，而是逐字节扫描 `<g>/<path>`：

- 不建 XML 树；
- 不处理命名空间/DTD/实体；
- 只认 bridge 的 `data-*` 属性；
- 非 bridge SVG 仍回退 DOM。

这是当前 bridge 路径的最快解析。

## `--outline`（生产候选）

```powershell
python scripts/plan_to_svg.py plan.json out.svg --outline
```

一笔一个 `<path>`：

- 每段生成 capsule 子路径；
- 同一笔的所有子路径放进一个 path；
- `fill-rule="nonzero"` 做并集；
- 不再输出 428k circle。

lighthouse 44k：

```text
14.9 MB
2.07 s（Python）
```

可选：

## `--stamps`（可选，native）

`--stamps-tip` 是 Python 的近似 brush tip 版：

```powershell
python scripts/plan_to_svg.py plan.json out.svg --stamps-tip
```

- preset 形状：flat/bristle 用旋转 ellipse；
- soft 用按颜色/硬度分档的 radialGradient；
- 压力仍用近似曲线；
- 纹理仍没有。

lighthouse 44k：约 39.8 MB，0.858 s。


```powershell
python scripts/plan_to_svg.py plan.json out.svg --stamps
```

每个 brush stamp 输出一个 `<circle>`：

- C++ 直接读 PSVB 生成；
- 不整条高斯模糊；
- 软边仍是圆/透明度近似；
- 文件会显著变大，默认关闭。

demo 359 笔：0.0505 s / 4.1 MB。

## PSVB v2 extra（未知字段）

PSVB v2 在末尾追加：

```text
主结构（v1）
+ extra_json bytes
+ u32 extra_len
```

`extra_json` 保存：

- 顶层未知字段；
- layer 的未知字段；
- stroke 的未知字段。

例如 `region_id`、`subject_layers`、自定义 metadata。没有未知字段时不追加，仍是 v1。

## 二进制协议（native 内存）

`plan_svg_native.py` 不再把 plan 序列化成 JSON 给 DLL，而是打包成 `PSVB`：

```text
magic PSVB | version | canvas | layers | strokes | points | tags
```

- `pack_plan(doc)`：Python 扁平打包；
- `unpack_plan(bytes)`：Python 还原 plan；
- `psvg_bin_to_svg(buf,len)`：C++ 直接生成 SVG；
- `psvg_svg_to_bin(svg,len)`：C++ 直接返回二进制 plan。

外部文件仍是 `plan.json` / SVG；二进制只存在于 Python ↔ DLL 的内存边界。

## Native（Windows，可选）

~~~text
native\plan_svg_core.dll   (RapidJSON + pugixml)
~~~

`plan_svg.py` 检测到 DLL 时自动走 native；没有 DLL 时回退纯 Python。构建：

~~~powershell
powershell -File scripts/native/build_plan_svg.ps1
~~~

native 只改变解析/序列化速度，不改变外部 `plan.json` / SVG 格式；XML 由 pugixml 解析；复杂 `transform`/`use`/外部引用不展开。

## CLI

~~~powershell
python scripts/plan_to_svg.py plan.json out.svg
python scripts/svg_to_plan.py out.svg back.plan.json
~~~

`plan_to_svg.py` 默认**不加** Gaussian filter；需要旧版柔边近似时加 `--soft-filter`，会更糊。`--pure` 强制走纯 Python 参考实现。

## API

~~~python
import plan_svg

svg = plan_svg.plan_to_svg(doc)          # str
back = plan_svg.svg_to_plan(svg)         # dict(canvas/layers/strokes)

plan_svg.dump_plan_svg("in.plan.json", "out.svg")
plan_svg.load_svg_plan("out.svg", "back.plan.json")

# native 文件直通（Windows DLL）：DLL 内读/写，不经 Python JSON
import plan_svg_native as N
N.plan_file_to_svg("in.plan.json", "out.svg")
N.svg_file_to_plan("out.svg", "back.plan.json")
~~~

`dump_plan_svg` 返回 `svg_s`（含写盘）；`load_svg_plan` 返回 `parse_s`（含解析 + dump plan）。

## SVG 结构

~~~xml
<g data-name="layer" data-blend="normal">
  <path d="M 10 10 L 100 20" fill="none" stroke="#1e2832"
        stroke-width="9" stroke-opacity="1" stroke-linecap="round"
        data-layer="layer" data-preset="flat" data-size="9"
        data-opacity="1" data-hardness="0.9"
        data-color="30,40,50" data-p="1,0.6" data-tag="glint"/>
</g>
~~~

`d` 是中心线；`data-p` 是逐点压力。读回时优先用 `data-*`，没有则从 SVG 属性推断。

## 保真边界

- 硬边/圆笔：用 stroked path 近似，形状和颜色可读；
- 软边：用 `<filter><feGaussianBlur>` 量化近似，不是原始纹理；
- 纹理笔刷：不重建纹理；只保留 `data-preset`；
- outline 写入 `data-center` 与 `data-d-hash`；d 被编辑后会检测 hash 不一致并改用 d；
- 逐点压力：保存在 `data-p`，SVG 视觉本身不按点变压力；
- 任意 SVG 的曲线：读回时采样为折线；`A` 弧已采样；
- 基础 `translate/scale/rotate/matrix` transform 在 Python 回退和 C++ `psvg_svg_to_plan` 里展开；
- C++ `psvg_svg_to_plan` 已支持 `data-center`、A 弧、transform、基础图元、CSS、`<use>`、gradient 平均色和简单 clip；
- 多 `M` 子路径也已拆成多笔（C++）；
- 基础 `circle/ellipse/rect/polygon/polyline` 读成折线；填充形状写 `preset="fill"`；
- 多 `M` 子路径拆成多笔；
- `clipPath/mask` 的简单多边形/矩形做几何裁剪（凸多边形近似）；
- stamps 也写 `data-center-hash`，中心线被改动时检测不一致；
- 简单 CSS class、`<use href="#id">`、linear/radial gradient 平均色在 C++ 主路径解析；
- 外部引用、mask/clipPath 的真实合成仍不解析；
- 这是“可编辑的桥”，不是像素级 SVG 复刻，也不是 `vectorize.py` 的平涂输出。

## 适用场景

适合：

- 在 Illustrator/Inkscape 里按层/按笔改位置、颜色、删笔；
- 局部改完回 plan 继续渲染；
- 交付一份可编辑的矢量结构。

不适合：

- 把厚涂压成几 KB 的纯平涂 SVG；
- 把任意复杂 SVG 反向变成可渲染 plan；
- 依赖浏览器滤镜逐像素一致的验收。

## 测试

~~~powershell
python tests/test_plan_svg.py
~~~

覆盖 roundtrip、层序/blend、tag、pressure、确定性、文件落盘、outline/stamps 中心线回退、A 弧、transform、基础图元、CSS/use/gradient、clip/fill、native 回退与 C++ 直通。

## paint（真渐变）

stroke 可带 `paint`：type/units/x1/y1/x2/y2/stops。`plan_to_svg` 输出 `<linearGradient>`/`<radialGradient>`；`svg_to_plan` 保留 gradient，不再只取平均色。`color` 仍是平均色 fallback。

## fill renderer

native `paint_core` 新增 `paint_fill_polygon(layer, points, color, opacity)`。`paint_plan` 遇到 `preset="fill"` 会调用它。

## 凹 clip

C++ / Python 均对凹多边形做耳切三角化，再逐三角形裁剪。

## CSS 继承 / symbol

C++ 支持 fill/stroke/stroke-width/opacity 继承；`<use href="#symbol">` 会展开 symbol；伪类仍不静态求值。

## 仍缺

- mask alpha/luminance：仍是二值几何裁剪；
- 外部引用：无 base 路径/资源策略；
- 复杂 CSS：伪类、优先级、!important；
- 厚涂 fill 渐变：native `paint_fill_gradient_polygon` 逐像素插值；
- 厚涂 stroke 渐变：native 单笔逐像素插值；GPU 未同步。

## layer mask（alpha/luminance）

layer 可带 `mask`：`{"encoding":"png-base64","data":"...","luminance":true}`。bridge 会把 `<mask>` 栅格化为灰度 PNG；`paint_plan` 在合成前调用 native `paint_apply_layer_mask` 乘 alpha。

仍限：mask 内 gradient fill 目前取平均亮度，不是逐像素 gradient；mask 只能作用于 layer，不是任意单笔。
