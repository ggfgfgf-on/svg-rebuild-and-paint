# svg-rebuild-and-paint

**位图 → 纯 SVG 重绘** · **程序化厚涂绘画引擎**（确定性回放 / 增量重绘 / CPU + GPU 双后端）

> An asset-agnostic DSH skill repository with two runnable halves: a pixel-level SVG reconstructor
> and a deterministic thick-paint rendering engine. Everything below is measured, not claimed.

仓库名沿用了历史名称，内容早已不止 SVG。现在它是一个 DSH Skill 仓库，包含两块可运行、可验收的实现：

1. **像素级 SVG 重绘** —— 任意位图（PNG/JPEG，≤2MP）→ 单文件自包含 HTML，纯 SVG 平涂路径 + 离散步骤播放器。
2. **程序化厚涂绘画引擎** —— 8 图层计划 → 笔触光栅化 → 30fps 视频；支持确定性回放、差量/单层/tile 增量重绘、GPU 后端、逐 stamp 上色、PSD/KRA 互操作。

### 关于仓库名

仓库原名为 `generic-pixel-svg-rebuild`，现已更名为 **`svg-rebuild-and-paint`**（内容早已从纯 SVG 扩展到完整绘画引擎）。
GitHub 会自动重定向旧链接：`github.com/ggfgfgf-on/generic-pixel-svg-rebuild` → `github.com/ggfgfgf-on/svg-rebuild-and-paint`。
Skill 的内部名称仍叫 `generic-pixel-svg-rebuild`（安装目录 `$DSH_HOME/skills/generic-pixel-svg-rebuild`），因为那是 skill id，与仓库名解耦。

---

## 能力与实测数字

| 能力 | 实现 | 实测 |
|---|---|---|
| 纯 SVG 重绘 | `skill/scripts/vectorize.py` | 未覆盖像素 0；100% 像素 ≤10/255；≥90% ≤5/255；平均 <2/255 |
| 30fps 视频 | `render_video.py` + `planner_8layer.py` | 720 帧 **8.07 s**（进程内）/ 8.76 s 墙钟 |
| 计划级差量重绘 | `paint_diff.py`（稳定 sid） | 60 行 / 1.4 KB 补丁 → **0.028 s** vs 全量 5.81 s（**209×**，逐位相同） |
| 单层增量 | `tile_inc.py` | **2.9×**，逐位相同 |
| tile 局部重绘（CPU） | `tile_inc.rebuild` | 全量 38.45 s → **0.395 s（97×）**，`max_delta = 0` |
| tile 局部重绘（GPU） | `paint_gpu.dll` clear_rect + 区域图 | 2.24 s → **0.314 s（7.1×）**，`max_delta = 0` |
| 多 tile 合并 + 合成缓存 | `tile_inc` / cache patch | 4 块 / 3,629 笔 **0.985 s**，逐位相同 |
| 逐 stamp 上色（CPU） | `paint_set_color_dynamics` | alpha 逐位不变；同笔 20 种颜色；**+23%** 开销（2.41 → 2.97 s @947×1147×8层） |
| 逐 stamp 上色（GPU） | 打包键 `(alpha16<<16) \| (65535-stampIdx)` | 与 CPU 均值差 **0.001**；关闭时走原路径逐位不变 |
| GPU smudge / impasto | `paint_fx.dll`（DirectCompute） | 1024²：smudge **8.1×**；4096²：impasto **6.8×**；与 CPU 最大差 **1.4e-6**（float32 舍入） |
| 笔感扩展 | `brush_ext.py` | smudge / 浮雕光照 / 纸纹变体 / 色彩动态 / 速度响应 / PSD+KRA 读回 |
| PSD 互操作 | 内置 8BPS 解析器 | 8/16bit、raw+RLE、unicode 图层名、图层组；不依赖 psd-tools |

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
