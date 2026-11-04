"""参数标定：脚本枚举候选 + 渲染接触表，模型看图选定，选择与依据落盘。

    # 枚举 + 取证（写 sweep 和接触表，不写 value）
    python scripts/calibrate.py --param model_grow_px --image <图> --trace <描边> --cells <windows> [--base-mask <种子>]

    # 我看完 docs/calib/<param>.png 后记录选择（必须给理由）
    python scripts/calibrate.py --param model_grow_px --choose 20 --reason "..."

    # 规则型参数（阈值中点之类）由脚本算出并记录，chosen_by=rule
    python scripts/calibrate.py --param edge_min_contrast --record-rule

分工：脚本负责枚举/渲染/量数，模型负责看图拍板；两者都落进 calibrated.json，test_calibrated.py 复验。
"""
import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
JSON_PATH = HERE / "calibrated.json"


def _load_json(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def _cells_windows(p):
    w = _load_json(p)
    return w.get("windows") if isinstance(w, dict) else w


def _iou(a, b):
    import numpy as np
    a = np.asarray(a, bool); b = np.asarray(b, bool)
    return float((a & b).sum()) / max(1.0, float((a | b).sum()))


def _sheet(panels, gap=6):
    import numpy as np
    gap_arr = np.full((panels[0].shape[0], gap, 3), 255, np.uint8)
    out = panels[0]
    for p in panels[1:]:
        out = np.hstack([out, gap_arr, p])
    return out


def _panel(image, mask, crop, extra=None, note=""):
    import cv2 as cv
    import numpy as np
    img = cv.imread(image).copy()
    if extra is not None:
        img[extra[0]] = extra[1]
    img[mask] = (0.35 * img[mask] + 0.65 * np.array([60, 220, 60])).astype(np.uint8)
    x0, y0, x1, y1 = crop
    p = cv.resize(img[y0:y1, x0:x1], None, fx=2.0, fy=2.0, interpolation=cv.INTER_NEAREST)
    cv.putText(p, note, (8, 24), cv.FONT_HERSHEY_SIMPLEX, 0.58, (255, 255, 255), 2)
    return p


def _crop_of(mask, pad=40):
    import numpy as np
    ys, xs = np.where(mask)
    return (max(0, int(xs.min()) - pad), max(0, int(ys.min()) - pad),
            int(xs.max()) + pad, int(ys.max()) + pad)


def _ctx(image, trace, cells, base_mask=None):
    import numpy as np
    import pick_instance as PI
    import masks as M
    from PIL import Image
    w, h = Image.open(image).size
    gt = PI.rasterize(M.from_json(_load_json(trace)), w, h)
    wins = _cells_windows(cells)
    if base_mask:
        base = PI.rasterize(M.from_json(_load_json(base_mask)), w, h)
    else:
        base = PI.cells_mask(image, wins)
    return {"w": w, "h": h, "gt": gt, "wins": wins, "base": base}


def sweep_attach_px(image, trace, cells, values, base_mask=None):
    """attach_px 的真实语义：把某个窗口的格子"附着"到已有 near 掩膜上，distance=v。

    只在给了 near 时生效（cells_mask 里 near is not None 才用 attach_px）。
    所以扫这个参数必须分层：主窗口格 -> near 种子，子窗口格 -> attach_px=v 附着。
    """
    import pick_instance as PI
    import numpy as np
    ctx = _ctx(image, trace, cells)
    wins = ctx["wins"]
    if len(wins) < 2:
        raise ValueError("attach_px 需要至少两个窗口（一个当 near 种子，一个当附着窗口）")
    near = PI.cells_mask(image, [wins[0]])
    crop = _crop_of(ctx["base"])
    rows, panels = {}, []
    for v in values:
        sub = dict(wins[1], attach_px=int(v))
        m = PI.cells_mask(image, [sub], near=near, near_px=int(v)) | near
        iou = _iou(m, ctx["gt"])
        rows[str(int(v))] = {"iou": round(iou, 3), "area": round(float(m.mean()), 4)}
        panels.append(_panel(image, m, crop, note="attach_px=%d  iou=%.3f  area %.4f" % (int(v), iou, m.mean())))
    return rows, _sheet(panels)


def sweep_min_ratio(image, trace, cells, values, base_mask=None):
    import pick_instance as PI
    ctx = _ctx(image, trace, cells, base_mask)
    anchors = PI.cells_anchors(image, ctx["wins"])
    thin, _i = PI.attach_thin(image, ctx["base"], gap_px=30, max_width_px=9, max_len_px=90)
    added = thin & ~ctx["base"]
    crop = _crop_of(ctx["base"])
    rows, panels = {}, []
    for v in values:
        out, rep = PI.filter_added(image, ctx["base"], added, min_ratio=float(v), anchors=anchors)
        iou = _iou(out, ctx["gt"])
        rows[str(v)] = {"iou": round(iou, 3), "kept": int((out & ~ctx["base"]).sum()),
                        "edge": int(rep["edge_px"]), "model": int(rep["model_px"]), "halo": int(rep["halo_px"])}
        panels.append(_panel(image, out, crop, extra=(added & ~out, (0, 0, 255)),
                             note="min_ratio=%.2f iou=%.3f keep %d" % (float(v), iou, (out & ~ctx["base"]).sum())))
    return rows, _sheet(panels)


def sweep_refine(image, trace, cells, values, base_mask=None, param="neg_px"):
    import pick_instance as PI
    ctx = _ctx(image, trace, cells, base_mask)
    anchors = PI.cells_anchors(image, ctx["wins"])
    init = ctx["base"]
    crop = _crop_of(init)
    rows, panels = {}, []
    for v in values:
        kw = {param: float(v) if param in ("neg_k",) else int(v)}
        bits, info = PI.refine_loop(image, ctx["wins"], init_mask=init, anchors=anchors, iters=8,
                                    verbose=False, **kw)
        iou = _iou(bits, ctx["gt"])
        er = PI.edge_report(image, bits, search_px=6)
        rows[str(v)] = {"iou": round(iou, 3), "iters": info.get("iters"), "area": round(float(bits.mean()), 4),
                        "edge_ok": bool(er["ok"]), "contrast": er["contrast"]}
        panels.append(_panel(image, bits, crop, note="%s=%s iou=%.3f edge=%s" %
                             (param, v, iou, "ok" if er["ok"] else "no")))
    return rows, _sheet(panels)


def record_rule(name, value, rule, extra=None):
    data = {}
    p = JSON_PATH
    if p.exists():
        data = _load_json(p)
    e = data.get(name) or {}
    e.update({"value": float(value), "chosen_by": "rule", "rule": rule,
              "reason": rule})
    if extra:
        e.update(extra)
    data[name] = e
    p.write_bytes((json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True) + "\n").encode("utf-8"))
    print("recorded %s = %s (rule)" % (name, value))



def sweep_edge_min_contrast(image, trace, cells, values, base_mask=None):
    """阈值型但让模型判：一屏看"每个阈值下哪些掩膜被接受/拒绝"，人挑分界。

    样本（这是判据该分开的东西）：
      accurate   人工描边/精修后的边界准确掩膜  -> 应 PASS
      fat_3px    把 accurate 故意加粗 3px        -> 应 REJECT（描边 IoU 更高但边界不对）
      cells_seed 模型格子并集（块状）            -> 应 REJECT
      refined    从格子并集精修出来的掩膜        -> 看模型怎么判
    """
    import numpy as np
    import cv2 as cv
    import pick_instance as PI
    import masks as M
    from PIL import Image
    w, h = Image.open(image).size
    gt = PI.rasterize(M.from_json(_load_json(trace)), w, h)
    accurate = PI.rasterize(M.from_json(_load_json(base_mask)), w, h) if base_mask else gt
    wins = _cells_windows(cells)
    cells_seed = PI.cells_mask(image, wins)
    refined, _info = PI.refine_loop(image, wins, init_mask=cells_seed,
                                    anchors=PI.cells_anchors(image, wins), iters=8, verbose=False)
    samples = [("accurate", accurate), ("fat_3px", cv.dilate(accurate.astype(np.uint8), np.ones((7, 7), np.uint8)) > 0),
               ("cells_seed", cells_seed), ("refined", refined)]
    crop = _crop_of(accurate, pad=30)
    rows = {}
    tiles = []
    for name, m in samples:
        er = PI.edge_report(image, m, search_px=6)
        rows[name] = {"contrast": er["contrast"], "d": er["d"], "iou_trace": round(_iou(m, gt), 3),
                      "accept": {str(t): bool(er["contrast"] >= float(t) and er["d"] == 0) for t in values}}
        tiles.append((name, m, er))
    panels = []
    for name, m, er in tiles:
        p = _panel(image, m, crop, note="%s c=%.2f iou=%.3f" % (name, er["contrast"], rows[name]["iou_trace"]))
        panels.append(p)
    sheet = _sheet(panels)
    return rows, sheet


def sweep_accept_max_area_change(image, trace, cells, values, base_mask=None):
    """把迭代过程摊开：每轮的面积变化 + 该轮掩膜，模型挑"回滚门限"。

    判据不是"哪个数最好看"，而是"哪个门限能放行正常收敛、拦住失控跳变"。
    """
    import numpy as np
    import cv2 as cv
    import pick_instance as PI
    import masks as M
    from PIL import Image
    w, h = Image.open(image).size
    gt = PI.rasterize(M.from_json(_load_json(trace)), w, h)
    wins = _cells_windows(cells)
    if base_mask:
        init = PI.rasterize(M.from_json(_load_json(base_mask)), w, h)
    else:
        init = PI.cells_mask(image, wins)
    bits, info = PI.refine_loop(image, wins, init_mask=init, anchors=PI.cells_anchors(image, wins),
                                iters=8, verbose=False)
    hist = info.get("history") or []
    areas = [float(x["area"]) for x in hist if x.get("area")]
    deltas = [abs(areas[i] - areas[i - 1]) / max(1e-9, areas[i - 1]) for i in range(1, len(areas))]
    crop = _crop_of(init, pad=30)
    panels = []
    rows = {"areas": [round(a, 4) for a in areas], "deltas": [round(d, 5) for d in deltas],
            "final_iou": round(_iou(bits, gt), 3), "thresholds": {}}
    for t in values:
        allowed = sum(1 for d in deltas if d <= float(t))
        rows["thresholds"][str(t)] = {"iterations_within": allowed, "iterations_total": len(deltas),
                                      "would_rollback": len(deltas) - allowed}
    for i, a in enumerate(areas):
        d = deltas[i - 1] if i > 0 else 0.0
        p = _panel(image, init if i == 0 else _iter_mask(image, wins, init, i),
                   crop, note="iter %d area=%.4f d=%.4f" % (i, a, d))
        panels.append(p)
    return rows, _sheet(panels)


def _iter_mask(image, wins, init, target_iter):
    """跑 refine_loop 到第 target_iter 轮，返回那一轮的掩膜（用于摊开过程）。"""
    import pick_instance as PI
    bits, info = PI.refine_loop(image, wins, init_mask=init, anchors=PI.cells_anchors(image, wins),
                                iters=int(target_iter), verbose=False)
    return bits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--param", required=True)
    ap.add_argument("--image")
    ap.add_argument("--trace")
    ap.add_argument("--cells")
    ap.add_argument("--base-mask", default="")
    ap.add_argument("--values", default="")
    ap.add_argument("--choose", default="")
    ap.add_argument("--reason", default="")
    ap.add_argument("--record-rule", action="store_true")
    ap.add_argument("--json", default=str(JSON_PATH))
    ap.add_argument("--sheet-dir", default=str(HERE.parent / "docs" / "calib"))
    a = ap.parse_args()
    data = _load_json(a.json) if Path(a.json).exists() else {}

    if a.choose:
        if a.param not in data:
            print("先跑枚举（不带 --choose）", file=sys.stderr); return 2
        if not a.reason.strip():
            print("--choose 必须给 --reason", file=sys.stderr); return 2
        data[a.param]["value"] = float(a.choose)
        data[a.param]["chosen_by"] = "model"
        data[a.param]["reason"] = a.reason
        Path(a.json).write_bytes((json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True) + "\n").encode("utf-8"))
        print("recorded %s = %s (model)" % (a.param, a.choose))
        return 0

    if a.record_rule:
        # 阈值型：用两个已知样本的分离程度定中点，规则固定，人不用猜
        if a.param == "edge_min_contrast":
            import pick_instance as PI
            import masks as M
            import numpy as np
            import cv2 as cv
            w, h = 900, 1100
            good = _load_json(a.base_mask) if a.base_mask else None
            if not good:
                print("需要 --base-mask（边界准确的掩膜）", file=sys.stderr); return 2
            m = PI.rasterize(M.from_json(good), w, h)
            c_good = PI.edge_report(a.image, m, search_px=6)["contrast"]
            fat = cv.dilate(m.astype(np.uint8), np.ones((7, 7), np.uint8)) > 0
            c_fat = PI.edge_report(a.image, fat, search_px=6)["contrast"]
            mid = round((max(c_good, c_fat) + min(c_good, c_fat)) / 2.0, 3)
            record_rule("edge_min_contrast", mid,
                        "两样本中点：边界准确 contrast=%.3f，人为加粗 3px contrast=%.3f" % (c_good, c_fat),
                        {"samples": {"accurate": round(c_good, 3), "fat_3px": round(c_fat, 3)}})
        elif a.param == "accept_max_area_change":
            import pick_instance as PI
            import masks as M
            import numpy as np
            w, h = 900, 1100
            init = PI.rasterize(M.from_json(_load_json(a.base_mask)), w, h)
            wins = _cells_windows(a.cells)
            bits, info = PI.refine_loop(a.image, wins, init_mask=init, anchors=PI.cells_anchors(a.image, wins),
                                        iters=6, verbose=False)
            hist = info.get("history") or []
            areas = [float(h["area"]) for h in hist if h.get("area")]
            deltas = [abs(areas[i] - areas[i - 1]) / max(1e-9, areas[i - 1]) for i in range(1, len(areas))]
            # 只用收敛后的稳态段（最后一轮 + 前两轮），首轮从种子跳到目标不是"循环里该容忍的抖动"
            tail = deltas[-3:] if len(deltas) >= 3 else deltas
            steady = max(tail) if tail else 0.0
            # 用 1 倍稳态值：历史上那次 +4.63% 的失控必须被拦住，取 2 倍就放过去了
            record_rule("accept_max_area_change", max(0.01, round(1 * steady, 3)),
                        "稳态段（最后 3 轮）最大面积变化 %.4f，直接作回滚门限（2 倍会放过历史上 +4.63%% 的失控）；首轮大跳不计入" % steady,
                        {"area_deltas": [round(float(v), 5) for v in deltas],
                         "steady_tail": [round(float(v), 5) for v in tail]})
        else:
            print("没有 %s 的规则型记录器" % a.param, file=sys.stderr); return 2
        return 0

    sweeps = {"attach_px": sweep_attach_px, "min_ratio": sweep_min_ratio,
              "edge_min_contrast": sweep_edge_min_contrast,
              "accept_max_area_change": sweep_accept_max_area_change}
    if a.param in ("neg_px", "neg_k", "max_grow_px"):
        def fn(image, trace, cells, values, base_mask=None):
            return sweep_refine(image, trace, cells, values, base_mask, param=a.param)
        sweeper = fn
    else:
        sweeper = sweeps.get(a.param)
    if not sweeper:
        print("未实现的参数 %r；已实现：attach_px / min_ratio / neg_px / neg_k / max_grow_px" % a.param, file=sys.stderr)
        return 2
    if not (a.image and a.trace and a.cells):
        print("需要 --image --trace --cells", file=sys.stderr); return 2
    vals = [float(x) for x in a.values.split(",") if x.strip()]
    rows, sheet = sweeper(a.image, a.trace, a.cells, vals, a.base_mask or None)
    out_dir = Path(a.sheet_dir); out_dir.mkdir(parents=True, exist_ok=True)
    sheet_path = out_dir / ("%s.png" % a.param)
    import cv2 as cv
    cv.imwrite(str(sheet_path), sheet)
    for k, v in rows.items():
        print("%-14s %s" % (k, json.dumps(v, ensure_ascii=False)))
    data[a.param] = {"sweep": rows, "contact_sheet": str(sheet_path.relative_to(HERE.parent)),
                     "chosen_by": (data.get(a.param) or {}).get("chosen_by", "unset"),
                     "reason": (data.get(a.param) or {}).get("reason", ""),
                     "spec": {"image": Path(a.image).name, "trace": Path(a.trace).name,
                              "cells": Path(a.cells).name,
                              "base_mask": Path(a.base_mask).name if a.base_mask else "cells"}}
    Path(a.json).write_bytes((json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True) + "\n").encode("utf-8"))
    print("wrote %s and %s" % (a.json, sheet_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
