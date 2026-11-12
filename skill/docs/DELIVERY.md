# Delivery Contract（spec 先行，不规定画法）

平台只校验字段和顺序；不规定题材必须满足什么。

## 顺序

1. 先写 `spec`，引用用户原话。
2. 渲染前冻结 `spec_hash`；plan 开始后不得改 spec。
3. `target` 每条必须引用 spec clause。
4. `checks` 每条必须有 `spec_clause`。
5. builder 出 plan/png。
6. verifier（不同 agent/上下文）只看 spec + 图 + reference，逐条判 pass/fail。
7. 没有 verifier 签字，不得标完成。

## spec

| 字段 | 含义 |
|---|---|
| user_prompt | 用户原话，不改写 |
| style | 用户明确要的风格 |
| subject | 用户明确要的主体 |
| constraints | 用户明确要保留/避免的东西 |
| unknowns | 用户没说、由 driver 决定的项 |
| spec_hash | 冻结后的哈希 |

## target

target 是数组，每条必须对应 spec clause：

{ "clause": "constraints", "value": "天空保留空气感" }

没有 spec clause 的 target，不允许写。

## checks

每条 check 必须有 `spec_clause`：

{ "id": "sky_atmosphere", "spec_clause": "constraints", "kind": "custom" }

## review

由 verifier 写，不是 builder 自评。

## 失败处理

- 没有 spec 或没有 spec_hash：不得开始 plan。
- target/check 引用不存在的 clause：gate 失败。
- builder 和 verifier 相同：不得标完成。
- verifier 判 fail：回 builder，不许改 spec 迁就结果。

## 可执行门

```powershell
python scripts/delivery_check.py manifest.json
```

检查 spec_hash、target clause、check spec_clause、builder/verifier 是否分离；只查结构，不查画法。

## reference

reference 可选；是数据，不是模板。
