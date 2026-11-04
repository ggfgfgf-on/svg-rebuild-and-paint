"""溢出闸门：any 会触发通知，center/all 不触发；通知不抛错。"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import masks as M
import ops
import plan_io

PLAN = Path(r"D:\DSHwork\flower\_new\v2.plan.json")
MASK = Path(r"D:\DSHwork\flower\mask_leaf_traced.json")


def main():
    doc, w, h = plan_io.load_plan(str(PLAN))
    mask = json.loads(MASK.read_text(encoding="utf-8"))
    specs = lambda mode: [{"op": "set_layer_hue", "layer": "band", "hue": 250.0, "mask": mask, "sel_mode": mode}]
    out_any = ops.apply(doc, specs("any"))
    kinds_any = [n["kind"] for n in ops.LAST_NOTICES]
    assert "spill" in kinds_any, "any 模式应触发溢出通知，实际 %r" % kinds_any
    sp = ops.LAST_SPILL["set_layer_hue"]
    assert sp["spill"] > sp["limit"], sp
    out_center = ops.apply(doc, specs("center"))
    kinds_center = [n["kind"] for n in ops.LAST_NOTICES]
    assert "spill" not in kinds_center, "center 不该触发溢出通知，实际 %r" % kinds_center
    # 通知是提醒不是错误：两次 apply 都正常返回
    assert out_any["strokes"] and out_center["strokes"]
    print("PASS any spill=%.3f(>%.2f 触发) center spill=%.3f(不触发)"
          % (sp["spill"], sp["limit"], ops.LAST_SPILL.get("set_layer_hue", {}).get("spill", -1)))
    print("RESULT OK")
    return True


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
