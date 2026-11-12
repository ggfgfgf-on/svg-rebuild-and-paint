# Verification（可选；builder / verifier 分离）

verifier 不写 driver，不看 builder 的解释，只读：

- spec（含 spec_hash）
- 最终 PNG
- 可选 reference

## 输出

逐条 target clause：

| clause | 要求 | 结果 | 证据 |
|---|---|---|---|
| style | ... | PASS/FAIL | 看图/指标 |

## 完成条件

- 所有 target clause 都有 PASS/FAIL。
- FAIL 必须回 builder；不能改 spec 迁就结果。
- verifier 不是 builder。
- 没有 verifier 签字，不得标完成。
