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

## 2. 多候选：structure_candidates.py

同一个 prompt 先生成 2–3 个**结构-only** plan（只画剪影、接地、遮挡，不画毛/鹿角/高光），然后：

~~~powershell
python scripts/structure_candidates.py out/cand_*.plan.json --layers stag --ref ref.plan.json --ref-layers stag,stag_detail --thumb 320 --cols 3 --sheet out/structure_candidates.png --out out/structure_ranking.json
~~~

有参考时按 shape_iou 排序并标出 best；没有参考但有 spec/landmark 时按 landmark 最大误差排序；两者都没有时 best=null，只生成联系表供人或视觉模型选择，不假装自动排名。

关键：**不要接受第一个结构候选**。先并排渲最小图，选结构后再进完成门。

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

## 4. 推荐流程

1. 同 prompt 生成 3 个结构候选；
2. structure_candidates.py 渲最小图 + 排序/联系表；
3. 选结构最好的一个（有 ref/spec 看指标，没有就人工/视觉选）；
4. 只对选中的结构做完成门：V1 的 base/fur/rim/hoof + V3 的 tapered 鹿角；
5. 完成门后仍看 25%：它是最后一眼，不是结构判据。

## 5. 返回值与 ops 边界

### structure_metric.py

函数返回：

| 接口 | 返回 |
|---|---|
| load_mask | (mask, width, height, layers)；mask 是 uint8 0/1 |
| mask_metrics | dict：empty / bbox / bbox_wh / bbox_aspect / area / fill_ratio / centroid / bottom_y / bottom_width |
| compare_masks | dict：ref / candidate / shape_iou / shape_dice / shape_chamfer_px / placement |
| check_landmarks | dict：available / tolerance / mean_error / max_error / landmark_pass / segment_pass / segments / landmarks / pass |

CLI 的 JSON 顶层：plan / canvas / layers / mask；有 ref 时多 ref / ref_layers / comparison；有 spec 时多 spec / landmarks / ground。

### structure_candidates.py

stdout 只有一行 JSON：{best, mode, n, sheet}。
--out 写完整候选数组：candidates[] / best / mode。
mode：reference / landmark_spec / manual。manual 时 best=null，这是故意的，不猜。

### ops 边界

structure_metric / structure_candidates 不是 ops：

- 它们不修改 plan，不进入 ops.apply；
- 它们不改变 OPS 表，当前仍是 21 个算子（含 set_layer_blend）；
- 结构度量需要渲染，不能当成纯 plan 编辑算子；
- shape_ops 也仍然不是 ops，只是 plan 生成 helper。

plan_io.dump_plan 返回写入笔数 int；现在保留 stroke 的 tag，所以 landmark 标签落盘后仍可读。

## 6. 边界

- 这不是自动生成结构；它只测候选。
- 没有参考/landmark 时不能自动判断“结构对不对”，只能并排给人看。
- spec 是题材相关的数据，不是引擎常量；没有 stag 解剖硬编码进绘制代码。
- 结构 metric 只测二维剪影/比例；不测 3D 解剖、材质或审美。
