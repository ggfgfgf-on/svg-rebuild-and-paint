"""plan_pipeline 契约：路径派生、命令行拼装、dry-run、报告合并（不真的跑 planner）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import plan_pipeline as PP


def test_paths():
    p = PP.paths_for("out/demo", "ref.png")
    assert p["plan"].endswith(".plan.json") and p["report"].endswith(".report.json")
    assert p["tone"].endswith(".tone.json") and p["final"].endswith(".final.png")
    assert p["regions"].endswith(".regions.npz")
    print("PASS paths: plan/report/tone/final/regions all derived from one prefix")


def test_cmd():
    p = PP.paths_for("out/demo", "ref.png")
    p.update(reference="rose.png", regions="r.npz")
    cmd = PP.build_planner_cmd(p, python="py")
    s = " ".join(cmd)
    assert cmd[0] == "py" and "planner_8layer.py" in s
    assert "--plan" in cmd and "--report" in cmd and "--calibrate" in cmd
    assert cmd[cmd.index("--plan") + 1].endswith(".plan.json")
    assert cmd[cmd.index("--calibrate") + 1] == "ref.png"
    p2 = PP.paths_for("out/demo", None)
    p2.update(reference="rose.png", regions="r.npz")
    cmd2 = PP.build_planner_cmd(p2, python="py")
    assert "--calibrate" not in cmd2, "no target -> no calibration flag"
    print("PASS cmd: --plan/--report always, --calibrate only with target")


def test_merge_and_dry(tmp):
    pp = PP.paths_for(str(Path(tmp) / "demo"), "ref.png")
    pp.update(reference="rose.png", regions="r.npz")
    s = PP.run("rose.png", "r.npz", str(Path(tmp) / "demo"), "ref.png", python="py", dry_run=True)
    assert s["dry_run"] is True and s["cmd"][0] == "py"
    assert not Path(pp["plan"]).exists(), "dry-run must not write anything"
    rep = {"total": 100, "luma": 11.2}
    m = PP.merge_report(rep, {"final_luma": 22.3, "final_png": "x.png"})
    assert m["total"] == 100 and m["luma"] == 11.2
    assert m["final_luma"] == 22.3 and m["final_png"] == "x.png"
    print("PASS dry-run writes nothing; merge keeps planner fields and adds final stats")


def main():
    import tempfile
    test_paths()
    test_cmd()
    with tempfile.TemporaryDirectory() as td:
        test_merge_and_dry(td)
    print("RESULT OK")


if __name__ == "__main__":
    main()
