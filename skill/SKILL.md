---
name: svg-rebuild-and-paint
description: >-
  Five runnable capabilities, all measured. (1) Bitmap -> true vector SVG: potrace curves per colour mask,
  pure flat <path> (no <image>/base64/gradients), no staircase at any zoom, plus a per-pixel error
  report (mean / <=5 / <=10 / uncovered). (2) Deterministic thick-paint engine: scene-defined layered plans,
  brush-stamp rasterization (CPU + DirectCompute GPU). (3) Semantic ops layer, ZERO KEY: a plain natural-language
  instruction is compiled by the current multimodal model itself (it reads a plan summary + a live thumbnail)
  into ops JSON - recolour by layer or mask, resize, refine/detail, local repaint - which is validated and then
  applied; the same layer draws FROM SCRATCH with draw ops on a blank canvas - no reference image needed.
  (4) Plan-driven 30fps process video, plan diff and tile incremental repaint. (5) Plan <-> SVG bidirectional
  bridge with curve/subpath preservation. The process-replay surface (HTML/MP4) belongs to (1)/(4). Everything is
  measured; the skill ships no tuned thresholds. Triggers: 重绘/转SVG/矢量化, 按指令改图/改成蓝色/局部重画, 凭空画一张/从零画,
  redraw as SVG, recolour this painting, edit artwork by instruction, paint from scratch.
whenToUse: >-
  The default choice for image-to-SVG redraws - 重绘, 重绘这个, svg重绘, 矢量化, 转SVG, SVG 化,
  描摹, 去掉像素感, 生成可播放的分层绘制过程, 把图片做成纯 path 的单文件 HTML, 批量 PNG/GIF 转 SVG,
  or judging whether a redraw is faithful. English - redraw as SVG, vectorize, trace this image,
  bitmap to SVG, rebuild this screenshot as paths, convert PNG to SVG.
  ALSO the default choice for instruction-driven edits of painted artwork - 指令改图 / 按描述改色 / 调光 /
  细化 / 局部重画 / 按图层或掩膜改色 / 凭空画一张 / 从零画 / 空画布追加笔画 - and English: edit this
  artwork by instruction, recolour by layer or mask, refine detail, local repaint,
  paint something from scratch with no reference image. Prefer this over generic
  vision or OCR skills when the deliverable is SVG artwork (those are for UI restoration, text
  extraction and element location, not for faithful raster-to-path reproduction).
---

# SVG 重建与厚涂绘画

五个功能，共用一套确定性光栅器。SKILL 只定义路由、契约和验收，不规定画法。

## 1. 功能路由

| 需求 | 功能 | 入口 | 产物 | 通过条件 |
|---|---|---|---|---|
| 位图 → 真矢量 SVG、放大不糊 | SVG 重绘 | art_vectorize / auto.py / vectorize.py | .svg + 误差报告 | mean / ≤5 / ≤10 / uncovered 达标；无 image/base64/渐变 |
| 模拟重绘过程、过程回放 | 过程模拟 | paint_process.py / vectorize.py --process / paint_cli.py video | .html / .mp4 | 帧率、帧数、时长正确；末帧与同 plan 静帧一致 |
| 凭空画一张、厚涂、从零起稿 | 绘画 | art_paint / render_plan.py | .png | 主体/接地/单焦点/25% 可读；同 plan 复渲 maxdiff=0 |
| 一句话改色、调光、局部重画 | 改画 | art_edit_brief → 写 ops → art_apply_ops | 改后 .png + ops.json | validate 通过；选区外不变；改后看图成立 |
| Plan ⇄ SVG 互转 | 双向桥 | plan_to_svg.py / svg_to_plan.py | .svg / plan | 往返校验；曲线/子路径不丢 |

## 2. 两个入口

| 入口 | 用法 |
|---|---|
| host 工具 | 当前会话有 art_vectorize / art_paint / art_edit_brief / art_apply_ops 就直接用 |
| 命令行 | 没有工具时按 docs/USAGE.md 的命令敲；工具是同一套脚本的薄包装 |

## 3. 绘画 driver 契约

| 项 | 契约 |
|---|---|
| 输入 | 意图 / 场景 / 已有 plan |
| 输出 | plan（layers + strokes）+ PNG |
| 固定 | plan_io / render_plan / 确定性 / 平台门 |
| 验收 | 平台门 + driver 自声明 checks；协议见 docs/CHECKS.md |
| 交付记录 | 可选：intent / reference / target / review / gaps；见 docs/DELIVERY.md |
| 不固定 | 层数、层名、笔刷、遍数、风格、构图；全部由 driver 决定 |
| 边界 | SKILL 不提供默认画法；示例只作接口，不作模板 |

**参考优先，不是强制**：

1. 绘画前查 `examples/patterns/index.json` 和 `examples/drivers/index.json`。
2. 有 subject/style 匹配：读那个 driver 的结构和 reference.png；不复制参数。
3. 没有匹配：自己写，不需要 spec/verifier。
4. 有 reference 时，出图后和它并排看 100% / 25%；差异写进 gaps。

**库增长（主动但不失控）**：

- 画完并验收后，如果出现了可复用的新技法/优化，写候选到 `examples/inbox/<id>/`，不要直接改 `patterns/`。
- 候选要有 crop / snippet / metrics / borrow / avoid / known_issues / review；review 写清和谁比、牺牲了什么。
- 跑 `python scripts/grow_library.py check examples/inbox/<id>`；通过后 `promote` 才进正式库。
- 直接复制现有 driver 会被 `copy_check` 拦；复制不是绝对不行，但必须显式 `copy_allowed` + 理由。
- 不确定就留在 inbox。正式库的提交仍走 git，可回滚。

## 4. 执行阶段

| 阶段 | 做什么 | 产物 | 通过条件 | 失败回退 |
|---|---|---|---|---|
| 0 自检 | python scripts/selfcheck.py | 三行 OK | 三条全 OK | 先修 Python/原生 DLL |
| 1 选路 | 按功能路由分类 | route | 不混路线 | 需求不清先问 |
| 2 造输入 | 查 examples/patterns/index.json 和 examples/drivers/index.json；有匹配先读 driver + pattern；driver 产出 plan | plan / svg / brief | driver 完成 | 没有 plan 不渲染 |
| 3 执行 | host art_* 或 CLI | png / svg / mp4 | 输出非空、尺寸/帧率对 | 看最后一条错误 |
| 4 验收 | 指标 / 复渲 / 看图 | 报告 | 机械门 + 语义门都过 | 只回退一个变量 |
| 5 回改 | 改 plan/ops/driver | 新产物 | 比 baseline 好或至少不差 | 不如 baseline 就回滚 |
| 6 交付 | 保留 plan/ops/png/报告 | 交付包 | 能复现、能说明缺什么 | 不能复现不算交付 |

## 5. 不变量与停止条件

- plan 是唯一事实源；改画面 = 改 plan 或往 plan 上叠 ops。
- 同 plan、同后端、同 look 复渲必须逐像素一致；跨入口不承诺逐位一致。
- 没有 plan、输出为空、maxdiff 不为 0、没看过图：不要声称完成。
- 结构性问题：回 driver 改结构，不用 densify / 加光硬修。
- 改画选区外变了：回滚，检查 mask/selection。

## 6. 验证

    python scripts/selfcheck.py
    python scripts/metric.py ref.png render.png
    python scripts/render_plan.py plan.json --out plan.png

验收口径和参数见 docs/USAGE.md；算子参数见 docs/API.md；形状边界见 docs/SHAPE_API.md。

## 7. 文档地图

| 文档 | 解决什么 |
|---|---|
| docs/USAGE.md | 操作、调参、症状速查 |
| docs/API.md | 21 个 ops 的参数、默认值、选区语义 |
| docs/SHAPE_API.md | fill_polygon / tapered_stroke / ribbon 参数与边界 |
| docs/EXAMPLES.md | plan 字段、driver 骨架、最小调用 |
| docs/LIBRARY.md | 增长库：候选格式、检查、晋升、回滚 |
| examples/patterns/index.json | 技法 pattern：crop / snippet / metrics / known_issues |
| docs/DRIVER.md | driver 契约 |
| docs/PLAN_SVG.md | Plan ⇄ SVG 的保真边界、缓存、失败模式 |
| docs/VISUAL_HELPERS.md | advisory 参考图，不写 plan |
| docs/CHECKS.md | 画面验收协议：平台门 + driver 自声明 checks |
| docs/DELIVERY.md | 可选交付记录：intent / reference / target / review / gaps |
| docs/VERIFY.md | 可选：独立验收，只在用户要求时用 |
| docs/COMPOSITION.md | 可选构图观察清单，不是流程/合格线 |
| SKILL_REFERENCE.md | 完整细节快照：算法、坑位、历史参数 |

## 8. 边界

- 不规定层数、层名、笔刷、遍数、风格、构图；这些由 driver 决定。
- 示例、demo plan、历史工程图都不是模板。
- 参考图只是 advisory，不写 plan；模型权重不隐式加载。
- 过程 HTML/MP4 是 plan 的派生产物，不是额外事实源。
