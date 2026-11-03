"""颜色连通域掩膜的验收：从图像取最大色块 -> rect 掩膜 -> 只影响该区域笔触。"""
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import masks
import plan_io

W, H = 60, 40


def main():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        img = np.zeros((H, W, 3), np.uint8)
        img[5:15, 5:25] = (200, 40, 50)      # 大色块
        img[30:34, 45:50] = (200, 40, 50)    # 同色小色块
        img[20:30, 35:45] = (40, 60, 200)    # 蓝块
        Image.fromarray(img).save(tmp / "p.png")

        m = masks.from_image(str(tmp / "p.png"), (200, 40, 50), tol=30)
        assert m and m["type"] == "rect", m
        assert abs(m["x"] - 5) <= 1 and abs(m["y"] - 5) <= 1, m
        assert m["w"] >= 20 and m["h"] >= 10, m
        assert m["x"] + m["w"] <= 26 and m["y"] + m["h"] <= 16, ("picked small blob?", m)
        print("PASS from_image: rect=(%d,%d,%dx%d) around the largest blob" % (m["x"], m["y"], m["w"], m["h"]))

        strokes = []
        for (x, y) in ((10, 8), (50, 31), (40, 25)):
            strokes.append({"layer": "a", "preset": "round_hard", "size": 6.0, "opacity": 0.9,
                            "color": [120, 120, 120], "hardness": 0.7,
                            "points": [[float(x), float(y), 0.7], [float(x) + 2, float(y) + 2, 0.9]]})
        plan_io.dump_plan(tmp / "plan.json", W, H, strokes)
        doc, _, _ = plan_io.load_plan(tmp / "plan.json")
        sel, wt = masks.weights(doc, m, feather=0)
        assert sel == [0], ("should select only the stroke in the big blob", sel)
        assert 0 < wt[0] <= 1
        print("PASS select: %d stroke(s) inside the component bbox, others untouched" % len(sel))
    print("RESULT OK")


if __name__ == "__main__":
    main()
