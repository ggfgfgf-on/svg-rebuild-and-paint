"""examples/demo.plan.json 必须能被 plan_io 读、层数与笔数对得上——它是外部调用方的起点样板。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import plan_io


def main():
    demo = ROOT / "examples" / "demo.plan.json"
    assert demo.exists(), "缺少 examples/demo.plan.json"
    doc, *_ = plan_io.load_plan(str(demo))
    assert doc["canvas"] == [480, 320], doc["canvas"]
    assert len(doc["layers"]) == 4, [l["name"] for l in doc["layers"]]
    assert int(doc["count"]) == len(doc["strokes"]) >= 300, (doc["count"], len(doc["strokes"]))
    names = {l["name"] for l in doc["layers"]}
    assert all(s["layer"] in names for s in doc["strokes"]), "笔触引用了不存在的层"
    ops = ROOT / "examples" / "demo.ops.json"
    assert ops.exists(), "缺少 examples/demo.ops.json"
    assert (ROOT / "scripts" / "selfcheck.py").exists(), "缺少 scripts/selfcheck.py"
    print("PASS demo.plan.json %d 笔 / %d 层；demo.ops.json 与 selfcheck.py 都在" % (len(doc["strokes"]), len(doc["layers"])))
    print("RESULT OK")
    return True


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
