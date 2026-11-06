# form.py 接口

form.py 测量 **SVG 人像**的颜色分区比例。它不是 paint plan 的结构 metric，也不生成笔画。

## 参数

| 参数 | 默认 | 说明 |
|---|---|---|
| svg | 必填 | 输入 SVG |
| --out | 空 | 写完整 JSON 报告 |
| --guides | 空 | 写刻度尺/中轴/脸框/眼线预览图 |
| --size | 从 SVG 读 | WxH |
| --tol | 2 | 颜色匹配容差（通道最大差） |
| --parts | 内置示例配色 | JSON 对象 {name: "#rrggbb"}，覆盖颜色分区 |
| --rules | 内置示例规则 | JSON 规则数组，覆盖比例规则 |

## --parts

- 每个 value 必须与 SVG 里对应区域的实际 fill 完全一致（在 --tol 内）；
- 抗锯齿、渐变、混合色会让颜色分区失真；
- 内置配色只是示例，真实 SVG 必须用 --parts 指定。示例见 [EXAMPLES.md](EXAMPLES.md) §10。

## --rules

格式是 form.py 专用的 JSON 数组：

~~~json
[
  ["rule_name", target, tolerance, "note"]
]
~~~

- 规则名必须是 form.py 支持的名称；
- 未知名称会让该规则 value/ok 为 null，不会被当成通过；
- 这不是 structure_specs/*.json 的 schema：后者是 paint plan 的 shape/landmark/segment 尺子，两者不要混用；
- 内置规则只是人像示例，换题材就要用 --rules 覆盖。示例见 [EXAMPLES.md](EXAMPLES.md) §10。

## 返回

--out JSON：

- svg / size；
- parts：每个颜色分区的 fill / px / bbox；
- ratios：规则名 -> 值；
- verdicts：{rule, value, target, tol, ok, note}；
- failed：ok=false 的规则名；
- guides_png。

stdout 只打印 ratios / failed / guides_png 摘要。

## 错误

- --parts / --rules 不是合法 JSON，或 rules 不是数组/每项不足 3 个字段：直接报错退出；
- SVG 缺少某个颜色时，对应规则 value=null，不会被当成通过。

## 边界

- 只认纯色 fill；
- 默认配色/规则是人像示例，不是通用人体标准；
- 与 STRUCTURE_METRIC.md 是两套不同接口。
