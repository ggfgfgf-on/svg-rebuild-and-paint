"""手册自检：SKILL.md 的标定表格不许漏项，证据类型与未标定清单不许漂移。"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import json

SK = (ROOT / "SKILL.md").read_text(encoding="utf-8")
CAL = json.loads((ROOT / "tests" / "fixtures" / "calibrated.project.json").read_text(encoding="utf-8"))
EVIDENCE = ("contact_sheet", "multi_sample", "per_pixel_hist", "measured")
UNSET = ()  # 本轮全部扫完/实现；tol 属工程自测，见手册「未标定」表
IMPLEMENTED = ("max_spill", "auto_zoom", "zoom_plan")


def main():
    # 参数名可以出现在手册里（作为例子），但不要求逐个列值表
    for ev in EVIDENCE:
        assert ev in SK, "手册没解释证据类型 %s" % ev
    for ev in set(v.get("evidence") for v in CAL.values()):
        assert ev in EVIDENCE, "calibrated.json 里出现未定义证据 %r" % ev
    assert "未标定" in SK or "未验证" in SK, "手册缺「未标定/未验证」说明"
    for name in UNSET:
        assert name in SK, "未标定清单缺 %s" % name
    IMPLEMENTED = ("max_spill", "zoom_plan", "calib_report")
    for name in IMPLEMENTED:
        assert name in SK, "新实现的能力没写进手册: %s" % name
    # 手册只讲机制；工程实测值（具体数字/选值表）属于工程报告，不许写进总手册
    for leak in ("0.857", "0.703", "| `model_grow_px` | 20 |", "| `edge_min_contrast` | 0.40 |"):
        assert leak not in SK, "总手册里出现了工程实测值：%r（应写进工程 CALIBRATION.md）" % leak
    for mech in ("SVP_CALIBRATED", "SVP_MEASURED", "CALIBRATED_SOURCE", "uncalibrated", "calib_report.py"):
        assert mech in SK, "手册缺机制说明：%s" % mech
    assert "CALIBRATION.md" in SK, "手册应指向工程报告 CALIBRATION.md"
    assert "inert" in SK and "LAST_ATTACH" in SK, "手册缺「未触发判据暴露」说明"
    assert "SVP_CALIBRATED" in SK, "手册缺 tol 的工程覆盖机制（SVP_CALIBRATED）"
    assert "tol" in SK, "手册缺 tol 的工程级标定说明"
    assert "attach_px" in SK and "near" in SK, "手册缺 attach_px 依赖 near 的提醒"
    assert "不重新分割" in SK or "不重分" in SK, "手册缺 refine 不能重分割的提醒"
    print("PASS 手册覆盖 %d 个已标定参数、%d 类证据、%d 个未标定项" % (len(CAL), len(EVIDENCE), len(UNSET)))
    print("RESULT OK")
    return True


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
