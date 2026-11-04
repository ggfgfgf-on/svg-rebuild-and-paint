"""新组件性质断言：区域重规划 / 验收门 / 谱系 / 掩膜注册表。

全部只断言可迁移的性质，不锁像素数值。
"""
import json
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import accept as AC
import edit_loop as EL
import lineage as LG
import mask_registry as MR
import masks
import planner_region as PR

ok = True
def check(n, c, extra=""):
    global ok
    print(("PASS " if c else "FAIL ") + n + (" " + str(extra) if extra else ""))
    ok = ok and bool(c)

def stroke(layer, color, pts):
    return {"layer": layer, "color": color, "size": 10.0, "points": [[x, y, 1.0] for x, y in pts]}

# ---- 区域重规划合并 ----
mask = masks.from_json(masks.to_json(masks.from_bool(
    np.array([[False] * 40 for _ in range(10)] + [[True] * 40 for _ in range(10)] +
             [[False] * 40 for _ in range(10)], bool))))
base = {"layers": [{"name": "band"}, {"name": "ink"}],
        "strokes": [stroke("band", "#111111", [(5, 5)]),
                    stroke("band", "#222222", [(5, 15)]),      # 区域内 -> 被替换
                    stroke("ink", "#333333", [(5, 15)])]}     # 非目标层 -> 保留
new = {"layers": [{"name": "band"}, {"name": "ink"}],
       "strokes": [stroke("band", "#999999", [(6, 16)]),      # 区域内 -> 合入
                   stroke("band", "#888888", [(6, 2)]),       # 区域外 -> 丢弃
                   stroke("ink", "#777777", [(6, 16)])]}      # 非目标层 -> 丢弃
merged, stats = PR.merge(base, new, mask, ["band"], mode="center")
colors = sorted(s["color"] for s in merged["strokes"])
check("outside stroke kept", "#111111" in colors)
check("inside stroke replaced", "#222222" not in colors and "#999999" in colors)
check("new stroke outside region dropped", "#888888" not in colors)
check("non-target layer untouched", any(s["layer"] == "ink" and s["color"] == "#333333" for s in merged["strokes"]))
check("count equals len(strokes)", merged["count"] == len(merged["strokes"]))
check("stats dropped/added", stats["dropped"] == 1 and stats["added"] == 1, stats)

# ---- 验收门 ----
before = np.zeros((10, 10, 3), np.float64); before[..., 0] = 100
after_up = before.copy(); after_up[2:5, 2:5] = 200
after_same = before.copy()
after_wrong = before.copy(); after_wrong[2:5, 2:5] = 20
mask_b = np.zeros((10, 10), bool); mask_b[2:5, 2:5] = True
check("direction up passes", AC.check_direction(before, after_up, mask_b, "up", 0.5)["ok"])
check("direction down passes", AC.check_direction(before, after_wrong, mask_b, "down", 0.5)["ok"])
check("direction wrong sign fails", not AC.check_direction(before, after_wrong, mask_b, "up", 0.5)["ok"])
check("outside untouched passes", AC.check_outside(before, after_up, mask_b, 0.5)["ok"])
leaky = after_up.copy(); leaky[0, 0] = 255
check("outside change fails", not AC.check_outside(before, leaky, mask_b, 0.5)["ok"])
check("determinism identical", AC.check_determinism(lambda: before.copy())["ok"])
seq = iter([before.copy(), after_up.copy()])
check("determinism differs detected", not AC.check_determinism(lambda: next(seq, before))["ok"])

# ---- 谱系 ----
with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    plan_a = td / "a.plan.json"; plan_a.write_text("{}", encoding="utf-8")
    plan_b = td / "b.plan.json"; plan_b.write_text('{"x":1}', encoding="utf-8")
    e1 = LG.record(td, plan_a, "第一版", tag="it1")
    e2 = LG.record(td, plan_b, "第二版", tag="it2", parent=e1["id"], snapshot=plan_b)
    check("record ids unique", e1["id"] != e2["id"])
    check("parent链正确", e2["parent"] == e1["id"])
    lines = LG.tree(td)
    check("tree shows both", any("it1" in l for l in lines) and any("it2" in l for l in lines), lines)
    out = td / "restored.plan.json"
    LG.checkout(td, e2["id"], out)
    check("checkout restores snapshot", out.read_text(encoding="utf-8") == '{"x":1}')
    try:
        LG.checkout(td, e1["id"], td / "nope.json")
        check("checkout without snapshot raises", False)
    except SystemExit as e:
        check("checkout without snapshot raises", "快照" in str(e))

# ---- 掩膜注册表 ----
with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    src = td / "m.json"
    src.write_text(json.dumps(masks.to_json(mask)), encoding="utf-8")
    e = MR.add(td / "reg", src, source="unit-test", note="rect-ish", mid="rose")
    check("registry add keeps id", e["id"] == "rose")
    check("registry get", MR.get(td / "reg", "rose")["source"] == "unit-test")
    check("registry file copied", Path(e["path"]).exists())

print("RESULT", "OK" if ok else "FAILED")
sys.exit(0 if ok else 1)