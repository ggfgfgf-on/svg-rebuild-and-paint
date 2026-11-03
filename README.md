# svg-rebuild-and-paint

**位图 → 真矢量 SVG 重绘（曲线，缩放无台阶）** · **程序化厚涂绘画引擎**（确定性回放 / 增量重绘 / CPU + GPU 双后端）

> An asset-agnostic DSH skill repository with two runnable halves: a potrace-based curve SVG
> reconstructor (the pixel-run mode is deprecated) and a deterministic thick-paint rendering engine.
> Everything below is measured, not claimed.

仓库名沿用了历史名称，内容早已不止 SVG。现在它是一个 DSH Skill 仓库，包含两块可运行、可验收的实现：

1. **真矢量 SVG 重绘** —— 任意位图（PNG/JPEG，≤2MP）→ potrace 逐色掩码曲线重建（2–3× 超采样）→ 单文件自包含 HTML，纯 `<path>` 平涂几何 + 离散步骤播放器；纯矢量，放大无台阶。
2. **程序化厚涂绘画引擎** —— 8 图层计划 → 笔触光栅化 → 30fps 视频；支持确定性回放、差量/单层/tile 增量重绘、GPU 后端、逐 stamp 上色、PSD/KRA 互操作。

### 关于仓库名

仓库原名为 `generic-pixel-svg-rebuild`，现已更名为 **`svg-rebuild-and-paint`**（内容早已从纯 SVG 扩展到完整绘画引擎）。
GitHub 会自动重定向旧链接：`github.com/ggfgfgf-on/generic-pixel-svg-rebuild` → `github.com/ggfgfgf-on/svg-rebuild-and-paint`。
Skill id 也一并改成了 **`svg-rebuild-and-paint`**（安装目录 `$DSH_HOME/skills/svg-rebuild-and-paint`），仓库名与 skill id 现在一致。

名字里的 “pixel” 同样是历史遗留：实现早已从**像素游程**（逐像素矩形，放大见台阶）转成**曲线重建**，见下节验收口径。

---

## 能力与实测数字

| 能力 | 实现 | 实测 |
|---|---|---|
| SVG 曲线重绘（默认） | `vectorize.py`（potrace + 2–3× 超采样） | K=192/2×：193 path、7.36 MB、mean **8.35**、≤5 44.6%；K=384/3×：385 path、19.7 MB、mean **6.36**、≤5 61.0%；验收口径 mean<10、uncovered=0、无台阶 |
| SVG 曲线重绘（vtracer 备选） | `smooth_svg.py` | 12,034 path / 3.47 MB、mean **7.20**、≤5 65.1%；比 potrace 结果更碎，适合追高保真 |
| 像素游程模式（**已弃用**） | `build_svg.py` / `autotune.py` | 游程矩形 4133 path / 4.06 MB、mean **2.16**、≤5 96.7%，**放大有像素台阶**；只用于复现旧的 mean<2 数字，不作默认、不作交付 |
| 30fps 视频 | `render_video.py` + `planner_8layer.py` | 720 帧 **8.07 s**（进程内）/ 8.76 s 墙钟 |
| 计划级差量重绘 | `paint_diff.py`（稳定 sid） | 60 行 / 1.4 KB 补丁 → **0.028 s** vs 全量 5.81 s（**209×**，逐位相同） |
| 单层增量 | `tile_inc.py` | **2.9×**，逐位相同 |
| tile 局部重绘（CPU） | `tile_inc.rebuild` | 全量 38.45 s → **0.395 s（97×）**，`max_delta = 0` |
| tile 局部重绘（GPU） | `paint_gpu.dll` clear_rect + 区域图 | 2.24 s → **0.314 s（7.1×）**，`max_delta = 0` |
| 多 tile 合并 + 合成缓存 | `tile_inc` / cache patch | 4 块 / 3,629 笔 **0.985 s**，逐位相同 |
| 逐 stamp 上色（CPU） | `paint_set_color_dynamics` | alpha 逐位不变；同笔 20 种颜色；**+23%** 开销（2.41 → 2.97 s @947×1147×8层） |
| 逐 stamp 上色（GPU） | 打包键 `(alpha16<<16) \| (65535-stampIdx)` | 与 CPU 均值差 **0.001**；关闭时走原路径逐位不变 |
| GPU smudge / impasto | `paint_fx.dll`（DirectCompute） | 1024²：smudge **8.1×**；4096²：impasto **6.8×**；与 CPU 最大差 **1.4e-6**（float32 舍入） |
| 计划空间改画（指令 → 算子） | `ops.py` / `masks.py` / `compile_ops.py` / `instruction_demo.py` | 909×1100/57,812 笔：编辑 **0.087 s**、局部重算 **0.065 s**（**编辑到出像素 0.15 s**，不含读盘）；增量与整份重画**逐位相同**，脏矩形外 **0 px**；`guidance=0` → 计划逐字节不变 |
| 改色（蓝玫瑰） | `paint_cli edit` + 颜色掩膜 | 2,862 笔红色笔触 → hue 218，背景/叶子零影响；几何掩膜这条路已验证走不通（大笔触横跨花头与背景） |
| 指令编译评测 | `eval_instructions.py` | 简单集 30 **30/30**；难集（含 10 条应拒绝）v1 **19/20** → 修正版 **20/20**，失败均被 schema 拦住；视觉框选改画区域外 **0 px** |
| 统一入口 | `paint_cli.py` | `edit` / `compile` / `video` / `eval` 四个子命令；编辑→出图→出视频一条命令（28.4 s 起） |
| 改画 → 30fps 视频 | `paint_video.py` | 600 帧 / 30 fps / 20.0 s；渲染 54.0 s + 编码 2.53 s = **59.8 s**；末帧与整份重画**逐位相同（0 px）** |
| 笔感扩展 | `brush_ext.py` | smudge / 浮雕光照 / 纸纹变体 / 色彩动态 / 速度响应 / PSD+KRA 读回 |
| PSD 互操作 | 内置 8BPS 解析器 | 8/16bit、raw+RLE、unicode 图层名、图层组；不依赖 psd-tools |

## SVG 重绘的验收口径（两个目标互斥，必须写清）

旧文档同时要求「游程像素」与「曲线」并要求 `mean < 2`、`100% ≤ 10`。这两件事**不可能同时成立**：

- 像素级精确复现**必须**用轴对齐整数游程——台阶本身就是编码方式，不是缺陷；平涂源能到 mean 0.43，AA 立绘 2.16。
- 任何曲线拟合在抗锯齿源上必然偏离 2/255 以上：`vectorize.py` 的实测区间是 **mean 3.74 … 8.35**。

因此交付口径按分支区分：**默认 `vectorize.py`，预算 mean < 10、uncovered = 0、无台阶；像素分支已弃用**，只保留复现历史数字的能力（`build_svg.py` 运行会直接打印 `DEPRECATED`，`autotune.py` 驱动的也是这条已弃用路径）。
源图带 AA 时不要硬凑 `100% ≤10`，改写「uncovered=0、mean<3、≤5 ≥95%」并说明瓶颈在源图 AA 与量化上界。

## 确定性契约

三条硬约束，全部由测试守住：

1. **同计划 → 逐位相同的像素**（同后端重复渲染 `max_delta = 0`）。
2. **增量结果 == 全量重画**（diff / 单层 / tile / 多 tile / 缓存补片，全部 `np.array_equal`）。
3. **CPU 与 GPU 视觉等价**：单笔边缘 1 像素差异是已知且有记录的，不做"魔法常数"式对齐。

## 快速开始

```powershell
# 安装（Windows）
Expand-Archive generic-pixel-svg-rebuild-*.zip -DestinationPath .
.\generic-pixel-svg-rebuild\install.ps1
```

```bash
# 安装（macOS / Linux）
unzip generic-pixel-svg-rebuild-*.zip && ./generic-pixel-svg-rebuild/install.sh
```

安装器会把目录放进 `$DSH_HOME/skills/generic-pixel-svg-rebuild`（默认 `~/.dsh-home/skills`）并装 Python 依赖。

```bash
# 1) 位图 → SVG / 步骤播放器
python skill/scripts/vectorize.py ref.png out.html 192
python skill/scripts/metric.py   ref.png shot.png          # mean / <=5 / uncovered

# 2) 绘画引擎
python skill/scripts/planner_8layer.py <config>            # 生成分层计划
python skill/scripts/render_video.py  <plan.npz> out.mp4   # 30fps 视频
```

入口手册：`skill/SKILL.md`；设计说明与测量：`skill/IMPLEMENTATION.md`。

## 测试

```bash
python skill/tests/run_tests.py           # SVG 重绘回归（fixtures）
python skill/tests/test_incremental.py    # 差量 / 单 tile / 多 tile / 缓存，逐位
python skill/tests/test_stamp_color.py    # 逐 stamp 上色（CPU + GPU）
python skill/tests/test_gpu_tile.py       # GPU tile 逐位 + CPU 对齐
python skill/tests/test_brush_ext.py      # 笔感扩展 + PSD/KRA 回环
python skill/tests/test_effects_pipeline.py  # 效果 + 增量管线
python skill/tests/test_fx_gpu.py         # GPU smudge/impasto 精度 + 性能
```

绘画侧 6 个套件共 **67 条断言**，全部以"逐位相同"或"float32 舍入范围内"为判定。

## 目录结构

```
skill/
  SKILL.md                 # 入口手册（含局部改画 / 逐 stamp 色彩 / GPU fx 章节）
  IMPLEMENTATION.md        # 设计与测量记录
  AUDIT.md                 # 能力审计（哪些有、哪些没有）
  scripts/
    vectorize.py ...       # SVG 重绘线
    planner_8layer.py plan_store.py paint_diff.py tile_inc.py   # 绘画线
    paint_native.py paint_gpu_native.py paint_fx_native.py      # 三个原生后端绑定
    brush_ext.py           # 笔感扩展（CPU 参考实现）
    native/                # C++ 源码 + 预编译 DLL + build.ps1
  tests/                   # 上面那 7 个测试
pixel-svg-reconstruction-generic.md   # 原始方法论文档
```

## 构建原生库

```powershell
powershell -ExecutionPolicy Bypass -File skill/scripts/native/build.ps1 -Gpu
```

源码含中文注释，**必须带 `/utf-8`**；否则 MSVC 按系统代码页解析会报一堆假语法错误（`C2530`、`C2065` 之类）。脚本已经带上，支持 MSVC BuildTools。

## 已知边界（不掩饰）

- CPU 与 GPU 在笔触边缘有 1 像素差异（alpha profile 7 行 vs 6 行），视觉等价但**不是**逐位相同；不做常数对齐。
- GPU 逐 stamp 上色开启时 alpha 走 16bit 量化（8bit 输出上看不出，alpha 最大差 0），关闭时逐位不变。
- smudge / impasto 是后处理 pass，不参与笔触内核；它们在 GPU 上很快，但语义仍是"画完再处理"。
- 没有任何与第三方工具的正面基准对比记录。

## License

GPL-2.0-or-later。tracing 核心是 potrace（编译版 `trace_core.dll` 与纯 Python `potracer` 回退），详见 `skill/NOTICE`；`vtracer`（smooth 模式）是 MIT，不进入 GPL 路径。
笔尖贴图（278 张 128×128 PNG，源自古开源笔刷包的 Krita `.kpp` / GIMP `.gbr/.gih`）来源与许可见 `skill/scripts/native/tips/PROVENANCE.md`，逐条 `src` 记录在 `index.json`。
