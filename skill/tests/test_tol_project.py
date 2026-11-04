"""tol 属工程标定：SVP_CALIBRATED 指向工程文件时，masks.from_image 必须用工程的值。"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import masks as M

IMG = Path(r"D:\DSHwork\flower\rose5-full.png")


def main():
    med = [23, 41, 4]   # 这张图叶子偏暗，正好用来区分容差
    base = M.from_image(str(IMG), med, tol=15)
    b15 = M.from_image(str(IMG), med, tol=15)
    b80 = M.from_image(str(IMG), med, tol=80)
    assert b15 != b80, "容差不同必须得到不同掩膜"
    with tempfile.TemporaryDirectory() as td:
        proj = Path(td) / "calibrated.json"
        proj.write_bytes(json.dumps({"tol": {"value": 80, "chosen_by": "model",
                                             "evidence": "contact_sheet", "reason": "工程自测"}}).encode("utf-8"))
        os.environ["SVP_CALIBRATED"] = str(proj)
        try:
            env_mask = M.from_image(str(IMG), med)          # tol=None -> 读工程
            skill_mask = M.from_image(str(IMG), med, tol=15)
            assert env_mask == b80, "SVP_CALIBRATED 没生效：应等于 tol=80"
            assert skill_mask != env_mask, "显式 tol 应优先于工程文件"
        finally:
            os.environ.pop("SVP_CALIBRATED", None)
        # 工程文件里没有 tol 时回落到默认 40
        proj.write_bytes(json.dumps({"other": {"value": 1}}).encode("utf-8"))
        os.environ["SVP_CALIBRATED"] = str(proj)
        try:
            fallback = M.from_image(str(IMG), med)
            assert fallback == M.from_image(str(IMG), med, tol=40), "缺 tol 应回落 40"
        finally:
            os.environ.pop("SVP_CALIBRATED", None)
    print("PASS tol 工程覆盖生效（15/80 掩膜不同、工程值优先、缺失回落 40）")
    print("RESULT OK")
    return True


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
