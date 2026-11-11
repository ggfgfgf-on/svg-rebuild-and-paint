# 画面验收协议（driver 自声明，平台不写画法规则）

平台只保证机制；画面验收由 driver 在 manifest 里声明。本文档只定义 check 种类和执行方式。

## 1. 平台固定门（所有交付必过）

- 产物存在且非空
- plan_io check 通过（schema 合法）
- 同 plan、同后端、同 look 复渲 maxdiff=0
- render_arr 无 alpha 空洞（alpha=0 像素即露底）
- report 存在，可复现

## 2. driver 自声明 checks

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

## 6. 标准语义门（通用，不是画法）

以下四项是通用交付门，driver 可以声明并自行参数化；不规定题材、层、笔刷。

| id | 用什么 kind 组合 | 要回答的问题 |
|---|---|---|
| subject | region_contrast + 主体 mask | 主体区域是否存在且可读 |
| support | mask_overlap + custom | 主体/脚/塔基是否有承托面，接触点是否成立 |
| focus | region_contrast | 最亮/最暗焦点是否唯一，主体是否被抢 |
| legibility_25 | 25% 缩略图 + custom/视觉复核 | 缩到 25% 还能读出主体和场景吗 |

这些只是默认建议；driver 可以换参数、加自己的 checks。
