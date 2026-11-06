# 结构度量与多候选选择

这一页解决的是“结构门只靠眼睛看”的问题。它是**量尺**，不是画法：脚本不生成任何结构，也不规定身体部件怎么画。

## 1. 单个候选：structure_metric.py

~~~powershell
# 有结构参考：比较主体剪影的形状、比例、位置
python scripts/structure_metric.py out/cand.plan.json --layers stag --ref ref.plan.json --ref-layers stag,stag_detail --out out/cand.structure.json

# 没有参考图：用归一化 landmark 表 + 候选里的 landmark 标签
python scripts/structure_metric.py out/cand.plan.json --layers stag --spec scripts/structure_specs/stag.json --guide out/structure_guide.png --out out/cand.structure.json
~~~

输出包括：

- 主体 mask 的 bbox / 宽高比 / 面积 / 填充率 / 质心 / 底边；
- 有参考时：按 bbox 归一化后的 **shape_iou**、shape_dice、shape_chamfer，以及位置/面积比；
- 有 spec + landmark 时：每个 landmark 的 bbox 归一化误差、最大误差；
- spec 还可给 segments（head / body / front_leg / hind_leg / stance）和 ground_y，输出归一化段长误差、接地误差；
- 所有指标只给数值和各自容差，不把审美写成硬门槛。

注意：**shape_iou** 是归一化形状比较，先消除位置/大小；位置和大小看 placement。不要拿原始像素 IoU 判结构。

## 1.1 --ref 的语义

--ref 是**可选的参考 plan**，不是渲染图、不是绘制模板，也不是硬编码结构。

- 取参考 plan 的主体层 alpha 作为剪影；
- 候选和参考各自按主体 bbox 归一化，再算 shape_iou / dice / chamfer；
- 位置和大小会被消掉；shape_iou 只看归一化轮廓：
  - 1.0 = 归一化轮廓相同；
  - 越高越接近参考；
  - shape_chamfer_px 越低越好；
  - 位置/大小另看 placement。
- 如果要对多个候选做形状比较，可以给它们传同一份 --ref；这样 shape_iou 在同一参考下可比。是否使用由调用方决定。
- 完成门后也可以再对 final 跑同一份 --ref，观察 shape_iou 是否变化；这是可选的复查，不是强制步骤。
- 没有 --ref 时，spec 只验 landmark / segment / 接地；它不提供轮廓判断。spec pass 不等于结构 pass。

### 1.2 CLI 参数

| 参数 | 默认 | 说明 |
|---|---|---|
| plan | 必填 | 候选 plan |
| --layers | plan.subject_layers | 逗号分隔的主体层；没有 subject_layers 时必须给 |
| --ref | 无 | 可选参考 plan；提供时输出 comparison |
| --ref-layers | 同 --layers | 参考 plan 的主体层 |
| --spec | 无 | 归一化 shape/landmark/segment/ground 尺子 |
| --landmarks | 无 | 候选 landmark JSON；不给则从 landmark: 标签取 |
| --alpha-thresh | 8 | alpha 大于该值算主体像素 |
| --out | 无 | 写完整 JSON |
| --guide | 无 | 写结构参考图；需要 --spec |

## 2. 多候选：structure_candidates.py

structure_candidates.py 接收多个结构-only 候选 plan（只画剪影、接地、遮挡，不画毛/鹿角/高光）。候选由调用方生成，可以来自同一个 prompt：

~~~powershell
python scripts/structure_candidates.py out/cand_*.plan.json --layers stag --ref ref.plan.json --ref-layers stag,stag_detail --thumb 320 --cols 3 --sheet out/structure_candidates.png --out out/structure_ranking.json
~~~

有参考时按 shape_iou 排序并标出 best；没有参考但有 spec 时先过 spec 的 shape/landmark gate，再按 landmark 最大误差排序，所有候选都不过时 best=null；两者都没有时 best=null，只生成联系表，不假装自动排名。

### 2.1 CLI 参数

| 参数 | 默认 | 说明 |
|---|---|---|
| plans | 必填 | 一个或多个候选 plan |
| --layers | 无，必填 | 候选主体层，逗号分隔 |
| --ref | 无 | 可选参考 plan；提供时按 shape_iou 排序 |
| --ref-layers | 同 --layers | 参考主体层 |
| --spec | 无 | 用 spec landmark/segment 排序 |
| --thumb | 320 | 每张缩略图宽度 |
| --cols | 3 | 联系表列数 |
| --sheet | 无 | 写联系表 PNG |
| --out | 无 | 写完整 ranking JSON |


输出是排序和联系表；是否选择、选哪个，由调用方决定。

## 3. landmark 标签

候选 plan 里用普通笔触的 tag 标关键点：

~~~json
{"layer": "stag", "preset": "round_hard", "size": 2, "opacity": 1,
 "color": [0, 0, 0], "hardness": 1, "tag": "landmark:nose",
 "points": [[550, 390, 1.0]]}
~~~

plan_io.dump_plan 会保留 tag；structure_metric.py 默认从 landmark:<name> 标签取点。也可以 --landmarks lm.json 传：

~~~json
{"nose": [550, 390], "poll": [520, 360], "front_hoof": [560, 780]}
~~~

spec 是归一化尺子：landmarks_bbox 的坐标相对主体 bbox，0..1；segments 给关键点之间的距离，ground_y 给接地线。它不是要照抄的几何模板。scripts/structure_specs/stag.json 只是一个示例，换题材时换 spec。

### 3.1 spec 字段

| 字段 | 说明 |
|---|---|
| bbox_aspect | 目标主体宽高比；给定时参与 shape gate |
| fill_ratio | 目标主体填充率（面积 / bbox 面积）；给定时参与 shape gate |
| subject_bbox | 归一化画布 bbox；只用于 --guide 的示意框 |
| ground_y | 归一化接地线；参与 ground 检查 |
| landmarks_bbox | landmark 目标点，坐标相对主体 bbox，0..1 |
| segments | 关键点对（head/body/front_leg/hind_leg/stance）；按 bbox 归一化长度比较 |
| tolerances.bbox_aspect | 宽高比绝对容差；例如 0.12 |
| tolerances.fill_ratio | 填充率绝对容差；例如 0.05 |
| tolerances.landmark | landmark 最大误差容差，默认 0.06 |
| tolerances.segment | 段长误差容差，默认 0.10 |
| tolerances.ground | 接地线误差容差，默认 0.03 |

未列出的字段不会被执行；bbox_aspect/fill_ratio 只有给了目标和容差才参与 gate，不是硬编码结构。spec 是尺子，不是绘制模板。


## 4. 一种组合方式（不是要求）

1. 可生成多个结构候选；
2. 用 structure_candidates.py 渲最小图并输出排序/联系表；
3. 由调用方选择候选（有 ref/spec 看指标，没有就人工/视觉看联系表）；
4. 对选中的结构做完成门：base/fur/rim/hoof + tapered 鹿角；
5. 完成门后仍可看 25%；它是复查，不是结构判据。

## 5. 返回值与 ops 边界

### structure_metric.py

函数返回：

| 接口 | 返回 |
|---|---|
| load_mask | (mask, width, height, layers)；mask 是 uint8 0/1 |
| mask_metrics | dict：empty / bbox / bbox_wh / bbox_aspect / area / fill_ratio / centroid / bottom_y / bottom_width |
| compare_masks | dict：ref / candidate / shape_iou / shape_dice / shape_chamfer_px / placement |
| check_shape_metrics | dict：available / pass / checks（bbox_aspect、fill_ratio 的 candidate/target/error/tolerance/pass） |
| check_landmarks | dict：available / tolerance / mean_error / max_error / landmark_pass / segment_pass / segments / landmarks / pass |

CLI 的 JSON 顶层：plan / canvas / layers / mask；有 ref 时多 ref / ref_layers / comparison；有 spec 时多 spec / shape / landmarks / ground；多个 gate 都可用时多 structure_pass。

### structure_candidates.py

stdout 只有一行 JSON：{best, mode, n, sheet}。
--out 写完整候选数组：candidates[] / best / mode。
mode：reference / spec / manual。manual 或 spec 无候选全过时 best=null，这是故意的，不猜。

### ops 边界

structure_metric / structure_candidates 不是 ops：

- 它们不修改 plan，不进入 ops.apply；
- 它们不改变 OPS 表，当前仍是 21 个算子（含 set_layer_blend）；
- 结构度量需要渲染，不能当成纯 plan 编辑算子；
- shape_ops 也仍然不是 ops，只是 plan 生成 helper。

plan_io.dump_plan 返回写入笔数 int；现在保留 stroke 的 tag，所以 landmark 标签落盘后仍可读。

## 5.1 度量本身不调用模型

structure_metric / structure_candidates 不调用 LLM、多模态模型或外部 API：

- mask 来自主体层渲染；
- 指标和 gate 是 numpy/cv2 的确定性计算；
- best 由 shape_iou 排序或 spec gate 计算；全不过时 best=null；
- 多模态模型（冷代理）只是读取 JSON/联系表的消费者；它可以做视觉复核，但不是 metric 的一部分。

所以“A 被拒”是数值 gate 的结果，不是模型看了返回值后自己决定的。模型可以忽略返回值，但那是调用方行为，不是接口行为。

## 6. 边界

- 这不是自动生成结构；它只测候选。
- 没有参考/landmark 时不能自动判断“结构对不对”，只能并排给人看。
- spec 是题材相关的数据，不是引擎常量；没有 stag 解剖硬编码进绘制代码。
- 结构 metric 只测二维剪影/比例；不测 3D 解剖、材质或审美。
