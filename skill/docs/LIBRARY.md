# 增长库：候选 → 正式 pattern

目标：每次真正解决了问题，就把可复用的技法沉淀下来；但正式库不能被一次成功或一次偷懒污染。

```text
使用时：读 examples/patterns/index.json + drivers/index.json
画完后：有可复用技法 → examples/inbox/<id>/ 写候选
检查后：通过 → examples/patterns/<id>/；不确定 → 留在 inbox
```

## 候选格式

```text
examples/inbox/<id>/
  candidate.json
  crop.png
  snippet.py      20–50 行关键代码，不是整份 driver
  metrics.json
  source driver   可选；copy_check 用它比对
```

`candidate.json`：

```json
{
  "id": "heron-marsh-v2",
  "subject": "heron",
  "source": "cold-heron-boat2",
  "status": "candidate",
  "tags": ["heron", "reflection"],
  "assets": {"crop": "crop.png", "snippet": "snippet.py", "metrics": "metrics.json"},
  "source_driver": "build_heron.py",
  "borrow": ["长颈收尖"],
  "avoid": ["倒影比主体实"],
  "known_issues": ["头颈比例容易风格化"],
  "review": "和 B7/B8 并排看过；倒影更干净，主体略简",
  "parent": null,
  "changes": [],
  "copy_allowed": false,
  "copy_allowed_reason": ""
}
```

`review` 必须写清看过什么、和谁比、牺牲了什么。空 review 不允许晋升。

## 细节修改：revision，不是新 pattern

如果新候选只是对已有 pattern 的局部优化，不要新建重复条目。写：

```json
"parent": "sunset-soft",
"changes": ["反光柱更窄", "云层透明度整体降 0.02"]
```

`check` 看到 `parent + changes` 时，即使 driver 重合 ≥ 0.98，也判为 `mode=revision`，不拒绝。

`promote` 会：

1. 备份原 `patterns/<parent>/` 到 `examples/_history/<parent>/<timestamp>/`
2. 用候选的 crop / snippet / metrics 更新原 pattern
3. 把 revision 记录追加到原 `pattern.md`
4. 不新增 `patterns/index.json` 条目

只有“真正的新技法/新主体”才用新 id；细节修改走 revision。

## 两步

```powershell
# 1. 建候选
python scripts/grow_library.py new --id heron-marsh-v2 --subject heron --source cold-heron-boat2 --driver build_heron.py

# 2. 填完 crop / snippet / metrics / borrow / avoid / known_issues / review
python scripts/grow_library.py check examples/inbox/heron-marsh-v2
python scripts/grow_library.py promote examples/inbox/heron-marsh-v2

# 细节修改
python scripts/grow_library.py new --id sunset-soft-r2 --subject sunset --source A11 --driver make_sunset.py --parent sunset-soft --change "反光柱更窄"
```

## 门拦什么

- 缺 crop / snippet / metrics / review：拒绝
- `borrow`、`avoid`、`known_issues` 为空：拒绝
- `source_driver` 与现有 driver/snippet 的 shingle 重合 ≥ 0.98：判定复制，拒绝
- 已有同名 pattern：拒绝
- 复制不是绝对禁止，但必须显式 `copy_allowed: true` + 理由

机械门不判断“画得好不好”。好不好由 `review` 和看图负责；机械门只拦“什么都没写”和“直接复制”。

## 什么时候晋升

- 新主体、新材质、新光型、新的可复用结构：值得晋升
- 只是同一 pattern 的微调：留在 inbox，或写成 patch 说明，不新增重复条目
- 只有图好看、但说不清借什么：不晋升
- 通过后更新 `examples/patterns/index.json` 和 `examples/CHANGELOG.md`

## 主动但不失控

模型在使用中可以主动写候选、自己跑 check；`promote` 是显式动作，不是每次画完都自动执行。

正式库的提交仍走 git；晋升后可以回滚。这样库会增长，但不会因为一次偶然出图就把错误固化成参考。
