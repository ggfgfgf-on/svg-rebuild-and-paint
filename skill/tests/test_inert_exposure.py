"""未触发的判据必须"暴露"：attach_thin 的 info.inert + calibrated.json 的 inert_on_sample。"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import masks as M
import pick_instance as PI

IMG = Path(r"D:\DSHwork\flower\rose5-full.png")
TRACE = Path(r"D:\DSHwork\flower\mask_leaf_traced.json")


def main():
    from PIL import Image
    w, h = Image.open(IMG).size
    gt = PI.rasterize(M.from_json(json.loads(TRACE.read_text(encoding="utf-8"))), w, h)
    out, info = PI.attach_thin(str(IMG), gt)
    assert "inert" in info, info
    assert "attach_len" in info["inert"], info["inert"]
    assert "attach_width" in info["inert"], info["inert"]
    assert PI.LAST_ATTACH["inert"] == info["inert"], "info 要同时挂到模块级，供管线读取"
    cal = json.loads((ROOT / "tests" / "fixtures" / "calibrated.project.json").read_text(encoding="utf-8"))
    for name in ("attach_len", "attach_width"):
        assert cal[name].get("inert_on_sample") is True, "%s 缺 inert_on_sample 标记" % name
        assert cal[name].get("inert_reason"), "%s 缺 inert 原因" % name
    # 触发了判据的参数不许乱标
    for name, e in cal.items():
        assert not (name == "attach_gap" and e.get("inert_on_sample")), "attach_gap 实际触发过，不该标 inert"
    print("PASS inert 暴露：%s（模块级 LAST_ATTACH 同步）" % info["inert"])
    print("RESULT OK")
    return True


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
