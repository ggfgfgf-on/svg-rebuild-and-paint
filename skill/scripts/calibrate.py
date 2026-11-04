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


def _json_path(a):
    """工程级隔离：给了 --json 就只读写那个文件，不动技能自带的默认值。"""
    p = getattr(a, "json", "") or ""
    return Path(p) if p else JSON_PATH


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



def load_samples(path):
    """多图样本：每个样本 = 一张图 + 一份人工描边（+ 可选 cells / 种子掩膜）。

    这是"阈值该由多张图支撑"的接口；样本列表用 JSON 给：
        [{"image": "a.png", "trace": "a_mask.json", "cells": "a_windows.json", "base_mask": "a_seed.json"}, ...]
    """
    data = _load_json(path)
    if isinstance(data, dict):
        data = data.get("samples") or [data]
    out = []
    for i, s in enumerate(data):
        for k in ("image", "trace"):
            if k not in s:
                raise ValueError("样本 %d 缺 %r" % (i, k))
        out.append(s)
    return out


def _numeric(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def sweep_multi(param, samples, values):
    """对每个样本各跑一遍判定，取可接受区间的交集。

    返回 {"per_sample": {...}, "intersection": [lo, hi], "values": [...]}：
      edge_min_contrast：每样本回推 (max(应拒) + eps, min(应放行) - eps)
      accept_max_area_change：每样本回推 (正常最大 delta, 失控最小 delta)
    交集为空说明这些样本互相矛盾——这时候不该硬选一个数，而要先看是哪个样本的判据错了。
    """
    per = {}
    lo_hi = []
    for s in samples:
        name = Path(s["image"]).name
        rows = {}
        if param == "edge_min_contrast":
            import numpy as np
            import cv2 as cv
            import pick_instance as PI
            import masks as M
            from PIL import Image
            w, h = Image.open(s["image"]).size
            gt = PI.rasterize(M.from_json(_load_json(s["trace"])), w, h)
            wins = _cells_windows(s["cells"]) if s.get("cells") else None
            accurate = PI.rasterize(M.from_json(_load_json(s["base_mask"])), w, h) if s.get("base_mask") else gt
            samples_mask = [("accurate", accurate, True),
                            ("fat_3px", cv.dilate(accurate.astype(np.uint8), np.ones((7, 7), np.uint8)) > 0, False)]
            if wins:
                cells_seed = PI.cells_mask(s["image"], wins)
                refined, _i = PI.refine_loop(s["image"], wins, init_mask=cells_seed,
                                             anchors=PI.cells_anchors(s["image"], wins), iters=8, verbose=False)
                samples_mask += [("cells_seed", cells_seed, False), ("refined", refined, False)]
            acc, rej = [], []
            for nm, m, should_pass in samples_mask:
                er = PI.edge_report(s["image"], m, search_px=6)
                ok = bool(er["ok"] and er["d"] == 0)
                rows[nm] = {"contrast": er["contrast"], "d": er["d"], "ok_now": ok, "should_pass": should_pass}
                (acc if should_pass else rej).append(er["contrast"])
            lo = max(rej) + 1e-3 if rej else 0.0
            hi = min(acc) - 1e-3 if acc else 1.0
            if lo < hi:
                lo_hi.append([round(lo, 3), round(hi, 3)])
            rows["interval"] = [round(lo, 3), round(hi, 3)]
        elif param == "accept_max_area_change":
            import pick_instance as PI
            import masks as M
            from PIL import Image
            w, h = Image.open(s["image"]).size
            wins = _cells_windows(s["cells"]) if s.get("cells") else []
            init = PI.rasterize(M.from_json(_load_json(s["base_mask"])), w, h) if s.get("base_mask") else PI.cells_mask(s["image"], wins)
            bits, info = PI.refine_loop(s["image"], wins, init_mask=init, anchors=PI.cells_anchors(s["image"], wins),
                                        iters=8, verbose=False)
            hist = info.get("history") or []
            areas = [float(x["area"]) for x in hist if x.get("area")]
            deltas = [abs(areas[i] - areas[i - 1]) / max(1e-9, areas[i - 1]) for i in range(1, len(areas))]
            normal = max(deltas) if deltas else 0.0
            rows["normal_max_delta"] = round(normal, 4)
            runaway = float(s.get("runaway_delta", 0) or 0)
            if runaway > normal:
                lo_hi.append([round(normal + 1e-3, 3), round(runaway - 1e-3, 3)])
                rows["runaway_delta"] = runaway
            rows["interval"] = [round(normal + 1e-3, 3), round(runaway, 3) if runaway else None]
        per[name] = rows
    lo = max((x[0] for x in lo_hi), default=0.0)
    hi = min((x[1] for x in lo_hi), default=1.0)
    return {"per_sample": per, "intersection": [round(lo, 3), round(hi, 3)] if lo < hi else [],
            "samples": len(samples), "values": [float(v) for v in values]}



def boundary_contrast(image, mask, patch=9):
    """逐边界像素的"峰值显著度"——一张图也能给出几千个样本，不是 1 个聚合数。

    每个边界像素取 patch x patch 邻域，先算 Lab 梯度幅值，再算
        contrast = (本像素梯度 - 邻域均值) / 邻域均值
    即"这一点比周围更像个边"的程度。掩膜边界压在真边上时这个分布重心高；
    边界糊出去时分布整体下移——阈值就该取在两个分布之间，而不是某个聚合数的中点。
    """
    import numpy as np
    import cv2 as cv
    img = cv.imread(image, cv.IMREAD_COLOR)
    lab = cv.cvtColor(img, cv.COLOR_BGR2LAB).astype(np.float32)
    grad = np.zeros(lab.shape[:2], np.float32)
    for ch in range(3):
        c0 = lab[..., ch]
        gx = np.zeros_like(c0); gy = np.zeros_like(c0)
        cv.Sobel(c0, cv.CV_32F, 1, 0, gx, ksize=3)
        cv.Sobel(c0, cv.CV_32F, 0, 1, gy, ksize=3)
        grad += np.sqrt(gx * gx + gy * gy)
    grad /= 3.0
    m = mask.astype(np.uint8)
    er = cv.erode(m, np.ones((3, 3), np.uint8)) > 0
    bd = m.astype(bool) & ~er
    ys, xs = np.nonzero(bd)
    h, w = grad.shape
    r = max(2, patch // 2)
    vals = []
    for y, x in zip(ys, xs):
        y0, y1 = max(0, y - r), min(h, y + r + 1)
        x0, x1 = max(0, x - r), min(w, x + r + 1)
        win = grad[y0:y1, x0:x1]
        mu = float(win.mean()) + 1e-6
        vals.append((float(grad[y, x]) - mu) / mu)
    return np.asarray(vals, np.float32)


def sweep_edge_hist(image, trace, cells, values, base_mask=None):
    """一图也可判：把 accurate / fat_3px / refined 的逐像素分布画在一张图上，模型看分界。"""
    import numpy as np
    import cv2 as cv
    import pick_instance as PI
    import masks as M
    from PIL import Image
    w, h = Image.open(image).size
    gt = PI.rasterize(M.from_json(_load_json(trace)), w, h)
    accurate = PI.rasterize(M.from_json(_load_json(base_mask)), w, h) if base_mask else gt
    fat = cv.dilate(accurate.astype(np.uint8), np.ones((7, 7), np.uint8)) > 0
    dists = {"accurate": boundary_contrast(image, accurate),
             "fat_3px": boundary_contrast(image, fat)}
    rows = {k: {"n": int(v.size), "p10": round(float(np.percentile(v, 10)), 3),
                "median": round(float(np.median(v)), 3), "p90": round(float(np.percentile(v, 90)), 3),
                "mean": round(float(v.mean()), 3)} for k, v in dists.items()}
    lo = float(min(v.min() for v in dists.values())); hi = float(max(v.max() for v in dists.values()))
    lo = max(lo, -1.0); hi = min(hi, 3.0)
    bins = 26
    edges = np.linspace(lo, hi, bins + 1)
    canvas = np.full((280, 900, 3), 255, np.uint8)
    hist_all = {}
    for name, col in (("accurate", (60, 140, 60)), ("fat_3px", (180, 80, 60))):
        hh, _ = np.histogram(dists[name], bins=edges)
        hist_all[name] = hh
        mx = max(1, int(hh.max()))
        for i in range(bins):
            x0 = int(40 + i * 800 / bins) + 2
            x1 = int(40 + (i + 1) * 800 / bins) - 2
            y0 = 250 - int(hh[i] / mx * 220)
            cv.rectangle(canvas, (x0, y0), (x1, 250), col, -1 if name == "accurate" else 1)
    cv.putText(canvas, "green=accurate (fill)  red=fat_3px (outline)", (40, 20), cv.FONT_HERSHEY_SIMPLEX, 0.55, (30, 30, 30), 1)
    for k in values:
        x = int(40 + (float(k) - lo) / max(1e-6, hi - lo) * 800)
        if 40 <= x <= 840:
            cv.line(canvas, (x, 28), (x, 250), (30, 30, 200), 1)
            cv.putText(canvas, ("%.2f" % float(k)), (x - 12, 270), cv.FONT_HERSHEY_SIMPLEX, 0.42, (30, 30, 200), 1)
    for name, v in dists.items():
        rows[name]["share_above_040"] = round(float((v > 0.40).mean()), 3)
    return rows, canvas



def make_samples(folder, out=None):
    """按目录约定给"当前工程"生成样本表——样本是工程数据，不是随技能发布的固定文件。

    配对规则：图片 X.png 与同目录下文件名同时包含 X 主干与 "mask"/"trace" 的 JSON 视为一对；
    配对失败的图片会列出来（不猜），由人补 trace 路径再用 --samples 指过去。
    输出默认写到该目录下的 calib.samples.json，同时建议配上 --json 让 calibrated.json 也落在本工程。
    """
    folder = Path(folder)
    imgs = sorted([p for p in folder.iterdir() if p.suffix.lower() in (".png", ".jpg", ".jpeg")])
    jsons = sorted([p for p in folder.iterdir() if p.suffix.lower() == ".json"])
    samples, unmatched = [], []
    for img in imgs:
        stem = img.stem.lower()
        cands = [j for j in jsons if ("mask" in j.name.lower() or "trace" in j.name.lower())
                 and (stem in j.name.lower() or j.stem.lower().replace("mask_", "") in stem)]
        if not cands:
            unmatched.append(img.name); continue
        samples.append({"image": str(img), "trace": str(cands[0])})
    out = Path(out) if out else folder / "calib.samples.json"
    out.write_bytes(json.dumps({"samples": samples}, ensure_ascii=False, indent=1).encode("utf-8"))
    return samples, unmatched, out



def sweep_ring(image, trace, cells, values, base_mask=None):
    """ring：精修时允许的增长走廊半径（像素）。太大 = 越界，太小 = 收不回来。"""
    import numpy as np
    import pick_instance as PI
    import masks as M
    from PIL import Image
    w, h = Image.open(image).size
    gt = PI.rasterize(M.from_json(_load_json(trace)), w, h)
    wins = _cells_windows(cells)
    init = PI.rasterize(M.from_json(_load_json(base_mask)), w, h) if base_mask else PI.cells_mask(image, wins)
    rows, panels = {}, []
    for v in values:
        bits, info = PI.refine_loop(image, wins, init_mask=init, anchors=PI.cells_anchors(image, wins),
                                    iters=6, ring=float(v), verbose=False)
        er = PI.edge_report(image, bits, search_px=6, min_contrast=0.0)
        rows[str(v)] = {"iou": round(_iou(bits, gt), 3), "area": round(float(bits.mean()), 4),
                        "iters": info.get("iters"), "edge_ok": bool(er["ok"]),
                        "contrast": round(float(er["contrast"]), 3)}
        panels.append(_panel(image, bits, _crop_of(init, pad=30),
                             note="ring=%s iou=%.3f c=%.2f" % (v, rows[str(v)]["iou"], rows[str(v)]["contrast"])))
    return rows, _sheet(panels)


def sweep_attach(image, trace, cells, values, base_mask=None, which="gap_px"):
    """attach_thin 的三条判据：缝隙宽度 gap_px / 可附着的最大细条宽度 / 最大长度。"""
    import numpy as np
    import pick_instance as PI
    import masks as M
    from PIL import Image
    w, h = Image.open(image).size
    gt = PI.rasterize(M.from_json(_load_json(trace)), w, h)
    wins = _cells_windows(cells)
    init = PI.rasterize(M.from_json(_load_json(base_mask)), w, h) if base_mask else PI.cells_mask(image, wins)
    rows, panels = {}, []
    for v in values:
        kw = {"gap_px": 6, "max_width_px": 6, "max_len_px": 90}
        kw[which] = int(float(v))
        m, _ainfo = PI.attach_thin(image, init, **kw)
        rows[str(v)] = {"iou": round(_iou(m, gt), 3), "area": round(float(m.mean()), 4),
                        "grew_px": int(m.sum() - init.sum())}
        panels.append(_panel(image, m, _crop_of(init, pad=30),
                             note="%s=%s iou=%.3f +%dpx" % (which, v, rows[str(v)]["iou"], rows[str(v)]["grew_px"])))
    return rows, _sheet(panels)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--param", default="", )
    ap.add_argument("--image")
    ap.add_argument("--trace")
    ap.add_argument("--cells")
    ap.add_argument("--base-mask", default="")
    ap.add_argument("--make-samples", default="", help="按目录约定生成 <目录>/calib.samples.json（工程样本，不随技能走）")
    ap.add_argument("--samples", default="", help="多图样本 JSON（每项 image/trace[/cells/base_mask]）")
    ap.add_argument("--make-samples-out", default="", help="--make-samples 的输出路径")
    ap.add_argument("--evidence", default="", help="判据来源：multi_sample / per_pixel_hist / contact_sheet；单图阈值不许空着")
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
        ev = (a.evidence or data[a.param].get("evidence") or "").strip()
        if not ev:
            print("%r 标 model 必须给 --evidence（contact_sheet / multi_sample / per_pixel_hist）" % a.param, file=sys.stderr)
            return 2
        data[a.param]["value"] = float(a.choose)
        data[a.param]["chosen_by"] = "model"
        data[a.param]["reason"] = a.reason
        data[a.param]["evidence"] = ev
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

    if a.samples:
        if a.param not in ("edge_min_contrast", "accept_max_area_change"):
            print("多图样本目前只支持 edge_min_contrast / accept_max_area_change", file=sys.stderr); return 2
        vals = [float(x) for x in (a.values or "0").split(",") if x.strip()]
        out = sweep_multi(a.param, load_samples(a.samples), vals)
        data = _load_json(a.json) if Path(a.json).exists() else {}
        prev = data.get(a.param) or {}
        merged = dict(prev)                      # 保留已有的 value/reason/contact_sheet，别整条覆盖
        merged.update({"per_sample": out["per_sample"], "intersection": out["intersection"],
                       "samples": out["samples"], "sweep": out["per_sample"],
                       "chosen_by": prev.get("chosen_by", "unset"),
                       "reason": prev.get("reason", ""),
                       "contact_sheet": prev.get("contact_sheet", ""),
                       "spec": dict(prev.get("spec") or {}, samples_file=Path(a.samples).name)})
        data[a.param] = merged
        Path(a.json).write_bytes((json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True) + "\n").encode("utf-8"))
        for name, rows in out["per_sample"].items():
            print("%-22s %s" % (name, json.dumps(rows, ensure_ascii=False)))
        print("samples=%d  intersection=%s" % (out["samples"], out["intersection"]))
        return 0
    sweeps = {"ring": sweep_ring,
              "attach_gap": lambda i, t, c_, v, b: sweep_attach(i, t, c_, v, b, "gap_px"),
              "attach_width": lambda i, t, c_, v, b: sweep_attach(i, t, c_, v, b, "max_width_px"),
              "attach_len": lambda i, t, c_, v, b: sweep_attach(i, t, c_, v, b, "max_len_px"),
              "edge_hist": sweep_edge_hist,
              "attach_px": sweep_attach_px, "min_ratio": sweep_min_ratio,
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
    if a.make_samples:
        sm, unmatched, out = make_samples(a.make_samples, a.make_samples_out or None)
        print("samples=%d -> %s" % (len(sm), out))
        for u in unmatched:
            print("  未配对（需手填 trace）: %s" % u)
        print("提示：再加 --json <工程目录>/calibrated.json，标定值与样本就都留在工程里")
        return 0
    need_cells = a.param not in ("edge_hist", "edge_min_contrast")
    if not (a.image and a.trace and (a.cells or not need_cells)):
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
                              "cells": Path(a.cells).name if a.cells else "",
                              "base_mask": Path(a.base_mask).name if a.base_mask else "cells"}}
    Path(a.json).write_bytes((json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True) + "\n").encode("utf-8"))
    print("wrote %s and %s" % (a.json, sheet_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
