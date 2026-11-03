"""30 条指令批量评测：编译产物 vs 期望算子/层，并做 schema 校验。

    python eval_instructions.py <plan.json> <expected.json> <compiled.json>

判定一条正确 = schema 通过 + 算子名集合相同 + 受影响层集合相同。
输出正确率、失败明细（最多 6 条）。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import compile_ops
import plan_io


def main():
    plan, exp_path, got_path = sys.argv[1], sys.argv[2], sys.argv[3]
    doc, w, h = plan_io.load_plan(plan)
    expected = json.loads(Path(exp_path).read_text(encoding="utf-8"))
    compiled = json.loads(Path(got_path).read_text(encoding="utf-8"))
    by_id = {int(c.get("id", i + 1)): c for i, c in enumerate(compiled)}

    rows, fails = [], []
    for e in expected:
        c = by_id.get(int(e["id"]))
        rec = {"id": e["id"], "instruction": e["instruction"]}
        if c is None:
            rec.update(ok=False, why="missing")
            fails.append(rec)
            rows.append(rec)
            continue
        specs = c.get("ops")
        try:
            compile_ops.validate_ops(doc, specs)
            schema_ok, err = True, ""
        except Exception as ex:
            schema_ok, err = False, str(ex)[:120]
        names = {s.get("op") for s in (specs or []) if isinstance(s, dict)}
        layers = {s.get("layer") for s in (specs or []) if isinstance(s, dict) and s.get("layer")}
        alts = e.get("any_of") or [{"ops": e.get("ops", []), "layers": e.get("layers", [])}]
        matched = [a for a in alts
                   if names == set(a["ops"]) and layers == set(a["layers"])]
        op_ok = bool(matched)
        layer_ok = op_ok
        ok = bool(schema_ok and op_ok and layer_ok)
        rec.update(ok=ok, schema_ok=schema_ok, why=err or ("" if ok else "ops/layers mismatch"),
                   got_ops=sorted(x for x in names if x), got_layers=sorted(x for x in layers if x))
        rows.append(rec)
        if not ok:
            fails.append(rec)

    n = len(expected)
    schema_pass = sum(1 for r in rows if r.get("schema_ok"))
    print(json.dumps({
        "total": n,
        "correct": n - len(fails),
        "rate": round((n - len(fails)) / n, 3),
        "schema_ok": schema_pass,
        "failures": [{k: v for k, v in f.items() if k not in ("id", "schema_ok")} for f in fails[:6]],
    }, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
