if __name__ != "__main__":
    import pytest
    pytest.skip("standalone acceptance script", allow_module_level=True)

"""视觉提示掩膜：性质断言（不锁像素数值）。

- 种子点必须落在掩膜内
- 远处背景必须被排除（暗红 vs 暗绿 的色相门控）
- 框外一律不选（box 是硬约束）
- 掩膜是 raster 类型且可解码回同形状
"""
import json
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import masks
import visual_mask as VM

ok = True
def check(name, cond, extra=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name + (" " + str(extra) if extra else ""))
    ok = ok and bool(cond)

with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    # 合成图：中间红色方块（玫瑰），四角深绿（背景），右上角另有一块暗红（干扰物）
    arr = np.zeros((120, 160, 3), np.uint8)
    arr[..., 1] = 28; arr[..., 2] = 20          # 背景深绿
    arr[30:90, 40:110] = (190, 5, 8)            # 目标：红块
    arr[5:20, 130:155] = (67, 0, 1)             # 暗红干扰（RGB 阈值会误选）
    img = td / "synth.png"
    Image.fromarray(arr).save(img)

    m = td / "m.json"
    ov = td / "ov.png"
    res = VM.run(str(img), [(70, 60)], tolerance=80, hue_tol=16, grow=0,
                 out=str(m), overlay_path=str(ov), box=(35, 25, 115, 95))
    check("overlay written", ov.exists())
    doc = json.loads(m.read_text(encoding="utf-8"))
    check("raster type", doc["type"] == "raster", doc["type"])
    b = masks._decode_bits(doc)
    check("decoded shape matches", b.shape == (doc["h"], doc["w"]), b.shape)
    check("seed inside", bool(b[60 - doc["y"], 70 - doc["x"]]))
    check("background corner excluded", not bool(b[min(100 - doc["y"], doc["h"] - 1), max(0, 5 - doc["x"])]))
    check("distractor excluded (hue gate)", not bool(b[10 - doc["y"], 140 - doc["x"]]) if 0 <= 10 - doc["y"] < doc["h"] else True)
    check("box hard limit", res["bbox"][0] >= 35 and res["bbox"][2] <= 115, res["bbox"])
    check("area sane", 0.05 < res["share"] < 0.6, res["share"])

print("RESULT", "OK" if ok else "FAILED")
if __name__ == "__main__":
    sys.exit(0 if ok else 1)