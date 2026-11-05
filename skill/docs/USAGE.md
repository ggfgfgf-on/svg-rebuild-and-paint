# 整套用法与调参手册

> 读者：第一次拿到这套 skill、手上没有上下文的模型。
> 目标只有一个：知道**该走哪个入口、该改哪个参数**，而不是重新发明管线。
> 算子细节见 [API.md](API.md)；坑位与清单见 [SKILL.md](../SKILL.md)；这里只讲操作。
>
> 验证过这份手册够用：一个**零上下文**的模型只读本目录文档，产出了 1280×960 / 24,964 笔 / 20 层 / 8 种笔刷的原创厚涂夜景，
> 经 draft → refined → final 三轮细化（8,171 → 24,964 笔），渲染 1.42 s、复渲逐像素差 0。它卡住的四处已补进 §5.3 与 §6.1。

---

## 0. 结构：四层能力，两个入口

| 层 | 干什么 | 入口 | 产出 | 实测 |
|---|---|---|---|---|
| A 位图 → 真矢量 SVG | 逐色掩膜 + 曲线重建，纯 `<path>`，无 `<image>`/base64/渐变 | `art_vectorize` / `scripts/auto.py` | `.svg` + 误差报告 | 320×180 K=96：91 path / 0.11 MB / 0.6 s |
| B 厚涂绘画 | plan（canvas+layers+strokes）确定性渲染 | `art_paint` / `scripts/render_plan.py` | `.png`（同计划逐像素一致） | 480×320 / 359 笔：0.26 s / 26 KB |
| C 语义改画 | 一句指令 → ops JSON → 作用在 plan 上 | `art_edit_brief` → 自己写 ops → `art_apply_ops` | 改后 `.png` + `ops.json` | 0.34 s（引擎内 0.13 s，129 笔被选中） |
| D 过程视频 | 按笔画顺序逐帧出片（30 fps） | `scripts/paint_cli.py video` | `.mp4` | 60 帧 1.2 s；600 帧 20 s 视频 7.3 s |

取证/验收脚本：`metric.py`（误差）、`selfcheck.py`（30 秒自检）、`attribute.py`（归因）、`calibrate.py`（参数标定）。

**两个入口是同一套脚本**：

- **入口 A：host 工具。** 当前会话工具列表里出现 `art_vectorize` / `art_paint` / `art_edit_brief` / `art_apply_ops` 时直接用，别自己拼命令行——工具已经处理了路径、超时和错误。
- **入口 B：命令行。** 没有工具时照本节命令敲。工具只是薄包装：每次调用起一个新进程，所以**改了 `scripts/*.py` 工具立刻用新代码**，不需要重启。

---

## 1. 五分钟跑通（复制即跑）

在 skill 根目录执行：

```powershell
# 0) 先自检：三条主线各跑一次真实命令，约 11 s
python scripts/selfcheck.py
#    [OK] bitmap -> SVG   mean=1.1554 uncovered=0 213 KB   9.1 s
#    [OK] blank canvas    paths=2                          0.9 s
#    [OK] paint -> video  60 帧 @30fps 2.0 s 25 KB         1.2 s
#    RESULT OK

# A) 位图 -> SVG（"这张图转成矢量、放大不糊"）
python scripts/auto.py ref.png out.svg

# B) 画：计划 -> PNG（"把这张画渲出来"）
python scripts/render_plan.py examples/demo.plan.json --out demo.png

# C) 改：一句指令 -> 改后 PNG（"背景压深成夜色"）
python scripts/compile_ops.py brief examples/demo.plan.json "把背景压深成墨绿夜色" --thumb --out brief.json
#    读 brief.json 里的 thumb 缩略图，按 prompt 写 ops.json，再：
python scripts/paint_cli.py edit examples/demo.plan.json --ops ops.json --out edited.png --fast

# D) 视频：计划 + ops -> 30fps 过程片
python scripts/paint_cli.py video examples/demo.plan.json --ops examples/demo.ops.json --out demo.mp4 --frames 60
```

---

## 2. 四个 host 工具

| 工具 | 参数 | 背后命令 | 落盘 | 返回 |
|---|---|---|---|---|
| `art_vectorize` | `image`, `out`, `k?`, `ss?` | `vectorize.py <img> <out> --k K --ss S` | 只有 `out` 的 SVG | SVG 路径、path 条数、MB、秒 |
| `art_paint` | `plan`, `out`, `ops?`, `gpu?` | `render_plan.py <plan> --out <png> [--ops] [--gpu]` | 只有 `out` 的 PNG；plan 只读 | PNG 路径、KB、秒 |
| `art_edit_brief` | `plan`, `instruction`, `out` | `compile_ops.py brief <plan> "<指令>" --thumb --out` | `out` 的 brief.json + 系统临时目录 `compile_thumb.png` | brief 路径、缩略图绝对路径、ops 模板 |
| `art_apply_ops` | `plan`, `ops`, `out`, `out_plan?`, `gpu?` | `paint_cli.py edit <plan> --ops <ops> --out <png> --fast [--save-plan]` | `out` 的 PNG；给了 `out_plan` 才落盘改后的 plan | PNG、KB、秒、`selected`/`dirty_rect`；给 `out_plan` 时另回 `plan`/`plan_strokes`/`plan_layers`、秒；`selected` / `dirty_rect` |

**四个工具共同的契约**（写在这里，因为参数表只写"进去什么"，写不下"出来什么"）：

- **plan 永远是只读输入**：没有一个工具会改写你给的 plan。要"改后的 plan"，要么 `art_apply_ops` 传 `out_plan`（写一份新文件），要么自己 materialize（`ops.apply(doc, specs, selection=None)` 再 `plan_io.dump_plan`）。
- **图与 plan 的对应关系靠你自己维持**：PNG 是产物，plan 是源；想复现就保留 plan + ops。
- **批量笔数变化不回报**：`densify` 之后真实笔数只能从落盘的 plan（`out_plan` 或 materialize）里数。
- 每个工具一个新进程跑脚本，所以**改 `scripts/*.py` 立即生效**；改插件的工具描述要重启 DSH。

**坑（都踩过）**

1. `art_vectorize` 的 `k`/`ss` 走命名参数；老版本 `vectorize.py` 只认位置参数，会抛 `ValueError: invalid literal for int() with base 10: '--k'`。本仓库两种写法都支持（`--k 96` 与 `96` 等价）。
2. `art_paint` 的输入**只有一个 plan**。没有 plan 就像没有剧本让演员即兴——先造 plan（第 4 节）。
3. `art_apply_ops` 一律带 `--fast`：流程会整幅重画或按脏矩形增量，**不加 `--fast` 手工调 CLI 时，凭空加笔画会得到一张 4 KB 空白图**（增量只重画原有笔触的脏矩形）。
4. 路径相对**调用方 cwd** 解析，工具内部会 `abs()`；相对路径写错时报错位置在 Python 栈里，看最后一个参数。

---

## 3. 选路：用户一句话 → 走哪条

| 用户说的 | 走这条 | 关键命令 |
|---|---|---|
| 转 SVG / 放大不模糊 / 要真矢量 | A | `auto.py ref.png out.svg` |
| 图标、logo、线稿 | A（低 K） | `vectorize.py in.png out.svg --k 64` |
| 画一张（有参考图，要像） | A 走完再 B | `auto.py` → 反推 plan |
| 画一张（凭空，没有参考） | B | 写 plan → `render_plan.py` |
| 改色 / 调光 / 细化 / 局部重画 | C | `compile_ops.py brief` → ops → `paint_cli.py edit` |
| 出 30 fps 过程视频 | D | `paint_cli.py video ... --frames N` |
| 验收："到底像不像" | 取证 | `metric.py ref.png render.png` |
| "跑不起来 / 环境对不对" | 自检 | `selfcheck.py` |

---

## 4. 画之前：plan.json 怎么造

引擎只执行 plan，不决定画什么。plan 是唯一事实来源，**改画面 = 改 plan 或往 plan 上叠 ops**。

### 4.1 顶层结构

```json
{
  "canvas": [960, 680],
  "layers": [{"name": "bed", "blend": "normal"}, {"name": "band", "blend": "screen"}],
  "strokes": [ { ...一笔... } ],
  "count": 12701,
  "regions": [],
  "subject_layers": []
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `canvas` | `[w, h]` **必须是数组** | 写成 `{"w":..,"h":..}` 会报 `invalid literal for int()` |
| `layers` | 数组，顺序 = 自下而上 | `name` 唯一，`blend` 见 4.3 |
| `strokes` | 数组 | 每个元素见 4.2；**顺序就是绘制顺序**，不要按 layer 排序 |
| `count` | int | 笔数，渲染时以 `strokes` 实际长度为准 |
| `regions` | 数组，可空 | 区域裁剪表；`region_id` 指向它，不填为 0 |
| `subject_layers` | 数组，可空 | 标明主体图层，供改画算子做保护 |

### 4.2 一笔长什么样

```json
{"layer": "band", "preset": "round_soft", "size": 46.0, "opacity": 0.18,
 "color": [250.0, 176.0, 112.0], "hardness": 0.25, "region_id": 0,
 "points": [[120.0, 340.5, 1.0], [480.0, 345.2, 1.0]]}
```

| 字段 | 类型 / 范围 | 说明 |
|---|---|---|
| `layer` | 已在 `layers` 里声明 | 拼错 = 静默不画 |
| `preset` | 10 个之一或 `tip:<名>` | 见 4.4 |
| `size` | 1–300 px | 笔头直径 |
| `opacity` | 0–1 | 单笔不透明度；叠 20 笔 0.1 比 1 笔 0.9 更柔 |
| `color` | `[r,g,b]` 0–255，可为浮点 | 不是 0–1，也不是 `#rrggbb` |
| `hardness` | 0–1 | 0 = 全软边（雾），1 = 硬边（剪影/平涂） |
| `region_id` | int | 有 `regions` 时才生效 |
| `points` | `[[x, y, pressure], ...]` | **pressure 必须在**；两点以上连成笔道 |

**硬规则**

1. `canvas` 是数组；`points` 是三元组。这两处错，报错信息都很绕。
2. 图层必须先声明后使用，顺序决定上下（后面的层压在前面上）。
3. 生成视频/过程时按**生成顺序**画；按 layer 分组会把不同层的笔交错，先画后画全乱。
4. 空画布 = alpha 0；渲染结果要和白纸合成（`alpha` 层叠到纸色上）才是"白底画"。
5. 笔数上万是常态：960×680 的落日约 1.2 万笔、2.4 MB。别手写，用循环生成。

### 4.3 图层与混合模式

| blend | 用途 | 例子 |
|---|---|---|
| `normal` | 铺底、主体、剪影 | 天空底色、山体、水面 |
| `screen` | 只加亮：光晕、水光、云缘 | 太阳外圈、水面碎光 |
| `multiply` | 只压暗：阴影、暗部叠色 | 云底、暗角 |
| `overlay` | 加对比：让受光处更亮、暗处更暗 | 整体质感 |
| `softlight` | 轻压/轻提，不破坏色相 | 四角暗角、氛围罩染 |

引擎另有 dodge / subtract / add / darken / lighten / colorburn / hardlight / vividlight / linearlight / pinlight / difference / exclusion / divide（`paint_native.BLENDS`）。画画常用前五个，其余留给实验。

**层序经验**：`bed`（不透明底）→ `band`（主体）→ `ambient`（screen 光晕）→ `highlight`（screen 细节光）→ `glaze`（softlight 暗角）。这个顺序保证：底永远盖满、光永远在形体之上、暗角最后收视线。

### 4.4 笔刷预设

| preset | 手感 | 用在哪 |
|---|---|---|
| `round_soft` | 软圆头，边缘羽化 | 天空渐变、云、体积光 |
| `round_hard` | 硬圆头，边界清楚 | 铺底、剪影、水面分界 |
| `flat` | 方头，一笔一块面 | 平涂、色块、建筑面 |
| `dry` | 干笔，飞白颗粒 | 粗糙肌理、土地、树干 |
| `chalk` | 粉笔，颗粒更细 | 雾面质感、墙面 |
| `bristle` | 鬃毛，带刷痕 | 油画厚涂笔触 |
| `sponge` | 海绵，随机点 | 树叶、石面、泡沫 |
| `airbrush` / `liner` / `marker` | 喷枪 / 细线 / 马克笔 | 雾、发丝、线稿 |
| `tip:<名>` | 278 个笔尖目录 | 本构建下渲染为平涂，**别指望出肌理** |

### 4.5 参数经验值（实测手感，不是硬约束）

| 目的 | size | opacity | hardness | 笔数级 |
|---|---|---|---|---|
| 铺满整幅不露底 | 40–90 | 0.9–1 | 0.3–0.9 | 20–40 大笔 |
| 天空渐变 | 40–100 | 0.1–0.25 | 0.2–0.4 | 400–900 |
| 太阳盘面 | 9–26 | 0.03–0.09 | 0.2–0.4 | 2000–4000 软点 |
| 云 | 14–32 | 0.04–0.09 | 0.2–0.4 | 每团 400–700 |
| 山/剪影 | 20–50 | 0.2–0.4 | 0.5–0.8 | 每层 500–1500 |
| 水面碎光 | 1.5–5 | 0.1–0.5 | 默认 | 500–1000 |
| 暗角 | 70–150 | 0.03–0.08 | 默认 | 150–250 |

### 4.6 凭空画的起手姿态（示例，可按题材改）

先立结构，再谈风格。下面是起手示例，不是唯一画法：

1. **先大笔铺底**（不露底）→ 2. **中笔立形体**（天空/山/水各自成形）→ 3. **小笔做细节**（碎光、云缘）→ 4. **screen 层加光、softlight 层压角**。
2. 每轮只改一类东西，渲一张看一眼；一次改十个参数，看不出是谁的作用。
3. 不要"先逐像素造参考图再采样"——那是脚本在画，不是引擎在画，形体改不动；而 plan 改一行就能重渲。

**从零造计划的代码骨架**（放在项目里跑，路径按自己的目录改）：

```python
import sys, math, random
sys.path.insert(0, r"<skill>\scripts")
import plan_io, render_util as RU

W, H = 1280, 960
strokes = []
def S(layer, pts, size, color, opacity, preset="round_soft", hardness=0.3):
    strokes.append({"layer": layer, "preset": preset, "size": float(size), "opacity": float(opacity),
                    "color": [float(c) for c in color], "hardness": hardness, "region_id": 0,
                    "points": [[float(x), float(y), 1.0] for x, y in pts]})   # pressure 必须在

S("bed", [(-40, H * 0.5), (W + 40, H * 0.5)], size=H * 3, color=[20, 24, 46], opacity=1.0,
  preset="round_hard", hardness=0.9)                                        # 先把底盖满
# ……天空/主体/光/细节各写一段循环，参考第 4.5 节的参数带……

blends = {"bed": "normal", "band": "normal", "glow": "screen", "vignette": "softlight"}
plan_io.dump_plan("my.plan.json", W, H, strokes, layer_blends=blends)
arr = RU.render_arr(plan_io.load_plan("my.plan.json")[0], gpu=False, nthreads=8)[..., :3]
print("strokes", len(strokes), "shape", arr.shape)
```

关键：**层名要先在 `blends` 里声明**，笔画按 `bed → 主体 → glow(screen) → vignette(softlight)` 的顺序生成；每一段都是独立循环，改哪段只影响哪段。

---

## 5. 调参手册（按阶段）

### 5.1 位图 → SVG

| 参数 | 作用 | 调法 |
|---|---|---|
| `K` | 颜色预算上限 | 交付 256–384；图标/线稿 64–128；不给默认 128 并打印提示 |
| `ss` | 超采样 2–3 | 边界台阶明显时开 3；代价约平方级 |
| `turd` | 丢弃小于 N 像素的色块 | 噪点多时 8–16 |
| `dil` | 掩膜膨胀像素 | 层间白缝时 1–2 |
| `blur` | 描前高斯模糊 | 锯齿重时 0.5–1.0 |
| `--seal` | 填细缝（需不透明底） | 透明图会跳过 |
| `--edge-soften` | 边缘柔化 | 0.15–0.6；与 `--alpha-soften`/seal 排队 |
| `--region` / `--elements` | 分区预算（脸高、背景低） | `--region "x0,y0,x1,y1:K[,SS]"` |
| `--tile` | 整幅分块 | `--tile 4x3:192` |

症状 → 参数：

| 症状 | 先看 | 改 |
|---|---|---|
| 大片色阶断层 | K 太小 | 加 K（+128）或开 `ss` |
| 层间白缝 | dil | `dil=1..2` 或 `--seal` |
| 边缘锯齿/台阶 | ss、blur | `ss=3`、`blur=0.6` |
| 小脏点 | turd | `turd=10` |
| 人脸/主体糊 | 区域预算 | `--elements "256:200:512"` 或 `--region` |
| 文件太大 | K / compact | 降 K；`--compact` |

迭代细化：`iterate.py ref.png out.svg --base "256 6 1.0 0 0 1 0"`（`--patch` 给模型写补丁；`--mode auto` 让脚本自己算）。凭空起稿用 `--blank 420x300`。

### 5.2 画（引擎渲染）

渲染本身没有参数可调（确定性、可复现）；**所有调节都在 plan 里**：层数、每层笔数、size/opacity/hardness、preset、blend、绘制顺序。

症状 → 改什么：

| 症状 | 原因 | 改法 |
|---|---|---|
| 画面发灰 | 软笔叠太多、低 opacity 铺满 | 底用 normal 高 opacity；光走 screen 层 |
| 露白/纸白颗粒 | 底层没盖满或 alpha 未合成 | 加大底笔 size/opacity，或渲染时合成纸色 |
| 太阳成环/烟花 | 从圆心往外拉线 | 改成圆盘内均匀软点（面积均匀） |
| 天空横带 | 横向笔同 y 同色反复叠 | 打散 y 抖动、换角度、加一笔大范围柔化 |
| 云像药丸 | 单条实心长笔 | 高斯点堆团块 + 靠光侧暖色边 |
| 水面竖条/灯芯绒 | 反光横向成排 | 反光柱加透视扩散、越远越散、压低不透明度 |
| 山像贴纸 | 硬边剪影 + 无空气透视 | 远山掺天空色、软笔收边、加雾层 |
| 笔触太碎 | size 太小、密度太高 | 放大 size、降笔数；先形体后细节 |
| 画面平 | 缺明暗层 | `split_shading` 拆层或补 multiply/screen 层 |

性能：480×320/359 笔 0.26 s；960×680/1.2 万笔 0.3 s 级；37k 笔 0.35 s。**同一 plan 每次逐像素一致**，可放心当回归基准。

### 5.3 改画（ops）

闭环四步：

```powershell
python scripts/compile_ops.py brief plan.json "把背景压深成墨绿夜色" --thumb --out brief.json
# 读 brief.json 的 thumb（缩略图）与 prompt，自己写 ops.json
python scripts/compile_ops.py validate plan.json --ops ops.json   # 多余键会被拒
python scripts/paint_cli.py edit plan.json --ops ops.json --out out.png --fast
```

20 个算子（详见 [API.md](API.md)）：

| 算子 | 改什么 | 关键键 |
|---|---|---|
| `set_layer_hue` / `_saturation` / `_brightness` | 整层色相 / 饱和 / 亮度 | layer, hue/sat/factor, guidance |
| `tone_map` | 曝光 / 提亮 / 对比曲线 | gain, lift, gamma |
| `set_light` | 主光角度与色温（全局均匀） | scale, tint |
| `light_rig` | 多光源：每盏灯可带方向/位置、颜色、强度、半径衰减、任意形状作用区域 | lights, ambient, mode, preserve |
| `relight_dir` | 按方向重算受光 | azimuth, elevation, strength |
| `split_shading` | 明暗拆层 | layer |
| `swap_brush` | 换笔刷预设 | layer, preset |
| `scale_stroke_size` | 笔触整体放大/缩小 | layer, k |
| `densify` | 加密笔触（加细节） | layer, k |
| `redraw` / `erase` | 选区内重画 / 擦除 | fraction, jitter |
| `recolor_palette` | 整套调色板替换 | palette |
| `draw_path` | 按 SVG path 加笔画（凭空起手；层不存在会自动建层） | layer, d, color, size, blend |
| `add_layer` | 新增空图层（幂等；`index` 指定插入位置，0=垫底） | name, blend, index |
| `remove_layer` / `rename_layer` / `move_layer` | 删层（连笔触） / 改名（连笔触） / 重排合成顺序 | name, to / index |
| `replan_region` | 按参考图重排某区域 | ref |

顺序约束：`split_shading` 必须在 `relight_dir` 之前（否则 `ValueError: 笔触缺少 albedo/shade`）。
`guidance` 0–1 控制强度；`selection` 用 `--mask-file` / `--rect` / `--hard-mask` 限定作用范围。
`draw_path` 一条 path = 一笔：要一片山就发上千条短笔，不要发十几条粗线。
`add_layer` 幂等：同名层已存在时什么都不做；`index` 不给 = 追加到最上，`index: 0` = 垫底（合成顺序 = 层顺序）。
多光源写法（`split_shading` 必须先跑；`preserve` 默认 strokes 保持整体亮度，`none` 才让画面整体变亮/变暗）：

```json
[{"op":"split_shading"},
 {"op":"light_rig","ambient":0.85,"lights":[
   {"type":"directional","azimuth":200,"elevation":15,"strength":1.1,"color":"#ffb070"},
   {"type":"radial","at":[900,300],"strength":0.9,"color":"#7fb4ff","radius":420,
    "shape":{"ellipse":[900,300,520,380],"feather":60}}]}]
```

贴图式光照（gobo）：`map` 是一张要「投影」的图。`map_mode:"luma"` 用亮度当光强（窗口格栅、树影），
`rgb` 连颜色一起投（彩色窗花），`alpha` 用透明通道当光强。`map_rect` 决定投到画布哪一块。
任意形状的遮挡/光池还可以把 `shape` 写成 `{"mask_file":"path.png"}` 或直接给图片路径。
`map` 与 `shape.mask_file` 可以写相对路径，CLI 会按 **plan 文件所在目录** 解析（`ops.resolve_asset_paths`）；跨目录调用时写绝对路径最稳。
投影的分辨率上限 = 笔触粒度（每笔一个颜色）：要更细的图案，就把长笔触拆短，别指望一条粗笔刷出窗格。

```json
[{"op":"split_shading"},
 {"op":"light_rig","ambient":0.2,"preserve":"none","lights":[
   {"type":"directional","azimuth":200,"elevation":25,"strength":2.6,
    "map":"textures/rose_window.png","map_mode":"rgb","map_rect":[80,20,400,300]}]}]
```
层管理三件套：`remove_layer`（层与笔触一起删）、`rename_layer`（层与笔触一起改，目标名已存在会报错）、`move_layer`（只改顺序）。

法线模型（`relight_dir` 与 `light_rig` 共用，见 `scripts/ops.py`）：`dome` 把选区当椭球，法线由笔触质心相对中心的偏移解析算出（纯 plan 坐标，不碰像素）；`gradient` 把 shade 画到 bbox 栅格上求梯度，再按笔触质心采样（内部有栅格中间量，但结果写回笔触参数）。两者都只是「图像平面伪法线」，不是 3D 法线贴图。公共 helper：`_dome_frame` / `_dome_normal` / `_normal_fields` / `_normal_sample`；`pick_instance.fit_boundary` 里的法线是轮廓边缘法线，与光照无关。

**一个必须知道的隐藏作用域（`paint_cli.py edit`，即 `art_apply_ops` 走的路径）**

一次 edit 里如果同时有「带 `layer` 的算子」和「不带 layer 的全局算子」（如 `tone_map`、`erase`、`redraw`），
CLI 会把**所有**算子的 selection 限制在「带 layer 那些算子提到的层集合」上（`paint_cli.py` 的 `cmd_edit`：
先 `touched = {带 layer 的算子提到的层}`，再对每个层造 weights 合成一个 selection 传给 `ops.apply`）。
也就是说：你以为的"全局 tone_map"只作用在那几层上。

实测（同一 plan、同一组 ops，25k 笔夜景）：`ops.apply(selection=None)` 直算 luma 52.894；
走 `paint_cli.py edit` 的选择 luma 49.555；1,167,357 / 1,228,800 像素不同（约 95.0%），max_delta=14。

两种规避，按需选：

1. **全局算子单独跑一次**：先执行只有全局算子的 ops，再执行层算子；两次都带 `--fast`。
2. **要"最终 plan"时绕过 CLI**：用 `ops.apply(doc, specs, selection=None)` 直接 materialize 成新计划，再用
   `render_plan.py` 渲染——这样 PNG 与 plan 一一对应，笔数也拿得到：

```python
import json, sys
sys.path.insert(0, r"<skill>\scripts")
import plan_io, ops
doc, w, h = plan_io.load_plan("refined.plan.json")
specs = json.load(open("refine_ops.json", encoding="utf-8"))
out = ops.apply(doc, specs, selection=None)          # 真·全局
plan_io.dump_plan("final.plan.json", w, h, out["strokes"],
                  layer_blends={l["name"]: l["blend"] for l in out["layers"]})
print("final strokes", len(out["strokes"]))
```

`art_apply_ops` 的行为：默认只返回 `selected` / `dirty_rect`（不返回 densify 后的总笔数），也**不落盘** plan；
传 `out_plan` 才把应用 ops 后的 plan 写到新文件，并回 `plan_strokes` / `plan_layers`。
带新层的 edit（`add_layer`，或 `draw_path` 指向新层）会自动改走整幅渲染——增量渲染没有新层的下标——返回里多一个 `new_layers`。
视频同理：`paint_video.py` 检测到层集合变化（加/删/改名/重排）时，在编辑帧换一个按编辑后计划建的 painter 整幅重画；末帧自检也改成对编辑后的计划做参考。
`art_edit_brief` 的缩略图固定写在系统临时目录的 `compile_thumb.png`，不受 `out` 控制；要归档就自己挪走。

### 5.4 视频

```powershell
python scripts/paint_cli.py video plan.json --ops ops.json --out out.mp4 --frames 600 --fps 30
python scripts/paint_video.py plan.json --edit-at 180 --groups 2 --out out.mp4   # 画到一半再施改画
```

| 参数 | 作用 |
|---|---|
| `--frames` / `--fps` | 总帧数 / 帧率（600 帧 @30 = 20 s） |
| `--groups` | 把改画分批到第几段 |
| `--edit-at` | 第几帧开始改画 |
| `--draw-until` | 画到第几帧停 |

实测：**增量渲染** 600 帧 20 s 视频 7.3 s；同一计划逐帧重渲 94.1 s（12.7× 差距）。h264 `-crf 20 -preset veryfast` 后 0.28–0.50 MB。

---

## 6. 验收与取证

交付时保留三样东西：`plan`（源）、由它渲染的 `png`、`ops`（细化过才有）——三者能复现同一张图；其余产物（过程图、视频、分层导出）随意附加。

```powershell
python scripts/metric.py ref.png render.png
# {"mean": 1.1554, "pct_le3": .., "pct_le5": .., "pct_le10": .., "pct_le20": .., "uncovered": 0, "pixels": N}
```

| 字段 | 含义 | 判读 |
|---|---|---|
| `mean` | 每像素平均色差 | < 2 算像；> 5 要查 K/区域 |
| `pct_le3/5/10/20` | 误差在阈值内的像素占比 | 看 5/10 两列，别只看 mean |
| `uncovered` | 没被任何 path 覆盖的像素 | 必须 0；非 0 = 漏色块 |
| `pixels` | 统计像素数 | 两份图尺寸必须一致 |

原则：**数字优先于眼睛**。每条结论带命令和数字；眼睛只用来发现"数字没覆盖的东西"（构图、笔触走向）。

### 6.1 没有参考图时怎么验收（凭空画专用）

凭空画没有 `ref.png`，`metric.py` 用不了。下面是一张**验收表示例**——数字和条目按题材改，不是固定门槛。

| 判据 | 怎么看 | 示例合格线 |
|---|---|---|
| 确定性 | 同一 plan 渲染两次，逐像素比较 | `maxdiff = 0` |
| 计划合法 | `plan_io.py check` / `compile_ops.py validate` | 无非法 preset、无悬空 layer |
| 不露底 | 合成图 alpha 最小值 / alpha=0 占比 | 无 alpha=0 区域 |
| 可复现 | 重新 materialize + 渲染 | 与交付 PNG 逐像素一致 |
| 改动生效 | 细化前后被选中/改变的像素数 | `selected > 0` |
| 规模（示例） | `len(plan["strokes"])`、层数 | 上千笔、至少 4–5 层；十几笔一条粗线 = 贴纸 |
| 分层（示例） | 层名与 blend | 有底（normal）、有光（screen）、有调色/暗角（softlight/multiply） |

“规模/分层”只是示例：单层厚涂也可以成立，二十层也可能空；别把数字当合格线。

再读一次**自查图**：把最终 PNG 读进来，问三件事——(a) 主体轮廓是否成立；(b) 最亮处是否只有一个视觉焦点；(c) 缩到 25% 是否还读得懂构图。这三条是语义判断，不能用层数/笔数代替。

### 6.2 凭空画的交付清单（示例）

交付时保留四样（缺一样都不算完整）：

| 交付物 | 要求 |
|---|---|
| `*.plan.json` | 唯一事实来源；层名都在 `layers` 里声明；每笔 `points` 是三元组 |
| `*.png` | 由该 plan 渲染；重渲一次逐像素差为 0 |
| `*.ops.json` | 细化用过的算子，`compile_ops.py validate` 通过；没有细化可省略 |
| 一行统计 | canvas / strokes / layers / presets / 渲染秒 / 字节数 / 复渲差 |

报告模板（数字换成你的，不要用形容词代替）：

```json
{"canvas": [1200, 900], "strokes": "<N>", "layers": "<N>", "presets": ["..."],
 "render_s": "<S>", "bytes": "<B>", "rerender_maxdiff": 0, "refine_ops": "<N>"}
```

### 6.3 标准流程（示例，从一句需求到交付）

这不是唯一画法，是按题材可改的起手示例：

1. **拆题**：把画面拆成层（底 / 主体 / 光 / 细节 / 调色），每层写清“它负责什么现象”。
2. **写计划**：每层用独立循环生成笔画，先大笔铺底，再中笔立形体，最后小笔做细节（§4.5 的参数带）。
3. **渲一张自查**：露底？发灰？主体读得懂？——按 §6.1 的清单逐条过。
4. **细化**：`densify` 加细节、层算子调色、必要时局部 `redraw`；每轮只改一类，改完重渲一次。
5. **交付**：按 §6.2 出四样，附一行统计。

### 6.4 接口与回路（这不是流程）

这套东西**不规定画法**。它只给你一组接口，怎么串由你定。真正要记的只有两件事。

**接口（彼此正交，可单独调用）**

| 接口 | 形态 | 给什么 | 得到什么 |
|---|---|---|---|
| 计划 | JSON：`canvas` / `layers` / `strokes` | 笔画数据 | 可渲染、可 diff 的唯一事实来源 |
| 渲染 | `render_plan.py` / `art_paint` | plan（+ 可选 ops） | PNG；同一 plan 逐像素一致 |
| 算子 | ops JSON（20 个） | 一组算子 | 改后的 plan，或直接渲染成 PNG |
| 编译 | `compile_ops.py brief` | plan + 一句话指令 | 缩略图 + 提示词（ops 由你写） |
| 校验 | `compile_ops.py validate` / `plan_io.py check` / `selfcheck.py` | plan / ops / 环境 | `ok`，或具体错误 |
| 度量 | `metric.py`（有参考图）；§6.1（无参考图） | 两张图，或一张图 + 判据 | 一组可比较的数字 |

**回路**

```
改一个东西  ->  渲染  ->  按 §6.1 量判据  ->  通过就停；不通过回到第一步
```

- 一轮只动一个变量（一层、一个参数、一组算子），否则说不清是谁在起作用。
- 判据按 §6.1 的验收表示例；有参考图时用 `metric.py`。
- 硬的是**接口契约**（层必须先声明、`points` 是三元组、同 plan 必同图）；软的是**你的画法**，随便组合。

> 别把这份文档当剧本：它给的是接口和判据。想先铺底再细节，还是先大块再修形，由你按题材决定。

---

## 7. 症状 → 参数 速查（总表）

| 症状 | 阶段 | 第一个该改的参数 |
|---|---|---|
| SVG 色阶断层 | A | `K` ↑ |
| SVG 白缝 | A | `dil` ↑ 或 `--seal` |
| SVG 边缘台阶 | A | `ss` ↑ |
| 渲染图露白 | B | 底笔 size/opacity ↑ |
| 画面发灰 | B | 光走 screen 层，底用 normal |
| 太阳成环 | B | 盘面软点，别拉线 |
| 天空横带 | B | y 抖动 / 换角度 / 柔化大笔 |
| 改画没反应 | C | `--fast`；`validate` 看算子是否被拒 |
| 改画整幅变 | C | `selection`（mask/rect）缩范围，`guidance` ↓ |
| 视频慢 | D | 用增量渲染；别逐帧重渲 |
| 视频顺序乱 | D | 按生成顺序，别按 layer 排序 |
| 工具报参数错 | 入口 | 看第 2 节「坑」 |

---

## 8. 边界与已知问题

- **绘画引擎是 Windows x64**（`scripts/native/paint_core.dll`）。Linux/macOS 只有 SVG 半边可用。
- `tip:*` 笔尖在当前构建渲染为平涂，别指望肌理；要肌理用 `bristle`/`dry`/`chalk`。
- 不是照片级写实：这是"厚涂油画"引擎，追求笔触与色块，不追求逐像素照片。
- `requirements.txt` / `install.ps1` 属于 DSH 本地安装，不是 skill 内容；skill 只保证脚本自洽。
- `__pycache__` / `*.pyc` 是运行时缓存（自动重建），已在 `.gitignore`，不要入库。
- 文档是中文；脚本输出是 ASCII JSON（任何 locale 都能读）。
