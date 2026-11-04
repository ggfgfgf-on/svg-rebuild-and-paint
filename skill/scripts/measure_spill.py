"""量 sel_mode 的溢出率，写进 calibrated.json（evidence=measured）。"""
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
    mask = M.from_json(json.loads(MASK.read_text(encoding="utf-8")))
    rows = {}
    for mode in ("any", "center", "all"):
        sp = M.spill(doc, mask, mode=mode)
        rows[mode] = {"spill": round(sp["spill"], 4), "selected": sp["selected"],
                      "paint_area": sp["paint_area"], "outside_area": sp["outside_area"]}
    out = {"per_mode": rows, "image": PLAN.name, "mask": MASK.name,
            "how": "M.spill：选中笔按 采样点数x笔宽 估面积，掩膜外占比"}
    (Path.cwd() / "spill_measured.json").write_bytes(
        (json.dumps(out, ensure_ascii=False, indent=1) + "\n").encode("utf-8"))
    for k, v in rows.items():
        print("%-7s spill=%.4f selected=%d" % (k, v["spill"], v["selected"]))
    print("wrote spill_measured.json (当前目录)")


if __name__ == "__main__":
    main()
