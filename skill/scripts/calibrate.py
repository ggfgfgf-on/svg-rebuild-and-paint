"""参数标定：脚本枚举候选 + 渲染接触表，模型看图选定，选择与依据落盘。

    # 1) 枚举 + 取证（不写 value，只写 sweep 与接触表）
    python scripts/calibrate.py --param model_grow_px --image rose5-full.png \
        --trace mask_leaf_traced.json --cells leaf_windows7.json --values 0,8,14,20,30

    # 2) 模型看完接触表后记录选择
    python scripts/calibrate.py --param model_grow_px --choose 14 \
        --reason "14 时叶缘不虚晕；20 起把背景暗块并进来；8 时叶尖仍缺角"

分工：脚本负责枚举/渲染/量数（sweep），模型负责看图和拍板（value/reason）。
calibrated.json 同时存两者，test_calibrated.py 复验；filter_added 等函数默认值从这里读。
"""
import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
JSON_PATH = HERE / "calibrated.json"


def _cv():
    import cv2
    return cv2


def _load_json(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def _cells_windows(path):
    win = _load_json(path)
    return win.get("windows") if isinstance(win, dict) else win


def sweep_model_grow_px(image, trace, cells, values, base_mask=None):
    import numpy as np
    import cv2 as cv
    import pick_instance as PI
    import masks as M
    from PIL import Image
    w, h = Image.open(image).size
    gt = PI.rasterize(M.from_json(_load_json(trace)), w, h)
    wins = _cells_windows(cells)
    if base_mask:
        # 参数只在"已经有较好种子、新增细结构落在模型报的格子里"时才起作用；
        # 用格子并集当种子时，新增的细像素基本都不在正格里，这参数看不出差别（实测扫描 5 档完全一样）。
        base = PI.rasterize(M.from_json(_load_json(base_mask)), w, h)
    else:
        base = PI.cells_mask(image, wins)
    anchors = PI.cells_anchors(image, wins)
    thin, _info = PI.attach_thin(image, base, gap_px=30, max_width_px=9, max_len_px=90)
    added = thin & ~base
    rows = {}
    crops = []
    for v in values:
        out, rep = PI.filter_added(image, base, added, min_ratio=0.8, anchors=anchors,
                                   keep_model=True, model_grow_px=int(v))
        kept = int((out & ~base).sum())
        dropped = int((added & ~out).sum())
        iou = float((out & gt).sum()) / max(1.0, float((out | gt).sum()))
        rows[str(v)] = {"kept_px": kept, "dropped_px": dropped, "iou": round(iou, 3),
                        "edge": int(rep["edge_px"]), "model": int(rep["model_px"]),
                        "halo": int(rep["halo_px"])}
        img = cv.imread(image).copy()
        img[out & ~base] = (0, 255, 0)
        img[added & ~out] = (0, 0, 255)
        ys, xs = np.where(base)
        x0, x1 = max(0, xs.min() - 40), min(w, xs.max() + 40)
        y0, y1 = max(0, ys.min() - 40), min(h, ys.max() + 40)
        panel = img[y0:y1, x0:x1]
        panel = cv.resize(panel, None, fx=2.0, fy=2.0, interpolation=cv.INTER_NEAREST)
        cv.putText(panel, "grow=%d  iou=%.3f  keep %d / drop %d" % (int(v), iou, kept, dropped),
                   (8, 26), cv.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
        crops.append(panel)
    gap = np.full((crops[0].shape[0], 6, 3), 255, np.uint8)
    sheet = crops[0]
    for c in crops[1:]:
        sheet = np.hstack([sheet, gap, c])
    return rows, sheet


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--param", required=True)
    ap.add_argument("--image")
    ap.add_argument("--trace")
    ap.add_argument("--cells")
    ap.add_argument("--base-mask", default="", help="可选：用这份掩膜当种子（精修过的），而不是格子并集")
    ap.add_argument("--values", default="0,8,14,20,30")
    ap.add_argument("--choose", default="")
    ap.add_argument("--reason", default="")
    ap.add_argument("--json", default=str(JSON_PATH))
    ap.add_argument("--sheet-dir", default=str(HERE.parent / "docs" / "calib"))
    a = ap.parse_args()
    data = {}
    p = Path(a.json)
    if p.exists():
        data = _load_json(p)
    if a.choose:
        if a.param not in data:
            print("先跑枚举（不带 --choose）再选；%s 还没有 sweep" % a.param, file=sys.stderr)
            return 2
        if not a.reason.strip():
            print("--choose 必须同时给 --reason（模型为什么选它）", file=sys.stderr)
            return 2
        data[a.param]["value"] = float(a.choose)
        data[a.param]["chosen_by"] = "model"
        data[a.param]["reason"] = a.reason
        data[a.param]["contact_sheet"] = data[a.param].get("contact_sheet", "")
        p.write_bytes((json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True) + "\n").encode("utf-8"))
        print("recorded %s = %s" % (a.param, a.choose))
        return 0
    if a.param != "model_grow_px":
        print("目前只实现 model_grow_px；其它参数按同一模板加分支", file=sys.stderr)
        return 2
    if not (a.image and a.trace and a.cells):
        print("需要 --image --trace --cells", file=sys.stderr)
        return 2
    vals = [float(x) for x in a.values.split(",") if x.strip()]
    rows, sheet = sweep_model_grow_px(a.image, a.trace, a.cells, vals, a.base_mask or None)
    out_dir = Path(a.sheet_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    sheet_path = out_dir / ("%s.png" % a.param)
    _cv().imwrite(str(sheet_path), sheet)
    for k, v in rows.items():
        print("grow=%3s  keep %5d  drop %5d  iou %.3f  (edge %d / model %d / halo %d)" %
              (k, v["kept_px"], v["dropped_px"], v["iou"], v["edge"], v["model"], v["halo"]))
    data[a.param] = {"sweep": rows, "contact_sheet": str(sheet_path.relative_to(HERE.parent)),
                     "chosen_by": (data.get(a.param) or {}).get("chosen_by", "unset"),
                     "reason": (data.get(a.param) or {}).get("reason", ""),
                     "spec": {"image": Path(a.image).name, "trace": Path(a.trace).name,
                              "cells": Path(a.cells).name,
                              "base_mask": Path(a.base_mask).name if a.base_mask else "cells"}}
    p.write_bytes((json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True) + "\n").encode("utf-8"))
    print("wrote %s and %s" % (p, sheet_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
