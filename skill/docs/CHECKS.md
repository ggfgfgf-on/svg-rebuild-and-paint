# 画面验收协议（driver 自声明，平台不写画法规则）

平台只保证机制；画面验收由 driver 在 manifest 里声明。本文档只定义 check 种类和执行方式。

## 1. 平台固定门（所有交付必过）

- 产物存在且非空
- plan_io check 通过（schema 合法）
- 同 plan、同后端、同 look 复渲 maxdiff=0
- render_arr 无 alpha 空洞（alpha=0 像素即露底）
- report 存在，可复现

## 2. driver 自声明 checks

每条 check 必须有 `spec_clause`，对应 DELIVERY spec/target 里的 clause；没有 clause 的 check 不得进 manifest。

driver 输出 manifest，声明自己要检查什么：

{
  "id": "lighthouse_v6",
  "checks": [
    {"id": "support",   "kind": "mask_overlap",   "args": {}},
    {"id": "no_specks", "kind": "stroke_filter",  "args": {"max_size": 3}},
    {"id": "focus",     "kind": "region_contrast","args": {}},
    {"id": "beam",      "kind": "custom",         "cmd": "python check_beam.py"}
  ]
}

平台不规定 lighthouse 必须有哪些 checks；这是 driver 自己的决定。

## 3. check kind 表

| kind | 测什么 | 关键参数 | 返回 |
|---|---|---|---|
| deterministic | 同 plan 复渲一致 | — | maxdiff |
| alpha_coverage | 无透明空洞 | — | uncovered 像素数 |
| pixel_diff | 与参照图逐像素差 | ref | mean / max |
| reference_diff | 与 reference 的结构/明暗/密度差 | reference, region | 分区域差值 |
| stroke_filter | 按笔触属性筛异常集 | layer, size_min/max, single_point | 命中数 |
| region_contrast | 区域内明度/对比 | box | luma / 对比 |
| mask_overlap | 形状与掩膜交叠 | mask_file | 命中数 |
| custom | 任意可执行检查 | cmd | pass / values |

## 4. 失败处理

- check 失败 → 回退 baseline，不叠加修补
- 失败写进 report 和 examples/drivers/*/known_issues
- 同一 failure case 反复出现时才考虑新增 kind；不加画法规则

## 5. 经验数据（不参与门）

旧 driver 的 known_issues 是检索数据；新 driver 可以读，但不把其中条目当规则。
