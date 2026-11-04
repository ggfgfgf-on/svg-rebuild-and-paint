"""工程标定值必须是数：坏值要当场报错，不能流到下游。"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import pick_instance as PI


def _with(value):
    td = tempfile.mkdtemp()
    p = Path(td) / "calibrated.json"
    p.write_bytes(json.dumps({"ring": {"value": value}}).encode("utf-8"))
    os.environ["SVP_CALIBRATED"] = str(p)
    try:
        return PI.calibrated("ring", 14)
    finally:
        os.environ.pop("SVP_CALIBRATED", None)


def main():
    assert _with(20) == 20
    assert abs(_with("20.5") - 20.5) < 1e-9, "数字字符串应能转成数"
    for bad in ("十四", None, [1], {"a": 1}, True):
        try:
            _with(bad)
        except ValueError as e:
            assert "工程标定值非法" in str(e) and "ring" in str(e), str(e)
        else:
            raise AssertionError("坏值 %r 没有报错" % (bad,))
    print("PASS 坏值当场报错（含参数名与来源）；数字字符串可转")
    print("RESULT OK")
    return True


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
