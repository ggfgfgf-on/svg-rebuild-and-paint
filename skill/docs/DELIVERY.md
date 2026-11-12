# Delivery Contract（driver 自声明）

平台只校验字段存在；不规定画面标准，也不规定题材必须满足什么。

## 必填字段

| 字段 | 含义 |
|---|---|
| intent | 这次要交什么 |
| reference | 可选：拿哪个 driver 当参照 |
| target | 这次“做到什么程度”算交付；没有 reference 时必填 |
| accept | 平台门 + driver 自声明 checks |
| review | 实际看过的图（100% / 25%）与结论 |
| gaps | 明知还差什么；不许隐瞒 |

## manifest 示例

{
  "id": "lighthouse_v10",
  "delivery": {
    "intent": "黄昏灯塔，厚涂",
    "reference": "v4_lighthouse",
    "target": "结构接近参考，细节按本图需要取舍",
    "accept": ["platform_gates", "driver_checks"],
    "review": ["100%: ok", "25%: ok"],
    "gaps": ["光束比参考软"]
  },
  "checks": []
}

## 规则

- 没有 delivery 或没有 target/reference 的 driver，不得标记完成。
- reference 是数据，不是模板；可以读、对比，不能复制参数冒充原创。
- gaps 必须写；写“无”就表示你愿意为没有已知缺陷负责。
- accept 里可以引用 docs/CHECKS.md 的 kind，但具体参数由 driver 定。
- 平台不要求接地、主体、焦点、25% 这些词；driver 自己决定要不要写进 target。

## 与 CHECKS 的关系

- DELIVERY：这次要交什么、做到什么程度、还差什么。
- CHECKS：怎么执行检查、怎么记录结果。
