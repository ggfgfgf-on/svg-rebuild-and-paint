"""auto_zoom：自动给的窗口粒度应当达到该尺寸的理论上限（不是块状 0.70）。"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import numpy as np
import pick_instance as PI
import masks as M

IMG = Path(r"D:\DSHwork\flower\rose5-full.png")
TRACE = Path(r"D:\DSHwork\flower\mask_leaf_traced.json")
ROI = [80, 500, 520, 790]


def _iou(a, b):
    a = a.astype(bool); b = b.astype(bool)
    return float((a & b).sum()) / max(1, (a | b).sum())


def _col(i):
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def main():
    from PIL import Image
    w, h = Image.open(IMG).size
    gt = PI.rasterize(M.from_json(json.loads(TRACE.read_text(encoding="utf-8"))), w, h)
    plan = PI.zoom_plan(str(IMG), ROI, max_cell_px=40, max_cells=64)
    assert plan["windows"], plan
    assert plan["cell_px"] <= 40, plan
    for win in plan["windows"]:
        assert win["cols"] * win["rows"] <= 64, win
    # 用"格子有 >=50% 落在描边内"这条规则自动填 pos，验证粒度收益（模型只负责报格子，这里模拟它）
    wins = []
    for win in plan["windows"]:
        x0, y0, x1, y1 = win["roi"]
        cw, ch = (x1 - x0) / win["cols"], (y1 - y0) / win["rows"]
        pos = []
        for r in range(win["rows"]):
            for c in range(win["cols"]):
                cx0, cy0 = int(x0 + c * cw), int(y0 + r * ch)
                cx1, cy1 = int(x0 + (c + 1) * cw), int(y0 + (r + 1) * ch)
                cell = gt[cy0:cy1, cx0:cx1]
                if cell.size and cell.mean() >= 0.5:
                    pos.append("%s%d" % (_col(c), r + 1))
        wins.append(dict(id=win["id"], roi=win["roi"], cols=win["cols"], rows=win["rows"], pos=pos, neg=[]))
    m = PI.cells_mask(str(IMG), wins)
    iou_auto = _iou(m, gt)
    iou_root = _iou(PI.cells_mask(str(IMG), [dict(id="root", roi=ROI, cols=8, rows=6,
                                                  pos=["D2","E2","F2","C3","D3","E3","F3","C4","D4","E4","F4","C5","D5","E5","F5","G4","G5"],
                                                  neg=[])]), gt)
    assert iou_auto > iou_root + 0.05, (iou_auto, iou_root)
    assert iou_auto >= 0.80, iou_auto
    print("PASS zoom_plan cell=%.1fpx cells=%d IoU=%.3f（固定 8x6 根窗 %.3f）"
          % (plan["cell_px"], plan["total_cells"], iou_auto, iou_root))
    print("RESULT OK")
    return True


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
