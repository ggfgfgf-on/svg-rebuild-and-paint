# 接口清单（自动生成，勿手改）

> 由 `scripts/api_doc.py` 从代码生成；改了接口就重跑，否则 `tests/test_api_doc.py` 会失败。语义（每个源该在什么场景用、代价多少）在 SKILL.md 的「选择源」一节，这里只保证签名不漂移。

## 选择源语义（由 ops.SOURCES 生成，与闸门同源）

| 来源 | coarse | 像素级 | 实测（tile_px→IoU） | 说明 |
|---|---|---|---|---|
| mask | 否 | 是 | - | 内联掩膜；像素级 |
| mask_file | 否 | 是 | - | 掩膜文件；像素级 |
| all | 否 | 否 | - | 全图 |
| polygon | 否 | 是 | - | 多边形；边界由顶点决定 |
| instance | 否 | 否 | - | 实例分割；近似边界 |
| tiles | 否 | 否 | - | 格子 + 颜色判别器（默认 grabcut）定边 |
| cells | 是 | 否 | 8px→0.968、11px→0.954、16px→0.930、24px→0.898、32px→0.864、48px→0.840、55px→0.813；refine(从格子)→0.862 | 模型报的格子并集（块状）；只回答「哪一片」，必须 refine 或 zoom 才能上色 |
| prompt | 否 | 否 | - | 点/框 → grabcut；近似边界 |
| segment | 否 | 否 | - | 文本提示分割；近似边界 |
| include | 否 | 否 | - | 组合：include & ~exclude |
| exclude | 否 | 否 | - | 组合：include & ~exclude |

**闸门**：coarse=是 的来源直接给改色类算子会抛错（整笔改色会越界）；放行方式是 refine: true 或 allow_coarse: true。约束在 ops.check_source，本表由 ops.describe_sources() 渲染，所以不可能只改行为不改表。

## 选择源（selection sources）

`resolve_mask` 认识这些键；`ops.apply` 只解析 `ops._SEL_KEYS` 里登记过的来源——**新增来源要同时改两处**，否则算子静默作用到全图。

```
mask, mask_file, all, polygon, instance, tiles, cells, prompt, segment, include, exclude
```

## 通用键（所有算子都接受）

结构类：`all`, `allow_coarse`, `attach_thin`, `cells`, `edge_min_contrast`, `edge_ok`, `exclude`, `filter_halo`, `fit_boundary`, `grow_px`, `include`, `instance`, `keep_model`, `loop`, `mask`, `mask_file`, `mask_obj`, `min_ratio`, `model_grow_px`, `polygon`, `prompt`, `refine`, `search_px`, `seed`, `segment`, `sel_box`, `sel_hue`, `sel_mode`, `sel_sat_min`, `sel_val_min`, `select_tag`, `selection`, `steps`, `tiles`, `weights`

执行类：`selection`, `weights`, `guidance`, `seed`, `mask_obj`, `sel_mode`

`sel_mode`：any（默认，笔触任一点碰到选区即整笔生效）、center（中心点在选区内）、all（整笔在选区内，最不溢出）。改色类算子建议 center 或 all。

### `densify`(doc, layer, k, guidance=1.0, selection=None, weights=None, seed=0, **_)

在选中笔触旁追加子笔触（k>1；k=1 逐字节不变）。

- 必填：`layer`, `k`
- 可选：`guidance`

### `draw_path`(doc, layer, d, color=(217, 180, 90), size=6.0, opacity=1.0, preset='round_soft', guidance=1.0, tag=None, selection=None, weights=None, seed=0, **_)

从零加笔：把 SVG path 展平成笔触，追加到指定层（不依赖参考图）。

- 必填：`layer`, `d`
- 可选：`color`, `guidance`, `opacity`, `preset`, `size`, `tag`

### `erase`(doc, layer=None, guidance=1.0, selection=None, weights=None, seed=0, **_)

删除笔触（强度即命中概率）。selection=None 且 guidance=1 等于清掉整层。

- 必填：（无）
- 可选：`guidance`, `layer`

### `recolor_palette`(doc, palette, guidance=1.0, selection=None, weights=None, seed=0, **_)

把颜色吸附到给定色板（取最近的，再按强度插值）。

- 必填：`palette`
- 可选：`guidance`

### `redraw`(doc, fraction=1.0, jitter=0.15, layer=None, guidance=1.0, order='random', selection=None, weights=None, seed=0, **_)

重抽一部分笔触的参数（SDEdit 类比：改多少由 fraction 决定）。

- 必填：（无）
- 可选：`fraction`, `guidance`, `jitter`, `layer`, `order`

### `relight_dir`(doc, azimuth=315.0, elevation=40.0, strength=0.8, blur=0, mode='dome', layer=None, guidance=1.0, selection=None, weights=None, seed=0, target='strokes', render_iters=8, render_tol=0.01, **_)

方向光：模型看图说"光从左上/右后来"，这个算子把方位角变成确定性的 shade 重算。

- 必填：（无）
- 可选：`azimuth`, `blur`, `elevation`, `guidance`, `layer`, `mode`, `render_iters`, `render_tol`, `strength`, `target`

### `replan_region`(doc, ref, regions=None, layers=('band', 'highlight'), mode='cover', thr=0.9, ring='auto', highlight_mode='keep', shadow_mode='drop', style_file=None, gpu=False, mask_obj=None, **_)

区域重规划算子：用参考图把区域内的笔触重画一遍（区域外原样保留）。

- 必填：`ref`
- 可选：`gpu`, `highlight_mode`, `layers`, `mode`, `regions`, `ring`, `shadow_mode`, `style_file`, `thr`

### `scale_stroke_size`(doc, layer, k, guidance=1.0, selection=None, weights=None, seed=0, **_)

笔触尺寸乘 k（k=1 逐字节不变）。

- 必填：`layer`, `k`
- 可选：`guidance`

### `set_layer_brightness`(doc, layer, factor, guidance=1.0, selection=None, weights=None, seed=0, **_)

明度乘因子（factor=1 不变，guidance=1 时精确乘 factor）。

- 必填：`layer`, `factor`
- 可选：`guidance`

### `set_layer_hue`(doc, layer, hue, guidance=1.0, selection=None, weights=None, seed=0, **_)

色相旋转到目标角度（沿最短弧插值，guidance=1 精确命中）。

- 必填：`layer`, `hue`
- 可选：`guidance`

### `set_layer_saturation`(doc, layer, sat, guidance=1.0, selection=None, weights=None, seed=0, **_)

饱和度插值到绝对值（0..1）。

- 必填：`layer`, `sat`
- 可选：`guidance`

### `set_light`(doc, scale=1.0, tint=None, layer=None, guidance=1.0, selection=None, weights=None, seed=0, **_)

只改光照：color = albedo × shade × scale × tint（需要先 split_shading）。

- 必填：（无）
- 可选：`guidance`, `layer`, `scale`, `tint`

### `split_shading`(doc, layer=None, guidance=1.0, selection=None, weights=None, seed=0, **_)

把笔触颜色拆成 albedo（材质反照率）+ shade（受光量），存在笔触字段里。

- 必填：（无）
- 可选：`guidance`, `layer`

### `swap_brush`(doc, layer, preset, guidance=1.0, selection=None, weights=None, seed=0, **_)

按强度逐笔替换笔刷 preset（离散步：强度即命中概率，稳定哈希决定）。

- 必填：`layer`, `preset`
- 可选：`guidance`

### `tone_map`(doc, gain=1.0, lift=0.0, gamma=1.0, layer=None, guidance=1.0, selection=None, weights=None, seed=0, **_)

色调映射：c' = 255 * clamp(((c/255) ** (1/gamma)) * gain + lift)。

- 必填：（无）
- 可选：`gain`, `gamma`, `guidance`, `layer`, `lift`

## `pick_instance`（辅助函数）

### `added_edge_report`(image, base_mask, added_mask, min_ratio=0.8, blur=3, anchors=None, anchor_px=6)

复查 attach_thin 之类"并进来的像素"到底是真结构还是晕边。

### `attach_thin`(image, mask, gap_px=6, max_width_px=6, max_len_px=90, hue_tol=16.0, s_min=0.06, v_min=0.02, max_area_px=5000)

把"细长、低饱和、贴着主体"的暗结构并回掩膜（叶尖/叶柄/细颈）。

### `calibrated`(name, default=None)

读 scripts/calibrated.json 里的标定值（由 calibrate.py 生成、模型选定）。

### `candidate_sheet`(image, cand, out_sheet, roi=None, cols=3)

把候选轮廓画在局部放大图上，模型一眼挑。

### `candidates`(image, roi=None, k=8, min_area=0.004, max_area=0.35, max_out=6, __unused=None)

算法提候选、模型来挑：颜色聚类 + 连通域，输出编号叠图。

### `cell_box`(roi, cols, rows, label)

格子编号 -> 像素框 (x0,y0,x1,y1)，浮点、已按 roi 截断。

### `cell_center`(roi, cols, rows, label)

### `cell_geometry`(roi, cols, rows)

### `cells_anchors`(image, windows)

报的格子中心点（= 锚点）：供 loop 的锚点铁律使用。

### `cells_for_box`(roi, cols, rows, box)

一个像素框覆盖到哪些格子（用于"把这些格子 zoom 出来"）。

### `cells_mask`(image, windows, dilate_px=0, near=None, near_px=0, use_neg=True, neg_attach_px=0)

把模型报的格子**直接**栅格化成掩膜：模型说属于就属于，不经过任何颜色判别器。

### `child_window`(image, parent, cells, sub=3, tile_px=None, wid=None, out=None, scale=None)

对指定格子做下一层 zoom：父窗口 + 若干格子 -> 子窗口（自带 roi/网格/编号）。

### `edge_report`(image, mask, search_px=6, blur=3, min_contrast=0.05)

自动判定"边界是否正好落在边缘上"（不依赖任何人工描边）。

### `enforce_anchors`(image, mask, anchors, radius=40, cap=12000, k=3.0)

模型明确报过的格子，绝不允许从掩膜里消失。

### `filter_added`(image, base_mask, added_mask, min_ratio=0.8, anchors=None, keep_model=True, model_grow_px=None)

按梯度证据给"并进来的像素"分三类：edge（自带边）/ model（模型报过，梯度弱也算）/ halo（只有一圈糊边）。 默认丢掉 halo，保留 edge 与 model——这就是"模型参与 + 可验证证据"的分工。

### `fit_boundary`(image, mask, search_px=6, blur=3, verbose=False)

把整圈边界"贴"到最强梯度上：全局平移 d ∈ [-search, +search]，取边缘梯度均值最大者。

### `grid_overlay`(image, box, out, step=50, scale=2)

带坐标网格的放大裁剪：模型据此读数描边，不再靠目测猜坐标。

### `hints`(v, spec)

把验证结论翻译成"下一轮改什么"，让模型能自己纠错而不是反复瞎试。

### `main`()

### `mask_implausible`(bits, windows, grow=8.0, fill_max=0.95)

提示与图像不符的早期警报：整片叶子不会占满全图，也不会面积暴涨。

### `pick`(image, specs, out_dir, attempt_log=None, attempt=1)

按模型给的种子点逐个长掩膜并验证；返回报告（含局部放大图路径供模型复核）。

### `prompted_from_tiles`(image, windows, strict=True, **kw)

模型给格子编号（可多级 zoom）→ 点 → prompted_mask。坐标换算全在代码里。

### `prompted_mask`(image, positives, negatives, ring=16, iters=4, radius=10, model='grabcut', components=3, bins=32, smooth_bins=2, bridge_px=11, hard_negatives=False)

模型给正/负点提示，确定性函数出掩膜（唯一的"阈值"是似然比 0.5，不是拟合常量）。

### `px_to_cell`(roi, cols, rows, x, y)

像素坐标 -> 格子编号（cell_center 的逆运算，用于复核）。

### `rasterize`(mask, W, H)

把任意类型掩膜（raster/polygon/rect/point/all）铺成整图布尔图，便于统一验证。

### `refine_loop`(image, windows, iters=6, tol=0.995, model='grabcut', ring=14, verbose=True, init_mask=None, anchors=None, neg_px=3, neg_k=2.5, neg_max=400, max_grow_px=2, max_total_grow_px=None)

自动闭环：靠栅格提示起手，然后把已得掩膜的"确信内部/外圈"当作新提示反复精修。

### `refine_mask`(image, mask, corridor=12, iters=5, open_px=0)

走廊约束的 GrabCut 掩膜精修：只在原掩膜 ±corridor 内重判前景/背景。

### `refine_polygon`(image, polygon, iters=6, seed_band=9, out_band=0)

模型画的多边形 + grabCut 边界吸附：不需要分割模型，也能把粗描边贴到真实边缘。

### `segment`(image, roi=None, seeds=None, ring=18, iters=5, hue_k=3.0, sat_q=15, chroma_scale=0.55, seed_radius=14, passes=2)

自动区域分割：按"色度 Otsu + 同色彩带"切出主体，再走廊精修。

### `snap_polygon`(image, poly, search=18, smooth=2, step=2.0)

把手画的粗轮廓吸到图像边缘上：每个采样点沿法线方向在 ±search 内找最强梯度。

### `survey`(image, out, cols=4, rows=4)

切块并标注每块像素范围：模型据此报"第几行第几列、偏左偏右"，再换算成坐标。

### `tile_grid`(image, roi, cols=None, rows=None, tile_px=None, out=None, scale=None, wid=None)

带编号格子的放大图。`tile_px` 按像素给格子边长（1=单像素）。

### `tile_points`(image, windows)

多级/多次 zoom：每级窗口自己的 roi + 细度，正负编号分别换算成照片坐标点。

### `verify`(bits, min_area=0.002, max_area=0.35, max_components=1, border_tol=0.1, allow_border=False, box=None, box_fill_max=0.9)

客观验证：一个"实例"应该面积适中、只有一个连通块（<2% 的碎片已忽略）、不贴边。

### `verify_mask`(mask, W, H, **kw)

直接验证模型画的掩膜（多边形也算）：面积/连通块/贴边/填充率。

## `masks`（辅助函数）

### `all_`()

### `bbox`(doc, selection)

选中笔触的脏矩形（含笔刷半径）。空选择 → None。

### `color`(rgb, tol=40.0)

### `from_bool`(b, x0=0, y0=0)

像素级布尔图 -> raster 掩膜（任意形状，不再退化成包围盒）。

### `from_image`(image, color, tol=40, keep_largest=True, pad=0)

像素级颜色连通域 -> 计划空间矩形掩膜（取最大连通块的包围盒）。

### `from_json`(obj)

### `point`(x, y, r)

### `point_weight`(mask, pts, radius=0.0, feather=0.0)

### `poly_weight`(mask, pts, feather=0.0)

### `polygon`(points)

### `raster_weight`(mask, pts, r=0.0, feather=0.0)

### `rect`(x, y, w, h)

### `rect_weight`(mask, pts, radius=0.0, feather=0.0)

### `select`(doc, mask, layer=None, mode='any')

### `stroke_bbox`(stroke)

### `stroke_radius`(stroke)

### `stroke_weight`(mask, stroke, feather=0.0, mode='any')

单笔相对掩膜的权重。

### `to_json`(mask)

### `weights`(doc, mask, feather=0.0, layer=None, mode='any')

返回 (selection, weights)：全局笔触下标 + 对应 0..1 权重。mode 见 stroke_weight。
