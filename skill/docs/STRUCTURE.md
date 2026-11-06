# 凭空画：结构单与结构门

这是一页流程说明，不是画法模板。它要求先填一张**由你写的结构单**，再画结构，最后才进完成门。

## 1. 结构单（空模板）

先把下面这张单填完；所有数值/坐标由你决定，文档不提供默认值：

~~~json
{
  "pose": "主体朝向、站姿、头颈方向",
  "canvas": [0, 0],
  "subject_bbox": [0, 0, 0, 0],
  "ground_y": 0.0,
  "parts": [
    {"name": "由你命名", "polygon": [[0, 0], [0, 0], [0, 0]], "z": 0, "value": 0}
  ],
  "landmarks": {
    "nose": [0, 0],
    "shoulder": [0, 0],
    "hip": [0, 0],
    "front_hoof": [0, 0],
    "hind_hoof": [0, 0]
  },
  "light": "主光方向"
}
~~~

填写时只回答结构问题，不要先想笔刷：

- 主体在画面哪里？占多大？
- 剪影由哪些块组成？谁在前、谁在后？
- 脚落在哪条接地线上？
- 头、颈、躯干、腿的接点在哪？
- 主光从哪来？

parts 的名称、数量和 z 都由你定。assemble_parts 只按 z 填充你写的多边形，不规定身体部件。

## 2. 结构门

用结构单生成**结构-only** plan（不画毛、鹿角细枝、高光、倒影），渲染后：

- 有参考 plan：用 --ref 比较，见 [STRUCTURE_METRIC.md](STRUCTURE_METRIC.md)；
- 没有参考：用你自己的 spec（不要用示例里的 null 值直接当门槛）或 structure_candidates 联系表；
- 检查：剪影是否读得出姿态、接地、遮挡、主体大小。

不通过就改结构单和几何，不要加细节。

## 3. 完成门

结构锁定后，再拆可独立调整的完成层：底色 → 受光/暗部 → 毛/纹理 → 轮廓光/脚 → 环境光/倒影/暗角。层名和数量由题材决定，不要求固定名称。

完成层做完，可以再对 final 跑一次结构 metric。注意：如果 ref 用的是你刚做的那份结构，它只能说明“细化没有把剪影改坏”，不能说明结构本身对；结构质量要用独立参考/认可的结构去比。

完成后可再对 final 跑同一个 --ref；如果结构 metric 明显变化，说明细化改了剪影，不是单纯加质感。

## 4. 接口

- shape_ops.assemble_parts(doc, layer, parts, ...)：按 z 填充模型写的 parts，支持远/近明暗偏移、边圈；见 [SHAPE_API.md](SHAPE_API.md) §2.6；

~~~python
import shape_ops
parts = [
    {"name": "far_leg", "z": 0, "value": -0.8, "polygon": [[150, 300], [210, 300], [200, 500]]},
    {"name": "body",    "z": 1, "value":  0.0, "polygon": [[120, 220], [360, 220], [400, 380], [100, 380]]},
    {"name": "near_leg","z": 2, "value":  0.3, "polygon": [[250, 340], [300, 340], [280, 520]]},
]
shape_ops.assemble_parts(doc, "stag", parts, size=12, spacing=5,
                        far_near_delta=40, edge=True)
~~~
- scripts/structure_metric.py：测量/参考比较；
- scripts/structure_candidates.py：多候选联系表/排序；
- scripts/structure_specs/stag.example.json：纯 schema，目标值全是 null；填自己的值或用 --ref。

## 6. 谁决定

- 结构单、parts：模型填；它可以在改结构时重填。本轮新增的 structure sheet 没有默认比例字段；比例由 parts/landmarks 加独立 ref/spec 检查，不要把它当成默认答案。
- 现有的 scripts/form.py 是 SVG 人像的独立比例量表，使用它自己的 RULES 或 --rules，不消费 paint plan 的 structure sheet；两者不要混用。
- 独立参考/目标：由调用方提供。可以是认可的 plan，也可以是调用方审过的 spec；模型不应自己编一个目标再宣布通过。
- metric：只执行确定性计算，返回指标/pass/exit code；它不生成结构，也不做审美判断。
- 循环：模型可以改结构再跑；不能改 pass 字段。要通过，只能改几何或改用调用方认可的目标。

一种 loop 写法：

~~~powershell
python scripts/render_plan.py out/structure.plan.json --out out/structure.png
python scripts/structure_metric.py out/structure.plan.json --layers stag --spec my-spec.json --require-pass
# exit 2 -> 改结构单/parts，重渲，再跑；exit 0 -> 进完成层
~~~

自身结构当 ref 只能证明“完成层没有漂移”，不能证明结构正确；独立 ref 才做质量门。

## 5. 不要做

- 不要用示例里的 null/占位值直接当 gate；
- 不要把 parts 名称固定成某个身体配方；
- 不要先画细节再回头找结构；
- 不要用更多笔数替代形体和遮挡。
