"""工程报告生成器：值/实测要落在工程 CALIBRATION.md，不进技能手册。"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures" / "calibrated.project.json"


def main():
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "CALIBRATION.md"
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / "calib_report.py"),
                            "--json", str(FIX), "--out", str(out)],
                           capture_output=True, text=True, encoding="utf-8")
        assert r.returncode == 0, r.stderr
        txt = out.read_text(encoding="utf-8")
        cal = json.loads(FIX.read_text(encoding="utf-8"))
        for name in cal:
            assert name in txt, "报告缺参数 %s" % name
        assert "未触发" in txt, "inert 条目没在报告里标出来"
        assert str(cal["attach_px"]["value"]) in txt
        print("PASS 工程报告 %d 项 + inert 标记" % len(cal))
    print("RESULT OK")
    return True


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
