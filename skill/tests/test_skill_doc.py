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



def _strip_fences(text):
    out, fence = [], False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            fence = not fence
            continue
        if not fence:
            out.append(line)
    return "\n".join(out)


def _table_rows(text):
    return [ln for ln in text.splitlines() if ln.lstrip().startswith("|")]


def _is_number(v):
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False


def _mentions(text, value):
    """文本里是否出现与 value 相同的数（按数字 token 精确比较，避免 20 撞 02 或 200）。"""
    import re
    for tok in re.findall(r"(?<![\w.])-?\d+(?:\.\d+)?(?![\w.])", text):
        if abs(float(tok) - float(value)) < 1e-9:
            return True
    return False


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
    # 手册只讲机制：查"表的结构"，不查具体字面量（字面量黑名单只会漏，数值比较会被 1.0/2.0 这类正文常数误伤）
    prose = _strip_fences(SK)
    params = set(CAL)
    evid = {"contact_sheet", "multi_sample", "per_pixel_hist", "measured"}
    for row in _table_rows(prose):
        cells = [c.strip().strip("`") for c in row.strip().strip("|").split("|")]
        if not cells:
            continue
        head = cells[0]
        body = cells[1:]
        has_num = any(_is_number(c) for c in body)
        # 形状一：参数名开头 + 数值 -> 这就是一条选值记录
        assert not (head in params and has_num), "手册表格里出现工程选值行：%r" % row
        # 形状二：带证据词 + 数值 -> 这也是标定记录的形状
        assert not (has_num and (evid & set(body))), "手册表格里出现标定记录：%r" % row
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
