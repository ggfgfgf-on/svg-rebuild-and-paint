"""技能不许自带标定值：缺工程文件时必须回落默认值并发 uncalibrated 通知。"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import ops
import pick_instance as PI


def main():
    assert not (ROOT / "scripts" / "calibrated.json").exists(), \
        "技能自己发布了标定值：这些值是某个工程测出来的，属于工程目录"
    old = os.environ.pop("SVP_CALIBRATED", None)
    cwd = os.getcwd()
    try:
        os.chdir(str(ROOT))
        PI.CALIBRATED_SOURCE = "default"
        v = PI.calibrated("model_grow_px", 14)
        assert v == 14, "没有工程文件时必须回落未验证默认值，实际 %r" % (v,)
        assert PI.CALIBRATED_SOURCE == "default", PI.CALIBRATED_SOURCE
        ops.LAST_NOTICES.clear()
        notice = ops.check_calibration()
        assert notice and notice["kind"] == "uncalibrated", notice
        assert any(n["kind"] == "uncalibrated" for n in ops.LAST_NOTICES)
        # 有工程文件时不发该通知
        PI.CALIBRATED_SOURCE = "project"
        assert ops.check_calibration() is None
    finally:
        os.chdir(cwd)
        PI.CALIBRATED_SOURCE = "default"
        if old:
            os.environ["SVP_CALIBRATED"] = old
    print("PASS 技能不带标定值；缺工程文件 -> 默认值 + uncalibrated 通知")
    print("RESULT OK")
    return True


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
