# Plan ⇄ SVG 双向桥

把厚涂 `plan.json` 转成可逐笔编辑的 SVG，再读回 plan。

- 一个 stroke = 一个 `<path>`；
- 一个 layer = 一个 `<g>`；
- `data-*` 保存 size/opacity/color/hardness/preset/tag/pressure；
- 读回时恢复 strokes、layers、blend、tag、pressure。

## 基准（本机，44,589 笔 / 17 层 / 1200×900）

| 方向 | 耗时 | 产物 |
|---|---:|---|
| plan → SVG | **0.41 s** | 14.9 MB SVG |
| SVG → plan | **0.59 s total**（parse 0.49 s + 写 plan） | 8.7 MB plan |

两个方向都在亚秒级；这是本机 44,589 笔 / 17 层 / 1200×900 的单次测量，不是跨机器基准；计时不含浏览器渲染。写回 plan 走快速 JSON 路径，已用 `plan_io.py check` 验证。

## CLI

~~~powershell
python scripts/plan_to_svg.py plan.json out.svg
python scripts/svg_to_plan.py out.svg back.plan.json
~~~

`plan_to_svg.py` 默认写柔边 filter；`--no-filters` 更快、更小，但软笔刷视觉上更硬。

## API

~~~python
import plan_svg

svg = plan_svg.plan_to_svg(doc)          # str
back = plan_svg.svg_to_plan(svg)         # dict(canvas/layers/strokes)

plan_svg.dump_plan_svg("in.plan.json", "out.svg")
plan_svg.load_svg_plan("out.svg", "back.plan.json")
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
- 逐点压力：保存在 `data-p`，SVG 视觉本身不按点变压力；
- 任意 SVG 的曲线：读回时采样为折线；`A` 弧不支持；
- `transform`/CSS 类/外部引用不解析；只认 bridge 结构和基础 `<path>`；
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

覆盖 roundtrip、层序/blend、tag、pressure、确定性、文件落盘。
