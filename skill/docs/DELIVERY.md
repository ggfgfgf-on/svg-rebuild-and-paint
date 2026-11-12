# Delivery Record（可选）

不挡渲染，不挡交付；复杂任务可记录，简单任务可跳过。

## 字段

| 字段 | 含义 |
|---|---|
| intent | 这次要交什么 |
| reference | 可选：用了哪个 example |
| target | 可选：做到什么程度 |
| review | 看图记录（100% / 25%） |
| gaps | 明知还差什么 |

## 使用建议

- 有匹配 example：reference 写 example id，出图后并排看。
- 没有匹配：target 写清楚，自己决定取舍。
- 不做结构校验；没有 spec_hash / verifier / 强制 gate。
- reference 是数据，不是模板；不复制参数。
