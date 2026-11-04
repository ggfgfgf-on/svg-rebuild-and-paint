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
    for name in CAL:
        assert re.search(r"^| \`%s\`" % re.escape(name), SK, re.M), "手册缺参数 %s 的表格行" % name
    for ev in EVIDENCE:
        assert ev in SK, "手册没解释证据类型 %s" % ev
    for ev in set(v.get("evidence") for v in CAL.values()):
        assert ev in EVIDENCE, "calibrated.json 里出现未定义证据 %r" % ev
    assert "未标定" in SK, "手册缺「未标定/已知缺口」清单"
    for name in UNSET:
        assert name in SK, "未标定清单缺 %s" % name
    for name in IMPLEMENTED:
        assert name in SK, "新实现的能力没写进手册: %s" % name
    assert "0.857" in SK and "0.703" in SK, "auto_zoom 的实测收益没写进手册"
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
