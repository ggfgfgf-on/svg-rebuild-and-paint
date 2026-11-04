"""把工程的标定值/实测表渲染成报告：属于工程，不进技能手册。"""
import argparse
import json
from pathlib import Path

BT = chr(96)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default="calibrated.json")
    ap.add_argument("--measured", default="sources_measured.json")
    ap.add_argument("--spill", default="spill_measured.json")
    ap.add_argument("--out", default="CALIBRATION.md")
    a = ap.parse_args()
    pj = Path(a.json)
    cal = json.loads(pj.read_text(encoding="utf-8")) if pj.is_file() else {}
    L = ["# 本工程标定报告（自动生成，勿手改）", "",
         "> 由 scripts/calib_report.py 从工程 calibrated.json 与实测表生成；",
         "> 技能手册只讲机制，具体值与实测属于本工程。", "",
         "## 选值（%d 项）" % len(cal), "",
         "| 参数 | 值 | 证据 | 来源 | 依据 |", "| --- | --- | --- | --- | --- |"]
    for k in sorted(cal):
        e = cal[k]
        spec = e.get("spec") or {}
        src = spec.get("samples_file") or e.get("contact_sheet") or "-"
        flag = "（**未触发**）" if e.get("inert_on_sample") else ""
        row = "| %s%s%s | %s%s | %s | %s | %s |" % (
            BT, k, BT, e.get("value"), flag, e.get("evidence", ""), src,
            str(e.get("reason", "")).replace("|", "/"))
        L.append(row)
    for title, path, key in (("cells 精度", a.measured, "cells"), ("溢出实测", a.spill, "per_mode")):
        p = Path(path)
        if not p.is_file():
            continue
        d = json.loads(p.read_text(encoding="utf-8"))
        L += ["", "## " + title + "（" + p.name + "）", "", "```json",
              json.dumps(d.get(key) or d, ensure_ascii=False, indent=1), "```"]
    Path(a.out).write_bytes(("\n".join(L) + "\n").encode("utf-8"))
    print("wrote %s (%d params)" % (a.out, len(cal)))


if __name__ == "__main__":
    main()