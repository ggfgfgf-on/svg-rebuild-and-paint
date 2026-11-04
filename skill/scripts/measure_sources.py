"""重测 SOURCES 里的 measured（格子分辨率 -> IoU），把数字从"我手填"变成"跑出来的"。

    python scripts/measure_sources.py --image rose5-full.png --trace mask_leaf_traced.json \
        --roi 80,500,520,790 --tiles 55,48,32,24,16,11,8 --out sources_measured.json

口径（写死在这里，避免每次重测换口径）：
  * "格子模型"= 在给定 roi 上按 tile 切格，某格有 >=50% 像素落在人工描边内就报正格；
  * IoU = 报正格并集 vs 人工描边；
  * 这只测"格子分辨率本身的上限"（理想模型），不测某个具体模型报得好不好；
  * refine 一列用 refin_loop 从最粗那档格子并集出发精修。
"""
import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def measure(image, trace, roi, tiles, refine_tile=None):
    import numpy as np
    import pick_instance as PI
    import masks
    from PIL import Image
    w, h = Image.open(image).size
    gt = PI.rasterize(masks.from_json(json.loads(Path(trace).read_text(encoding="utf-8"))), w, h)
    out = {"spec": {"image": Path(image).name, "trace": Path(trace).name, "roi": list(roi),
                    "rule": "cell>=50% inside trace"},
           "cells": {"iou_by_tile": {}, "edge_by_tile": {}}}
    for tile in tiles:
        cols = max(1, round((roi[2] - roi[0]) / tile))
        rows = max(1, round((roi[3] - roi[1]) / tile))
        cw = (roi[2] - roi[0]) / cols
        ch = (roi[3] - roi[1]) / rows
        keep = np.zeros((h, w), bool)
        npos = 0
        for j in range(rows):
            for i in range(cols):
                y0 = int(roi[1] + j * ch); y1 = int(roi[1] + (j + 1) * ch)
                x0 = int(roi[0] + i * cw); x1 = int(roi[0] + (i + 1) * cw)
                blk = gt[y0:y1, x0:x1]
                if blk.size and blk.mean() >= 0.5:
                    keep[y0:y1, x0:x1] = True
                    npos += 1
        iou = float((keep & gt).sum()) / max(1.0, float((keep | gt).sum()))
        er = PI.edge_report(image, keep, search_px=6)
        out["cells"]["iou_by_tile"][str(int(tile))] = round(iou, 3)
        out["cells"]["edge_by_tile"][str(int(tile))] = {"ok": bool(er["ok"]), "contrast": er["contrast"],
                                                        "pos_cells": int(npos), "cols": cols, "rows": rows}
        print("tile %2dpx  格 %3d  正格 %3d  IoU %.3f  edge ok=%s contrast %+.2f" %
              (tile, cols * rows, npos, iou, er["ok"], er["contrast"]))
    if refine_tile:
        tile = int(refine_tile)
        cols = max(1, round((roi[2] - roi[0]) / tile)); rows = max(1, round((roi[3] - roi[1]) / tile))
        cw = (roi[2] - roi[0]) / cols; ch = (roi[3] - roi[1]) / rows
        pos = []
        for j in range(rows):
            for i in range(cols):
                y0 = int(roi[1] + j * ch); y1 = int(roi[1] + (j + 1) * ch)
                x0 = int(roi[0] + i * cw); x1 = int(roi[0] + (i + 1) * cw)
                blk = gt[y0:y1, x0:x1]
                if blk.size and blk.mean() >= 0.5:
                    pos.append([i, j])
        wins = [{"roi": list(roi), "cols": cols, "rows": rows,
                 "pos": ["%s%d" % (chr(65 + i), j + 1) for i, j in pos]}]
        bits0 = _cells_bits(roi, cols, rows, pos, w, h)
        bits, info = PI.refine_loop(image, wins, init_mask=bits0, anchors=PI.cells_anchors(image, wins), iters=6,
                                    verbose=False)
        iou = float((bits & gt).sum()) / max(1.0, float((bits | gt).sum()))
        out["cells"]["refined"] = {"tile": tile, "iou": round(iou, 3), "iters": info.get("iters"),
                                   "area": round(float(bits.mean()), 4)}
        print("refine(%dpx 种子) -> IoU %.3f  iter=%s" % (tile, iou, info.get("iters")))
    return out


def _cells_bits(roi, cols, rows, pos, w, h):
    import numpy as np
    m = np.zeros((h, w), bool)
    cw = (roi[2] - roi[0]) / cols; ch = (roi[3] - roi[1]) / rows
    for i, j in pos:
        m[int(roi[1] + j * ch):int(roi[1] + (j + 1) * ch), int(roi[0] + i * cw):int(roi[0] + (i + 1) * cw)] = True
    return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", required=True)
    ap.add_argument("--trace", required=True)
    ap.add_argument("--roi", default="0,0,0,0")
    ap.add_argument("--tiles", default="55,48,32,24,16,11,8")
    ap.add_argument("--refine-tile", default="")
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    roi = [float(x) for x in a.roi.split(",")]
    if roi == [0, 0, 0, 0]:
        print("需要 --roi x0,y0,x1,y1（格子网格的外框）", file=sys.stderr)
        return 2
    tiles = [float(x) for x in a.tiles.split(",") if x.strip()]
    out = measure(a.image, a.trace, roi, tiles, a.refine_tile or None)
    txt = json.dumps(out, ensure_ascii=False, indent=1, sort_keys=True)
    if a.out:
        Path(a.out).write_bytes((txt + "\n").encode("utf-8"))
        print("wrote", a.out)
    else:
        print(txt)
    return 0


if __name__ == "__main__":
    sys.exit(main())
