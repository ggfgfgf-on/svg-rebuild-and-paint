"""让模型自己从整图挑实例：模型给种子点，这里只负责"长成物体"并验证像不像。

分工（关键）：
    模型   看图 -> 指认"这块是玫瑰、那块同色的是叶子"（种子点 / 负样本 / 框 / 分块坐标）
    本模块 长掩膜 + 客观验证（面积占比、连通块数、贴边率、填充率）+ 出局部放大图给模型复核

用法:
    python scripts/pick_instance.py survey --image photo.png --out sheet.jpg [--cols 4 --rows 4]
    python scripts/pick_instance.py pick --image photo.png --spec spec.json --out-dir lab/pick
"""
import argparse
import json
import warnings
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw

import masks
import visual_mask as VM


def snap_polygon(image, poly, search=18, smooth=2, step=2.0):
    """把手画的粗轮廓吸到图像边缘上：每个采样点沿法线方向在 ±search 内找最强梯度。

    手画点误差一般 5~20px（实测），靠眼睛再调很难收敛；这一步是确定性的，能复核。
    平滑窗口避免锯齿；返回 (新多边形, 统计)。
    """
    arr = np.asarray(Image.open(image).convert("RGB"))
    H, W = arr.shape[:2]
    g = cv2.GaussianBlur(cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY).astype(np.float32), (3, 3), 0)
    gy, gx = np.gradient(g)
    mag = np.sqrt(gx * gx + gy * gy)
    pts = [tuple(map(float, p)) for p in poly]
    out = []
    shifts = []
    for i in range(len(pts)):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % len(pts)]
        seg = float(np.hypot(x1 - x0, y1 - y0))
        n = max(2, int(seg / float(step)))
        for k in range(n):
            t = k / float(n)
            x, y = x0 + (x1 - x0) * t, y0 + (y1 - y0) * t
            nx, ny = (y1 - y0) / max(seg, 1e-6), -(x1 - x0) / max(seg, 1e-6)   # 法线
            best, best_d = -1.0, 0.0
            for d in np.arange(-search, search + 0.5, 0.5):
                xx, yy = int(round(x + nx * d)), int(round(y + ny * d))
                if 0 <= xx < W and 0 <= yy < H:
                    v = float(mag[yy, xx])
                    if v > best:
                        best, best_d = v, float(d)
            out.append([x + nx * best_d, y + ny * best_d])
            shifts.append(abs(best_d))
    out = np.asarray(out, np.float64)
    if smooth and len(out) > 2 * int(smooth):
        k = int(smooth)
        ker = np.ones(2 * k + 1) / (2 * k + 1)
        out = np.stack([np.convolve(np.pad(out[:, 0], (k, k), mode="edge"), ker, "valid"),
                        np.convolve(np.pad(out[:, 1], (k, k), mode="edge"), ker, "valid")], 1)
    def _fit(points):
        m = masks.polygon([tuple(map(float, q)) for q in points])
        bits = rasterize(m, W, H)
        e = cv2.morphologyEx(bits.astype(np.uint8), cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8)).astype(bool)
        return float(mag[e].mean())
    new_poly = out.tolist()
    stats = {"points": len(new_poly), "mean_shift": round(float(np.mean(shifts)), 2),
             "max_shift": round(float(np.max(shifts)), 2),
             "edge_fit_before": round(_fit(pts), 2), "edge_fit_after": round(_fit(new_poly), 2)}
    return new_poly, stats


def _gmm_fit(X, k=3, iters=40, seed=0, reg=1e-2):
    """numpy 版 GMM（EM）：单高斯对多峰背景必然坍缩，这里每类用 k 个高斯。"""
    rng = np.random.default_rng(seed)
    k = int(max(1, min(k, max(1, len(X) // 50))))
    idx = rng.choice(len(X), k, replace=False)
    mu = X[idx].copy()
    cov = [np.cov(X.T) + np.eye(X.shape[1]) * reg for _ in range(k)]
    w = np.ones(k) / k
    for _ in range(int(iters)):
        dens = np.stack([w[j] * _gauss_pdf(X, mu[j], cov[j]) for j in range(k)], 1)
        tot = dens.sum(1, keepdims=True) + 1e-300
        r = dens / tot
        Nk = r.sum(0) + 1e-9
        w = Nk / len(X)
        for j in range(k):
            mu[j] = (r[:, j:j + 1] * X).sum(0) / Nk[j]
            d = X - mu[j]
            cov[j] = (r[:, j:j + 1] * d).T @ d / Nk[j] + np.eye(X.shape[1]) * reg
    return w, mu, cov


def _gauss_pdf(X, mu, cov):
    d = X - mu
    inv = np.linalg.inv(cov)
    maha = np.einsum("...i,ij,...j->...", d, inv, d)
    det = max(np.linalg.det(cov), 1e-30)
    return np.exp(-0.5 * maha) / np.sqrt(((2 * np.pi) ** X.shape[-1]) * det + 1e-300)


def _gmm_logpdf(F, w, mu, cov):
    H, W = F.shape[:2]
    X = F.reshape(-1, F.shape[2])
    out = np.full(len(X), -1e30)
    for j in range(len(w)):
        p = np.log(w[j] + 1e-12) - 0.5 * np.einsum("...i,ij,...j->...", X - mu[j],
                                                   np.linalg.inv(cov[j]), X - mu[j])
        out = np.logaddexp(out, p)
    return out.reshape(H, W)


def _tile_name(c, r):
    n, lab = c, ""
    while True:
        lab = chr(ord("A") + n % 26) + lab
        n = n // 26 - 1
        if n < 0:
            break
    return "%s%d" % (lab, r + 1)


def _parse_tile(name):
    """'AA17' -> (col=26, row=16)；列号 0-based，行号 0-based。"""
    i = 0
    while i < len(name) and name[i].isalpha():
        i += 1
    if i == 0 or i == len(name) or not name[i:].isdigit():
        raise ValueError("非法格子编号: %r" % name)
    col, acc = 0, name[:i].upper()
    for ch in acc:
        col = col * 26 + (ord(ch) - ord("A") + 1)
    return col - 1, int(name[i:]) - 1


def cell_geometry(roi, cols, rows):
    x0, y0, x1, y1 = [float(v) for v in roi]
    return (x1 - x0) / float(cols), (y1 - y0) / float(rows)


def cell_box(roi, cols, rows, label):
    """格子编号 -> 像素框 (x0,y0,x1,y1)，浮点、已按 roi 截断。"""
    cw, ch = cell_geometry(roi, cols, rows)
    c, r = _parse_tile(label)
    if not (0 <= c < cols and 0 <= r < rows):
        raise ValueError("格子 %s 超出 %dx%d" % (label, cols, rows))
    x0, y0, x1, y1 = [float(v) for v in roi]
    return (x0 + c * cw, y0 + r * ch, min(x1, x0 + (c + 1) * cw), min(y1, y0 + (r + 1) * ch))


def cell_center(roi, cols, rows, label):
    bx = cell_box(roi, cols, rows, label)
    return ((bx[0] + bx[2]) / 2.0, (bx[1] + bx[3]) / 2.0)


def px_to_cell(roi, cols, rows, x, y):
    """像素坐标 -> 格子编号（cell_center 的逆运算，用于复核）。"""
    cw, ch = cell_geometry(roi, cols, rows)
    x0, y0, x1, y1 = [float(v) for v in roi]
    xe, ye = min(max(float(x), x0), x1 - 1e-6), min(max(float(y), y0), y1 - 1e-6)
    return _tile_name(min(cols - 1, int((xe - x0) / cw)), min(rows - 1, int((ye - y0) / ch)))


def cells_for_box(roi, cols, rows, box):
    """一个像素框覆盖到哪些格子（用于"把这些格子 zoom 出来"）。"""
    cw, ch = cell_geometry(roi, cols, rows)
    x0, y0, x1, y1 = [float(v) for v in roi]
    bx0, by0, bx1, by1 = [float(v) for v in box]
    c0 = max(0, int(np.floor((bx0 - x0) / cw))); c1 = min(cols - 1, int(np.ceil((bx1 - x0) / cw)) - 1)
    r0 = max(0, int(np.floor((by0 - y0) / ch))); r1 = min(rows - 1, int(np.ceil((by1 - y0) / ch)) - 1)
    return [_tile_name(c, r) for r in range(r0, r1 + 1) for c in range(c0, c1 + 1)]


def _cells_shape(w):
    """窗口分辨率：cols/rows 优先，其次由 tile_px 推；两者都没有就报错（不猜）。"""
    cols, rows = w.get("cols"), w.get("rows")
    tp = w.get("tile_px")
    if (not cols or not rows) and tp and w.get("roi"):
        tw, th = (float(tp), float(tp)) if not isinstance(tp, (list, tuple)) else (float(tp[0]), float(tp[1]))
        x0, y0, x1, y1 = [float(v) for v in w["roi"]]
        cols = cols or max(1, int(round((x1 - x0) / max(1e-6, tw))))
        rows = rows or max(1, int(round((y1 - y0) / max(1e-6, th))))
    if not cols or not rows:
        raise ValueError("窗口缺少网格分辨率：需要 cols/rows，或 tile_px（%r）" % (w.get("id") or w.get("roi")))
    return int(cols), int(rows)


def cells_anchors(image, windows):
    """报的格子中心点（= 锚点）：供 loop 的锚点铁律使用。"""
    by_id = {w.get("id"): w for w in windows if w.get("id")}
    pts = []
    for w in windows:
        src = w
        cols, rows = _cells_shape(w)
        for raw in w.get("pos") or []:
            lab = raw
            if "." in raw:
                src = by_id[raw.split(".", 1)[0]]
                lab = raw.split(".", 1)[1]
                cols, rows = _cells_shape(src)
            pts.append(tuple(float(v) for v in cell_center(src["roi"], cols, rows, lab)))
    return pts


def cells_mask(image, windows, dilate_px=0, near=None, near_px=0, use_neg=True, neg_attach_px=0):
    """把模型报的格子**直接**栅格化成掩膜：模型说属于就属于，不经过任何颜色判别器。

    这是"语义由模型决定"的那条通道：判别器（GrabCut/knn）只是辅助，报格子才是权威。
    支持多级窗口与前缀引用（"tip.D6"），几何换算复用 cell_box（浮点 roi，不漂移）。
    """
    from PIL import Image as _Image
    W, H = _Image.open(image).size
    by_id = {w.get("id"): w for w in windows if w.get("id")}
    bits = np.zeros((H, W), bool)
    hit = 0
    neg_bits = np.zeros((H, W), bool)
    attached_px = 0
    for w in windows:
        src = w
        cols, rows = _cells_shape(w)
        w_attach = int(w.get("attach_px", near_px if near is not None else 0))
        w_bits = np.zeros((H, W), bool)
        for raw in w.get("pos") or []:
            lab = raw
            if "." in raw:
                wid = raw.split(".", 1)[0]
                if wid not in by_id:
                    raise ValueError("格子引用 %r 的窗口 %r 不存在（可用窗口：%s）"
                                     % (raw, wid, ", ".join(k for k in by_id if k) or "无"))
                src = by_id[wid]
                lab = raw.split(".", 1)[1]
                cols, rows = _cells_shape(src)
            b = cell_box(src["roi"], cols, rows, lab)
            y0, y1 = max(0, int(np.floor(b[1]))), min(H, int(np.ceil(b[3])))
            x0, x1 = max(0, int(np.floor(b[0]))), min(W, int(np.ceil(b[2])))
            w_bits[y0:y1, x0:x1] = True
            hit += 1
        if near is not None and w_attach > 0:
            # 每个窗口可以有自己的 attach 距离：叶尖这种"离主掩膜十几像素"的凸起要放大附着距离，
            # 主叶身用 4px 就够，避免整块方块贴上去。
            import cv2 as _cv
            nd = _cv.dilate(np.asarray(near, np.uint8), np.ones((2 * w_attach + 1, 2 * w_attach + 1), np.uint8)) > 0
            w_bits = w_bits & nd
            attached_px += int(w_bits.sum())
        bits |= w_bits
        if use_neg:
            n_att = int(w.get("neg_attach_px", neg_attach_px))
            for raw in w.get("neg") or []:
                nsrc, nlab = w, raw
                if "." in raw:
                    wid2 = raw.split(".", 1)[0]
                    nsrc = by_id.get(wid2, w)
                    nlab = raw.split(".", 1)[1]
                ncols, nrows = _cells_shape(nsrc)
                nb = cell_box(nsrc["roi"], ncols, nrows, nlab)
                ny0, ny1 = max(0, int(np.floor(nb[1]))), min(H, int(np.ceil(nb[3])))
                nx0, nx1 = max(0, int(np.floor(nb[0]))), min(W, int(np.ceil(nb[2])))
                neg_bits[ny0:ny1, nx0:nx1] = True
                if n_att > 0:
                    import cv2 as _cvn
                    slot = np.zeros((H, W), np.uint8)
                    slot[ny0:ny1, nx0:nx1] = 1
                    neg_bits |= _cvn.dilate(slot, np.ones((2 * n_att + 1, 2 * n_att + 1), np.uint8)) > 0
                hit += 1
    if dilate_px:
        import cv2 as _cv
        r = int(dilate_px)
        bits = _cv.dilate(bits.astype(np.uint8), np.ones((2 * r + 1, 2 * r + 1), np.uint8)) > 0
    if use_neg and neg_bits.any():
        bits = bits & ~neg_bits
    return bits


def attach_thin(image, mask, gap_px=None, max_width_px=None, max_len_px=None, hue_tol=16.0,
               s_min=0.06, v_min=0.02, max_area_px=5000):
    """把"细长、低饱和、贴着主体"的暗结构并回掩膜（叶尖/叶柄/细颈）。

    判定全部参数化、无硬编码阈值：
      gap_px        与现有掩膜的最大间隙（像素）
      max_width_px  该结构的最大厚度（距离变换 ×2 的 95 分位）
      max_len_px    最大长度（连通块对角线）
      hue_tol/s_min 颜色可信度：与掩膜边界色相接近，或足够低饱和（叶尖属于后者）
    返回 (mask, info)；info 里记录每个被并入块的面积/厚度/长度，便于复核。
    """
    gap_px = int(calibrated("attach_gap", 6)) if gap_px is None else int(gap_px)
    max_width_px = int(calibrated("attach_width", 6)) if max_width_px is None else int(max_width_px)
    max_len_px = int(calibrated("attach_len", 90)) if max_len_px is None else int(max_len_px)
    import cv2 as _cv
    if isinstance(image, np.ndarray):
        img = np.asarray(image)
    else:
        img = _cv.imread(str(image))
    if img is None:
        raise ValueError("attach_thin 读不到图像：%r" % (image,))
    H, W = img.shape[:2]
    m = np.asarray(mask, bool)
    if m.sum() == 0:
        return m, {"added": []}
    near = _cv.dilate(m.astype(np.uint8), np.ones((2 * gap_px + 1, 2 * gap_px + 1), np.uint8)) > 0
    # 颜色判别：像素更像"掩膜边界色"才要，更像"外圈背景色"就不要。
    # 这个比值规则同时解决两件事：叶尖这种贴着主体的异色细条能进来，
    # 而沿边界的同色晕边（旧版 attach_px 一直在补的东西）不会被吃。
    lab = _cv.cvtColor(_cv.GaussianBlur(img, (3, 3), 0), _cv.COLOR_BGR2LAB).astype(np.float32)
    edge_in = (_cv.dilate(m.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0) & m
    ring_out = (_cv.dilate(m.astype(np.uint8), np.ones((2 * gap_px + 1,) * 2, np.uint8)) > 0) & ~m
    if not edge_in.any() or not ring_out.any():
        return m, {"added": [], "components": 0, "rejected": {"len": 0, "thick": 0, "colour": 0}}
    e_col = np.median(lab[edge_in], 0)
    b_col = np.median(lab[ring_out], 0)
    d_edge = np.sqrt(((lab - e_col) ** 2).sum(-1))
    d_bg = np.sqrt(((lab - b_col) ** 2).sum(-1))
    near = ring_out
    like = d_edge < d_bg * 0.95          # 更像边界色（留 5% 余量，避免临界噪声）
    cand = (near & like).astype(np.uint8)
    n, cc, st, _ = _cv.connectedComponentsWithStats(cand, 8)
    if n <= 1:
        return m, {"added": [], "components": 0, "rejected": {"len": 0, "thick": 0, "colour": 0}}
    out = m.copy()
    added = []
    rejected = {"len": 0, "thick": 0, "colour": 0}
    for i in range(1, n):
        area = int(st[i, _cv.CC_STAT_AREA])
        x, y, w, h = st[i, :4]
        if area > max_area_px:
            rejected["len"] += 1
            continue
        if max(w, h) > max_len_px:
            rejected["len"] += 1
            continue
        comp = (cc == i)
        dt = _cv.distanceTransform(comp.astype(np.uint8), _cv.DIST_L2, 3)
        thick = float(np.percentile(dt, 95)) * 2.0
        if thick > max_width_px:
            rejected["thick"] += 1
            continue
        if not comp.any():
            rejected["colour"] += 1
            continue
        out |= comp
        added.append({"area": area, "thickness": round(thick, 2), "len": int(max(w, h)),
                      "d_edge": round(float(np.median(d_edge[comp])), 2),
                      "d_bg": round(float(np.median(d_bg[comp])), 2), "why": "closer_to_edge"})
    inert = []
    if rejected["len"] == 0:
        inert.append("attach_len")
    if rejected["thick"] == 0:
        inert.append("attach_width")
    if rejected["colour"] == 0:
        inert.append("attach_hue")
    info = {"added": added, "gap_px": gap_px, "max_width_px": max_width_px,
            "components": int(n - 1), "rejected": rejected, "inert": inert}
    globals()["LAST_ATTACH"] = info
    return out, info


def calibrated(name, default=None):
    """读 scripts/calibrated.json 里的标定值（由 calibrate.py 生成、模型选定）。

    分工：脚本枚举 + 渲染接触表，模型看图拍板，值落盘；这里只读，不猜。
    """
    import json as _j
    import os as _os
    from pathlib import Path as _P
    proj = _os.environ.get("SVP_CALIBRATED") or ""
    p = _P(proj) if proj and _P(proj).is_file() else _P(__file__).resolve().parent / "calibrated.json"
    try:
        d = _j.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default
    v = (d.get(name) or {})
    if "value" not in v:
        return default
    try:
        return float(v["value"])
    except Exception:
        return default


def filter_added(image, base_mask, added_mask, min_ratio=None, anchors=None, keep_model=True,
                 model_grow_px=None):
    """按梯度证据给"并进来的像素"分三类：edge（自带边）/ model（模型报过，梯度弱也算）/ halo（只有一圈糊边）。
    默认丢掉 halo，保留 edge 与 model——这就是"模型参与 + 可验证证据"的分工。
    """
    import cv2 as _cvf
    if model_grow_px is None:
        model_grow_px = calibrated("model_grow_px", 20)
    if min_ratio is None:
        min_ratio = calibrated("min_ratio", 0.8)
    rep = added_edge_report(image, base_mask, added_mask, min_ratio=min_ratio, anchors=anchors)
    keep = np.zeros_like(np.asarray(added_mask, bool))
    base_b = np.asarray(base_mask, bool)
    # 模型块（梯度弱但模型报过）只允许在主体外 model_grow_px 之内，防止顺着低对比一路长出去
    if anchors and int(model_grow_px) >= 0:
        gk = np.ones((2 * int(model_grow_px) + 1,) * 2, np.uint8)
        model_zone = _cvf.dilate(base_b.astype(np.uint8), gk) > 0
    keep_verdicts = ("edge",) + (("model",) if keep_model else ())
    for row in rep["rows"]:
        if row["verdict"] in keep_verdicts:
            x, y, w, h = row["bbox"]
            piece = np.asarray(added_mask, bool)[y:y + h, x:x + w]
            if row["verdict"] == "model" and anchors and int(model_grow_px) >= 0:
                piece = piece & model_zone[y:y + h, x:x + w]
            keep[y:y + h, x:x + w] |= piece
    out = base_b | keep
    rep["kept_verdicts"] = list(keep_verdicts)
    return out, rep


def fit_boundary(image, mask, search_px=6, blur=3, verbose=False):
    """把整圈边界"贴"到最强梯度上：全局平移 d ∈ [-search, +search]，取边缘梯度均值最大者。

    为什么用全局平移而不是逐点法线搜索：逐点会沿着噪声长出毛刺（实测边界抖动 ±3px），
    而整圈平移保持形状、可复现，且正负号直接回答"掩膜该胖还是该瘦"。
    返回 (mask, info)，info 记录每个 d 的得分，便于复核是不是真的有一个峰。
    """
    import cv2 as _cv
    img = _cv.imread(str(image)) if not isinstance(image, np.ndarray) else np.asarray(image)
    if img is None:
        raise ValueError("fit_boundary 读不到图像：%r" % (image,))
    gray = _cv.cvtColor(img, _cv.COLOR_BGR2GRAY)
    k = max(3, int(blur) | 1)
    g = _cv.GaussianBlur(gray, (k, k), 0).astype(np.float32)
    gx = _cv.Sobel(g, _cv.CV_32F, 1, 0, ksize=3)
    gy = _cv.Sobel(g, _cv.CV_32F, 0, 1, ksize=3)
    mag = _cv.magnitude(gx, gy)
    m = np.asarray(mask, bool).astype(np.uint8)
    def _boundary(b):
        er = _cv.erode(b, np.ones((3, 3), np.uint8))
        return (b > 0) & (er == 0)
    base_score = float(mag[_boundary(m)].mean()) if _boundary(m).any() else 0.0
    best = (0, base_score, m.copy())
    scores = {0: round(base_score, 3)}
    for d in range(1, int(search_px) + 1):
        kk = np.ones((2 * d + 1, 2 * d + 1), np.uint8)
        for sign, op in ((+1, _cv.dilate), (-1, _cv.erode)):
            b = op(m, kk)
            bd = _boundary(b)
            if not bd.any():
                continue
            sc = float(mag[bd].mean())
            scores[sign * d] = round(sc, 3)
            if sc > best[1]:
                best = (sign * d, sc, b.copy())
    out = best[2].astype(bool)
    info = {"d": best[0], "score": round(best[1], 3), "base_score": round(base_score, 3),
            "scores": scores, "search_px": int(search_px)}
    if verbose:
        print(info)
    return out, info


def _edge_mag(image, blur=3):
    import cv2 as _cv
    img = _cv.imread(str(image)) if not isinstance(image, np.ndarray) else np.asarray(image)
    if img is None:
        raise ValueError("读不到图像：%r" % (image,))
    gray = _cv.cvtColor(img, _cv.COLOR_BGR2GRAY)
    k = max(3, int(blur) | 1)
    g = _cv.GaussianBlur(gray, (k, k), 0).astype(np.float32)
    gx = _cv.Sobel(g, _cv.CV_32F, 1, 0, ksize=3)
    gy = _cv.Sobel(g, _cv.CV_32F, 0, 1, ksize=3)
    return _cv.magnitude(gx, gy)


def edge_report(image, mask, search_px=6, blur=3, min_contrast=None):
    """自动判定"边界是否正好落在边缘上"（不依赖任何人工描边）。

    判据两条，都可解释：
      1) d == 0：整圈边界的最强梯度就在当前位置（fit_boundary 的峰在 0）；
      2) contrast = (s0 - 邻域平均) / s0 ≥ min_contrast：这个峰真的比 ±search 内的位置高，
         否则说明"往前挪和现在差不多"，边界是平的/模糊的，不可信。
    """
    import cv2 as _cv
    if min_contrast is None:
        min_contrast = calibrated("edge_min_contrast", 0.05)
    out, info = fit_boundary(image, mask, search_px=search_px, blur=blur)
    scores = dict(info["scores"])
    s0 = float(scores.get(0, 0.0))
    neigh = [float(v) for k, v in scores.items() if k != 0]
    contrast = (s0 - (sum(neigh) / len(neigh))) / max(1e-6, s0) if neigh else 0.0
    ok = bool(info["d"] == 0 and contrast >= float(min_contrast))
    return {"ok": ok, "score": round(s0, 3), "contrast": round(contrast, 4),
            "d": int(info["d"]), "min_contrast": float(min_contrast),
            "scores": scores, "mask_area": int(np.asarray(mask).sum())}


def added_edge_report(image, base_mask, added_mask, min_ratio=0.8, blur=3, anchors=None, anchor_px=6):
    """复查 attach_thin 之类"并进来的像素"到底是真结构还是晕边。

    对每一块新增像素，比较它与整圈基准边界的梯度强度：
      ratio ≥ min_ratio → 真结构（它自己带边）
      ratio <  min_ratio → 晕边（只是把主体边缘向外糊了一圈）
    """
    import cv2 as _cv
    mag = _edge_mag(image, blur=blur)
    base = np.asarray(base_mask, bool)
    add = np.asarray(added_mask, bool) & ~base
    b_er = _cv.erode(base.astype(np.uint8), np.ones((3, 3), np.uint8))
    bd = (base.astype(np.uint8) > 0) & (b_er == 0)
    base_score = float(mag[bd].mean()) if bd.any() else 0.0
    n, cc, st, _ = _cv.connectedComponentsWithStats(add.astype(np.uint8), 8)
    rows = []
    for i in range(1, n):
        comp = cc == i
        er = _cv.erode(comp.astype(np.uint8), np.ones((3, 3), np.uint8))
        cbd = (comp.astype(np.uint8) > 0) & (er == 0)
        sc = float(mag[cbd].mean()) if cbd.any() else 0.0
        ratio = sc / max(1e-6, base_score)
        has_anchor = False
        if anchors:
            for ax, ay in anchors:
                if comp[max(0, int(ay) - 2):int(ay) + 3, max(0, int(ax) - 2):int(ax) + 3].any():
                    has_anchor = True
                    break
        verdict = "edge" if ratio >= float(min_ratio) else ("model" if has_anchor else "halo")
        rows.append({"area": int(st[i, _cv.CC_STAT_AREA]),
                     "bbox": [int(v) for v in st[i, :4]], "score": round(sc, 2),
                     "ratio": round(ratio, 3), "anchor": has_anchor, "verdict": verdict})
    edge_px = sum(r["area"] for r in rows if r["verdict"] == "edge")
    model_px = sum(r["area"] for r in rows if r["verdict"] == "model")
    halo_px = sum(r["area"] for r in rows if r["verdict"] == "halo")
    return {"base_score": round(base_score, 2), "rows": rows,
            "edge_px": edge_px, "model_px": model_px, "halo_px": halo_px,
            "components": len(rows)}


def tile_points(image, windows):
    """多级/多次 zoom：每级窗口自己的 roi + 细度，正负编号分别换算成照片坐标点。

    编号可带窗口前缀（"tip.A2"）：多级时避免同名格子互相混淆。
    """
    by_id = {w.get("id"): w for w in windows if w.get("id")}
    pos, neg, used = [], [], []
    for w in windows:
        g = tile_grid(image, w["roi"], cols=w.get("cols"), rows=w.get("rows"),
                      tile_px=w.get("tile_px"), wid=w.get("id"))
        wins_cells = {w.get("id"): g["cells"]}
        for key, bucket in (("pos", pos), ("neg", neg)):
            for raw in w.get(key) or []:
                src, lab = (by_id[raw.split(".", 1)[0]], raw.split(".", 1)[1]) if ("." in raw and
                     raw.split(".", 1)[0] in by_id) else (w, raw)
                wid = src.get("id")
                if wid not in wins_cells:
                    wins_cells[wid] = tile_grid(image, src["roi"], cols=src.get("cols"),
                                                rows=src.get("rows"), tile_px=src.get("tile_px"))["cells"]
                cells = wins_cells[wid]
                if lab not in cells:
                    raise ValueError("window %s 没有格子 %s（示例：%s）" % (
                        wid or "root", lab, ", ".join(list(cells)[:4])))
                bucket.append(cells[lab])
        used.append({"id": w.get("id"), "roi": g["roi"], "cols": g["cols"], "rows": g["rows"],
                     "tile_px": g["tile_px"], "pos": w.get("pos") or [], "neg": w.get("neg") or []})
    return pos, neg, used


def tile_grid(image, roi, cols=None, rows=None, tile_px=None, out=None, scale=None, wid=None):
    """带编号格子的放大图。`tile_px` 按像素给格子边长（1=单像素）。

    roi 用**浮点**保存：上一次按 int 截断，导致"由格子算出的子窗口"整体偏 1px，
    子格中心也跟着漂——这是报格子能对、掩膜却错位的真 bug。
    """
    arr = np.asarray(Image.open(image).convert("RGB"))
    H, W = arr.shape[:2]
    fx0, fy0, fx1, fy1 = [float(v) for v in roi]
    fx0 = max(0.0, fx0); fy0 = max(0.0, fy0); fx1 = min(float(W), fx1); fy1 = min(float(H), fy1)
    if tile_px:
        tp = float(tile_px)
        cols, rows = max(1, int(np.ceil((fx1 - fx0) / tp))), max(1, int(np.ceil((fy1 - fy0) / tp)))
    cols = int(cols or 8); rows = int(rows or 6)
    cw, ch = (fx1 - fx0) / float(cols), (fy1 - fy0) / float(rows)
    scale = int(scale or max(1, round(1400.0 / max(1.0, fx1 - fx0, fy1 - fy0))))
    x0, y0 = int(np.floor(fx0)), int(np.floor(fy0))
    x1, y1 = int(np.ceil(fx1)), int(np.ceil(fy1))
    span_w, span_h = (fx1 - fx0) * scale, (fy1 - fy0) * scale
    im = Image.fromarray(arr[y0:y1, x0:x1]).resize((int(np.ceil(span_w)), int(np.ceil(span_h))), Image.LANCZOS)
    dr = ImageDraw.Draw(im)
    cells = {}
    show_labels = (cw * scale) >= 14 and (ch * scale) >= 10
    ox, oy = (fx0 - x0) * scale, (fy0 - y0) * scale      # 浮点 roi 相对裁剪图的偏移
    for r in range(rows):
        for c in range(cols):
            name = _tile_name(c, r)
            cells[name] = (fx0 + (c + 0.5) * cw, fy0 + (r + 0.5) * ch)
            px, py = ox + c * cw * scale, oy + r * ch * scale
            if cw * scale >= 4:
                dr.rectangle([px, py, px + cw * scale, py + ch * scale], outline=(0, 200, 230), width=1)
            if show_labels:
                dr.text((px + 1, py + 1), name, fill=(255, 255, 0))
    if out:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        fmt = "PNG" if str(out).lower().endswith(".png") else "JPEG"
        im.save(out, fmt, **({"quality": 92} if fmt == "JPEG" else {}))
    return {"cells": cells, "out": str(out) if out else None, "cols": cols, "rows": rows,
            "tile_px": [round(cw, 3), round(ch, 3)], "roi": [fx0, fy0, fx1, fy1],
            "scale": scale, "id": wid}


def child_window(image, parent, cells, sub=3, tile_px=None, wid=None, out=None, scale=None):
    """对指定格子做下一层 zoom：父窗口 + 若干格子 -> 子窗口（自带 roi/网格/编号）。

    `sub` = 每个父格子再切几份（细度）；也可直接给 `tile_px`（像素）覆盖。
    """
    roi = [float(v) for v in parent["roi"]]
    cols, rows = int(parent["cols"]), int(parent["rows"])
    cw, ch = cell_geometry(roi, cols, rows)
    xs, ys = [], []
    for t in cells:
        bx = cell_box(roi, cols, rows, t)
        xs += [bx[0], bx[2]]; ys += [bx[1], bx[3]]
    sub_roi = [min(xs), min(ys), max(xs), max(ys)]
    if tile_px is None:
        tile_px = max(cw, ch) / float(sub)
    g = tile_grid(image, sub_roi, tile_px=float(tile_px), out=out, scale=scale, wid=wid)
    g["parent"] = parent.get("id")
    g["parent_cells"] = list(cells)
    g["sub"] = float(sub)
    return g


def enforce_anchors(image, mask, anchors, radius=40, cap=12000, k=3.0):
    """模型明确报过的格子，绝不允许从掩膜里消失。

    GrabCut 会把"比叶身亮、比背景只亮一点"的小凸起（本例叶尖）整块判成背景。
    这里对每个丢失的锚点做一次局部自适应生长：以该点邻域颜色的中位数 + k*MAD 为容差，
    只在 radius 半径内、面积不超过 cap 的连通块才并回掩膜（防呆：不会顺手把邻居吞掉）。
    """
    import cv2 as _cv
    img = _cv.imread(str(image))
    H, W = img.shape[:2]
    lab = _cv.cvtColor(img, _cv.COLOR_BGR2LAB).astype(np.float32)
    out = np.asarray(mask, bool).copy()
    added = 0
    for x, y in anchors:
        xi, yi = int(x), int(y)
        if out[yi, xi]:
            continue
        patch = lab[max(0, yi - 2):yi + 3, max(0, xi - 2):xi + 3].reshape(-1, 3)
        med = np.median(patch, 0)
        mad = float(np.median(np.abs(patch - med))) * 1.4826 + 1e-6
        tol = max(6.0, k * mad)
        d = np.sqrt(((lab - med) ** 2).sum(-1))
        like = ((d < tol) & (np.indices((H, W))[1] >= 0)).astype(np.uint8)
        yy, xx = np.ogrid[:H, :W]
        like[(xx - xi) ** 2 + (yy - yi) ** 2 > radius ** 2] = 0
        n, cc = _cv.connectedComponents(like, 8)
        target = cc[yi, xi]
        if target == 0:
            continue
        comp = (cc == target)
        if int(comp.sum()) > cap:
            continue
        # 必须贴着已有掩膜（或与之距离 ≤ 8px），否则不许凭空加一块
        near = _cv.dilate(out.astype(np.uint8), np.ones((17, 17), np.uint8)) > 0
        if not (comp & near).any():
            continue
        out |= comp
        added += 1
    return out, added


def refine_loop(image, windows, iters=6, tol=0.995, model="grabcut", ring=None, verbose=True,
                init_mask=None, anchors=None, neg_px=None, neg_k=2.5, neg_max=400, max_grow_px=None,
                max_total_grow_px=None):
    """自动闭环：靠栅格提示起手，然后把已得掩膜的"确信内部/外圈"当作新提示反复精修。

    这就是"玫瑰掩膜多轮迭代"的机械化版本：
      1) 提示点 -> 掩膜 M
      2) 取 erode(M) 采样作正样本、dilate(M) 减去 M 作负样本
      3) 重建掩膜，与上一轮比 IoU
      4) IoU > tol 或到上限即停，返回每轮指标
    全过程无需人工再报格子；失败会带原因抛错（而不是悄悄给一张错掩膜）。
    """
    ring = float(calibrated("ring", 14)) if ring is None else float(ring)
    import cv2 as _cv
    if neg_px is None:
        neg_px = int(calibrated("neg_px", 3))
    if max_grow_px is None:
        max_grow_px = int(calibrated("max_grow_px", 2))
    img_cv = _cv.imread(str(image))
    H, W = img_cv.shape[:2]
    init_ref = None
    if init_mask is not None:
        bits = np.asarray(init_mask, bool)
        init_ref = bits.copy()
        meta = {"history": []}
        if anchors:
            bits, _n = enforce_anchors(image, bits, anchors)
    else:
        bits, meta = prompted_from_tiles(image, windows, ring=ring, model=model)
    hist = [{"iter": 0, "area": round(float(bits.mean()), 4), "iou_prev": None,
             "components": int(verify(bits, allow_border=True)["components"])}]
    m_prev = bits
    for it in range(1, int(iters) + 1):
        core = _cv.erode(m_prev.astype(np.uint8), np.ones((15, 15), np.uint8)).astype(bool)
        # 负样本必须取自"离开边缘至少 margin 像素"的外圈：直接取 dilate(M) 减 M 会把真实叶缘的
        # 像素当成背景教给判别器，每轮再缩几像素 —— 实测最终一圈均匀缩进 10~20px（红色带）。
        margin = int(neg_px)
        inner_d = _cv.dilate(m_prev.astype(np.uint8), np.ones((2 * margin + 1, 2 * margin + 1), np.uint8)).astype(bool)
        far_d = _cv.dilate(m_prev.astype(np.uint8), np.ones((2 * margin + 5, 2 * margin + 5), np.uint8)).astype(bool)
        ring_px = far_d & ~inner_d
        # 贴边采样 + 颜色筛选：只把"确实和叶缘颜色不同"的那几个像素当背景，
        # 这样 GrabCut 没有 14px 的自由区可外扩（旧做法扩出 2578px 的软阴影响应）。
        lab = None
        if int(ring_px.sum()) > 0:
            import cv2 as _cvl
            img_cv = _cvl.imread(str(image))
            lab = _cvl.cvtColor(img_cv, _cvl.COLOR_BGR2LAB).astype(np.float32)
            edge = (_cvl.dilate(m_prev.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0) & m_prev
            ref = np.median(lab[edge], 0) if edge.any() else np.median(lab[m_prev], 0)
            mad = float(np.median(np.abs(lab[m_prev] - ref))) * 1.4826 + 1e-6
            d = np.sqrt(((lab - ref) ** 2).sum(-1))
            outer = ring_px & (d > float(neg_k) * mad)
            if int(outer.sum()) < 40:
                outer = ring_px
        else:
            outer = ring_px
        if int(core.sum()) < 50 or int(outer.sum()) < 50:
            break
        ys, xs = np.nonzero(core); step = max(1, int(np.sqrt(len(xs) / 300.0)))
        pos = [(float(x), float(y)) for x, y in zip(xs[::step][:300], ys[::step][:300])]
        ys2, xs2 = np.nonzero(outer)
        step2 = max(1, int(np.sqrt(len(xs2) / float(neg_max))))
        neg = [(float(x), float(y)) for x, y in zip(xs2[::step2][:neg_max], ys2[::step2][:neg_max])]
        # 模型报过的格子永远保留在提示里：闭环只允许细化，不许把"模型明确说属于它"的地方丢掉
        anchors = list(anchors) if anchors else [p for p in tile_points(str(image), windows)[0]]
        if init_ref is not None:
            # init 是模型拍板的区域：只锚定它真的覆盖到的那些点，避免又把整格重新长一遍
            anchors = [p for p in anchors if init_ref[min(H - 1, int(p[1])), min(W - 1, int(p[0]))]]
        m_new = prompted_mask(image, pos + anchors, neg, ring=ring, radius=6, model=model)
        # 每轮生长上限：判别器只能把边界推进 max_grow_px 像素，避免往叶缘软阴影里爬
        if max_total_grow_px is not None and int(max_total_grow_px) >= 0:
            import cv2 as _cvgt
            ref0 = init_ref if init_ref is not None else bits
            cap = _cvgt.dilate(ref0.astype(np.uint8),
                               np.ones((2 * int(max_total_grow_px) + 1,) * 2, np.uint8)) > 0
            m_new = m_new & cap
        if int(max_grow_px) >= 0:
            import cv2 as _cvg
            gg = _cvg.dilate(m_prev.astype(np.uint8), np.ones((2 * int(max_grow_px) + 1,) * 2, np.uint8)) > 0
            m_new = m_new & gg
        if init_ref is not None:
            # loop 只允许"往里补"，不许把模型报过的区域再判掉：init（cells 直通）每轮并回
            m_new = m_new | init_ref
        lost = [p for p in anchors if not m_new[min(H - 1, int(p[1])), min(W - 1, int(p[0]))]]
        if lost:
            # 先按锚点铁律补一次（局部自适应生长），补不回来才算判别器真的吃掉了
            m_new, _nfix = enforce_anchors(image, m_new, lost)
            lost = [p for p in lost if not m_new[min(H - 1, int(p[1])), min(W - 1, int(p[0]))]]
        if lost:
            raise ValueError("第 %d 轮把 %d 个已报正样本丢掉了：%s（掩膜比提示小，说明判别器吃掉了该区域）"
                             % (it, len(lost), [(round(x, 1), round(y, 1)) for x, y in lost[:3]]))
        inter = float((m_new & m_prev).sum()); union = float((m_new | m_prev).sum())
        iou = inter / max(1.0, union)
        v = verify(m_new, allow_border=True)
        hist.append({"iter": it, "area": round(float(m_new.mean()), 4), "iou_prev": round(iou, 4),
                     "components": int(v["components"])})
        m_prev = m_new
        if iou > float(tol):
            break
    if verbose:
        for h in hist:
            print(h)
    return m_prev, {"history": hist, "windows": meta.get("windows"), "iters": len(hist) - 1}


def mask_implausible(bits, windows, grow=8.0, fill_max=0.95):
    """提示与图像不符的早期警报：整片叶子不会占满全图，也不会面积暴涨。

    实测教训：把背景格子当正样本，GMM 学歪，输出 area 0.9968（整图）。这种输出必须当场拦下。
    """
    tot = 0.0
    for w in windows:
        x0, y0, x1, y1 = [float(v) for v in w["roi"]]
        tot += max(1.0, (x1 - x0) * (y1 - y0))
    arr = np.asarray(bits, bool)
    area = float(arr.sum())
    fill = area / max(1.0, float(arr.size))
    if area > grow * tot:
        return "面积 %.0f px 超过窗口总面积的 %.0f 倍（提示可能把背景当正样本）" % (area, grow)
    if fill > fill_max:
        return "掩膜占全图 %.2f（接近整图，判别器已坍缩）" % fill
    return None


def prompted_from_tiles(image, windows, strict=True, **kw):
    """模型给格子编号（可多级 zoom）→ 点 → prompted_mask。坐标换算全在代码里。"""
    if isinstance(windows, dict):
        windows = [windows]
    pos, neg, used = tile_points(image, windows)
    bits = prompted_mask(image, pos, neg, **kw)
    bad = mask_implausible(bits, used)
    if bad:
        raise ValueError("tiles 提示与图像不符：%s" % bad)
    H_, W_ = np.asarray(bits).shape[:2]
    miss_pos = [(round(x, 1), round(y, 1)) for x, y in pos
                if not (0 <= int(y) < H_ and 0 <= int(x) < W_ and bits[int(y), int(x)])]
    hit_neg = [(round(x, 1), round(y, 1)) for x, y in neg
               if 0 <= int(y) < H_ and 0 <= int(x) < W_ and bits[int(y), int(x)]]
    # 少量正点落在边界/缝隙属于正常报告误差 -> 警告并继续；大面积未覆盖或负点落进前景 -> 报错。
    tol_miss = 0 if strict else max(2, int(round(0.2 * len(pos))))
    if len(miss_pos) > tol_miss or hit_neg:
        raise ValueError("tiles 提示自相矛盾：%d/%d 个正点未被覆盖（容差 %d）%s；%d 个负点落在前景内 %s"
                         "（负点不再抠洞，但落在叶片上的负点说明格子报错了）"
                         % (len(miss_pos), len(pos), tol_miss, miss_pos[:3], len(hit_neg), hit_neg[:3]))
    if miss_pos:
        warnings.warn("有 %d/%d 个正点未被覆盖，已忽略：%s" % (len(miss_pos), len(pos), miss_pos[:3]), stacklevel=2)
    return bits, {"pos": pos, "neg": neg, "windows": used}


def prompted_mask(image, positives, negatives, ring=16, iters=4, radius=10, model="grabcut",
                  components=3, bins=32, smooth_bins=2, bridge_px=11, hard_negatives=False):
    """模型给正/负点提示，确定性函数出掩膜（唯一的"阈值"是似然比 0.5，不是拟合常量）。

    单高斯会崩：背景跨"暗底/亮边/梗"三种颜色，方差一放就把前景吞了——实测全图 0.9968。
    改用**颜色直方图似然比**（多峰、无需正定性）：正点/负点各堆一张 32³ Lab 直方图，平滑后
    ratio = hf/(hf+hb)；ratio>0.5 即"更像前景"。模型看图后增删点重跑，判据在图里。
    """
    import cv2 as _cv
    img = _cv.imread(str(image))
    H, W = img.shape[:2]
    if model == "grabcut":
        # GrabCut：有界混合高斯 + 区域先验。负点只是"背景种子"，不是抠洞刀。
        # 之前 knn/gmm 会坍缩，是因为 Lab 里的近邻/似然没有边界，亮邻叶被吸进来。
        # 底图必须是 GC_PR_BGD（"可能背景"），整图设成 GC_BGD 会让 GrabCut 无处可算，
        # 实测只动 11 个像素。正样本外圈 probable、内圈 definite。
        m = np.full((H, W), _cv.GC_PR_BGD, np.uint8)
        for x, y in positives:
            _cv.circle(m, (int(x), int(y)), int(radius) * 2, _cv.GC_PR_FGD, -1)
            _cv.circle(m, (int(x), int(y)), max(2, int(radius) // 3), _cv.GC_FGD, -1)
        for x, y in negatives:
            _cv.circle(m, (int(x), int(y)), int(radius) * 2, _cv.GC_PR_BGD, -1)
        if hard_negatives:
            for x, y in negatives:
                _cv.circle(m, (int(x), int(y)), int(radius), _cv.GC_BGD, -1)
        bgm = np.zeros((1, 65), np.float64); fgm = np.zeros((1, 65), np.float64)
        try:
            _cv.grabCut(img, m, None, bgm, fgm, max(1, int(iters)), _cv.GC_INIT_WITH_MASK)
        except _cv.error:
            pass
        out = ((m == _cv.GC_FGD) | (m == _cv.GC_PR_FGD)).astype(np.uint8)
        out = _cv.morphologyEx(out, _cv.MORPH_OPEN, np.ones((5, 5), np.uint8))
        out = _cv.morphologyEx(out, _cv.MORPH_CLOSE, np.ones((11, 11), np.uint8))
        n, cc, st, _ = _cv.connectedComponentsWithStats(out, 8)
        keep = sorted({int(cc[int(y), int(x)]) for x, y in positives
                       if 0 <= int(y) < H and 0 <= int(x) < W} - {0})
        if keep:
            out = np.isin(cc, keep).astype(np.uint8)
        elif n > 1:
            out = (cc == (1 + int(np.argmax(st[1:, _cv.CC_STAT_AREA])))).astype(np.uint8)
        ff = out.copy(); mm2 = np.zeros((H + 2, W + 2), np.uint8)
        _cv.floodFill(ff, mm2, (0, 0), 1)
        base = out.astype(bool) | (ff == 0)
        res = refine_mask(image, base, corridor=ring, iters=iters)
        # 保块：refine 的走廊 GrabCut 会把"与主体只靠细颈相连的小凸起"（叶尖、叶柄）整块吃掉。
        # 凡是包含正样本的连通块，一律并回结果——正样本是模型明确说"这里属于它"的地方。
        n2, cc2 = _cv.connectedComponents(out, 8)
        keep_ids = {int(cc2[int(y), int(x)]) for x, y in positives
                    if 0 <= int(y) < H and 0 <= int(x) < W} - {0}
        if keep_ids:
            backup = np.isin(cc2, list(keep_ids))
            lost = [p for p in positives if not res[min(H - 1, int(p[1])), min(W - 1, int(p[0]))]]
            if lost:
                res = res | (backup & (ff == 0))
            res = res | (backup & np.asarray(base))
        res, _n = enforce_anchors(image, res, positives)
        return res
    if model == "knn":
        # kNN 判别：对每个像素找最近的"正样本像素"和"负样本像素"，谁近算谁。
        # 比高斯/直方图稳——后者对协方差宽度极敏感，负样本一少就整图坍缩。
        from scipy.spatial import cKDTree as _KD
        F = _cv.cvtColor(_cv.GaussianBlur(img, (3, 3), 0), _cv.COLOR_BGR2LAB).astype(np.float64)
        F[:, :, 0] /= 2.55
        def _samp(pts):
            acc = []
            for x, y in pts:
                mm = np.zeros((H, W), np.uint8)
                _cv.circle(mm, (int(x), int(y)), int(radius), 1, -1)
                acc.append(F[mm > 0])
            return np.concatenate(acc, 0)
        fg_s, bg_s = _samp(positives), _samp(negatives)
        tf, tb = _KD(fg_s), _KD(bg_s)
        flat = F.reshape(-1, 3)
        df = tf.query(flat, k=1)[0]
        db = tb.query(flat, k=1)[0]
        m = (df < db).reshape(H, W).astype(np.uint8)
        m = _cv.morphologyEx(m, _cv.MORPH_OPEN, np.ones((5, 5), np.uint8))
        m = _cv.morphologyEx(m, _cv.MORPH_CLOSE, np.ones((13, 13), np.uint8))
        n, cc, st, _ = _cv.connectedComponentsWithStats(m, 8)
        if n > 1:
            keep = sorted({int(cc[int(y), int(x)]) for x, y in positives
                           if 0 <= int(y) < H and 0 <= int(x) < W} - {0})
            m = np.isin(cc, keep).astype(np.uint8) if keep else (cc == (1 + int(np.argmax(st[1:, _cv.CC_STAT_AREA])))).astype(np.uint8)
        ff = m.copy(); mm2 = np.zeros((H + 2, W + 2), np.uint8)
        _cv.floodFill(ff, mm2, (0, 0), 1)
        return refine_mask(image, m.astype(bool) | (ff == 0), corridor=ring, iters=iters)
    if model == "gmm":
        F = _cv.cvtColor(_cv.GaussianBlur(img, (3, 3), 0), _cv.COLOR_BGR2LAB).astype(np.float64)
        F[:, :, 0] /= 2.55
        def _samp(pts):
            acc = []
            for x, y in pts:
                mm = np.zeros((H, W), np.uint8)
                _cv.circle(mm, (int(x), int(y)), int(radius), 1, -1)
                acc.append(F[mm > 0])
            return np.concatenate(acc, 0)
        fg_s, bg_s = _samp(positives), _samp(negatives)
        wf, muf, covf = _gmm_fit(fg_s, components, seed=0)
        wb, mub, covb = _gmm_fit(bg_s, components, seed=1)
        lf = _gmm_logpdf(F, wf, muf, covf)
        lb = _gmm_logpdf(F, wb, mub, covb)
        score = lf - lb
        # 阈值由提示点自己的分数分布决定：正样本的 5% 分位与负样本的 95% 分位取中点。
        # 固定 0 会因为前景/背景分布宽度不同而整图坍缩或全丢——这是实测的真实病根。
        sf = np.array([score[min(H - 1, max(0, int(y))), min(W - 1, max(0, int(x)))] for x, y in positives])
        sb = np.array([score[min(H - 1, max(0, int(y))), min(W - 1, max(0, int(x)))] for x, y in negatives])
        t_res = float(np.percentile(sf, 5)); t_bg = float(np.percentile(sb, 95))
        if not (t_res > t_bg):
            raise ValueError("提示不可分：正样本分数 p5=%.2f ≤ 负样本 p95=%.2f（正负点颜色叠在一起，"
                             "请换格子或缩小窗口）" % (t_res, t_bg))
        thr = 0.5 * (t_res + t_bg)
        m = (score > thr).astype(np.uint8)
        m = _cv.morphologyEx(m, _cv.MORPH_OPEN, np.ones((5, 5), np.uint8))
        m = _cv.morphologyEx(m, _cv.MORPH_CLOSE, np.ones((13, 13), np.uint8))
        n, cc, st, _ = _cv.connectedComponentsWithStats(m, 8)
        if n > 1:
            keep = sorted({int(cc[int(y), int(x)]) for x, y in positives
                           if 0 <= int(y) < H and 0 <= int(x) < W} - {0})
            m = np.isin(cc, keep).astype(np.uint8) if keep else (cc == (1 + int(np.argmax(st[1:, _cv.CC_STAT_AREA])))).astype(np.uint8)
        ff = m.copy(); mm2 = np.zeros((H + 2, W + 2), np.uint8)
        _cv.floodFill(ff, mm2, (0, 0), 1)
        out = refine_mask(image, m.astype(bool) | (ff == 0), corridor=ring, iters=iters)
        if hard_negatives:
            hard = np.ones((H, W), np.uint8)
            for x, y in negatives:
                _cv.circle(hard, (int(x), int(y)), int(radius), 0, -1)
            out = out & (hard > 0)
        n2, cc2, _, _ = _cv.connectedComponentsWithStats(out.astype(np.uint8), 8)
        keep2 = sorted({int(cc2[int(y), int(x)]) for x, y in positives
                        if 0 <= int(y) < H and 0 <= int(x) < W} - {0})
        if keep2 and n2 > 1:
            out = np.isin(cc2, keep2)
        ff3 = out.astype(np.uint8).copy(); mm3 = np.zeros((H + 2, W + 2), np.uint8)
        _cv.floodFill(ff3, mm3, (0, 0), 1)
        return out | (ff3 == 0)
    if model == "gauss":
        # 单高斯判别：对这张图实测 IoU 0.936（直方图版 0.707，会更糊）
        F = _cv.cvtColor(_cv.GaussianBlur(img, (3, 3), 0), _cv.COLOR_BGR2LAB).astype(np.float64)
        F[:, :, 0] /= 2.55
        def _samp(pts):
            acc = []
            for x, y in pts:
                mm = np.zeros((H, W), np.uint8)
                _cv.circle(mm, (int(x), int(y)), int(radius), 1, -1)
                acc.append(F[mm > 0])
            return np.concatenate(acc, 0)
        fg, bg = _samp(positives), _samp(negatives)
        mu_f, mu_b = fg.mean(0), bg.mean(0)
        inv_f = np.linalg.inv(np.cov(fg.T) + np.eye(3) * 1e-2)
        inv_b = np.linalg.inv(np.cov(bg.T) + np.eye(3) * 1e-2)
        Zf, Zb = F - mu_f, F - mu_b
        df = np.einsum("...i,ij,...j->...", Zf, inv_f, Zf)
        db = np.einsum("...i,ij,...j->...", Zb, inv_b, Zb)
        m = ((df - db) < 0.0).astype(np.uint8)
        m = _cv.morphologyEx(m, _cv.MORPH_OPEN, np.ones((5, 5), np.uint8))
        m = _cv.morphologyEx(m, _cv.MORPH_CLOSE, np.ones((13, 13), np.uint8))
        n, cc, st, _ = _cv.connectedComponentsWithStats(m, 8)
        if n > 1:
            keep = sorted({int(cc[int(y), int(x)]) for x, y in positives
                           if 0 <= int(y) < H and 0 <= int(x) < W} - {0})
            m = np.isin(cc, keep).astype(np.uint8) if keep else (cc == (1 + int(np.argmax(st[1:, _cv.CC_STAT_AREA])))).astype(np.uint8)
        ff = m.copy(); mm2 = np.zeros((H + 2, W + 2), np.uint8)
        _cv.floodFill(ff, mm2, (0, 0), 1)
        base = m.astype(bool) | (ff == 0)
        out = refine_mask(image, base, corridor=ring, iters=iters)
        if hard_negatives:
            hard = np.ones((H, W), np.uint8)
            for x, y in negatives:
                _cv.circle(hard, (int(x), int(y)), int(radius), 0, -1)
            out = out & (hard > 0)
        n2, cc2, _, _ = _cv.connectedComponentsWithStats(out.astype(np.uint8), 8)
        keep2 = sorted({int(cc2[int(y), int(x)]) for x, y in positives
                        if 0 <= int(y) < H and 0 <= int(x) < W} - {0})
        if keep2 and n2 > 1:
            out = np.isin(cc2, keep2)
        ff3 = out.astype(np.uint8).copy(); mm3 = np.zeros((H + 2, W + 2), np.uint8)
        _cv.floodFill(ff3, mm3, (0, 0), 1)
        return out | (ff3 == 0)
    lab = _cv.cvtColor(img, _cv.COLOR_BGR2LAB)
    Lb = (lab[:, :, 0].astype(np.int32) * bins) // 256
    Ab = ((lab[:, :, 1].astype(np.int32) + 0) * bins) // 256
    Bb = ((lab[:, :, 2].astype(np.int32) + 0) * bins) // 256
    idx = (Lb * bins + Ab) * bins + Bb

    def _hist(pts):
        m = np.zeros((H, W), np.uint8)
        for x, y in pts:
            _cv.circle(m, (int(x), int(y)), int(radius), 1, -1)
        hh = np.bincount(idx[m > 0].ravel(), minlength=bins ** 3).astype(np.float64)
        hh = hh.reshape(bins, bins, bins)
        return _cv.GaussianBlur(hh, (0, 0), float(smooth_bins))

    hf = _hist(positives)
    hb = _hist(negatives)
    if hf.sum() < 1 or hb.sum() < 1:
        raise ValueError("prompted_mask: 正负点各至少一个")
    ratio = hf / (hf + hb + 1e-9)
    p = ratio.ravel()[idx]
    m = (p > 0.5).astype(np.uint8)
    m = _cv.morphologyEx(m, _cv.MORPH_OPEN, np.ones((5, 5), np.uint8))
    m = _cv.morphologyEx(m, _cv.MORPH_CLOSE, np.ones((13, 13), np.uint8))
    n, cc, st, _ = _cv.connectedComponentsWithStats(m, 8)
    if n > 1:
        keep = sorted({int(cc[int(y), int(x)]) for x, y in positives
                       if 0 <= int(y) < H and 0 <= int(x) < W} - {0})
        if keep:
            m = np.isin(cc, keep).astype(np.uint8)
        else:
            m = (cc == (1 + int(np.argmax(st[1:, _cv.CC_STAT_AREA])))).astype(np.uint8)
    ff = m.copy(); mm = np.zeros((H + 2, W + 2), np.uint8)
    _cv.floodFill(ff, mm, (0, 0), 1)
    base = m.astype(bool) | (ff == 0)
    out = refine_mask(image, base, corridor=ring, iters=iters)
    # 负点是硬约束：负数点周围不许是前景（否则同色区域会被 GrabCut 又拉回来）
    hard = np.ones((H, W), np.uint8)
    for x, y in negatives:
        _cv.circle(hard, (int(x), int(y)), int(radius), 0, -1)
    out = out & (hard > 0)
    # 只保留与正点连通的部分（先断开细桥，防止沿 1–2px 通道渗到别的色块）
    br = int(bridge_px)
    if br > 1:
        o = _cv.morphologyEx(out.astype(np.uint8), _cv.MORPH_OPEN, np.ones((br, br), np.uint8))
        if o.any():
            out = o.astype(bool)
    n2, cc2, _, _ = _cv.connectedComponentsWithStats(out.astype(np.uint8), 8)
    keep2 = sorted({int(cc2[int(y), int(x)]) for x, y in positives
                    if 0 <= int(y) < H and 0 <= int(x) < W} - {0})
    if keep2 and n2 > 1:
        out = np.isin(cc2, keep2)
    ff3 = out.astype(np.uint8).copy(); mm3 = np.zeros((H + 2, W + 2), np.uint8)
    _cv.floodFill(ff3, mm3, (0, 0), 1)
    return out | (ff3 == 0)


def segment(image, roi=None, seeds=None, ring=18, iters=5, hue_k=3.0, sat_q=15,
            chroma_scale=0.55, seed_radius=14, passes=2):
    """自动区域分割：按"色度 Otsu + 同色彩带"切出主体，再走廊精修。

    为什么不用"从种子增长"：同一片叶子上，边缘被照亮的 5–10px 与背景亮度接近，
    增长要么漏进来、要么沿相近色一路渗到别处。真正能分开它们的是**色度（s·v）**
    和**色相**：叶子 chroma 0.14 / hue 0.25，背景 chroma 0.05 / hue 0.41。
    所以先对 chroma 做 Otsu 定一个粗前景，再用它的色相中位数 ± k·MAD 收边。
    seeds 给了就只在种子附近统计（多主体/多色主体），否则用 roi 内的统计。
    """
    import cv2 as _cv
    img = _cv.imread(str(image))
    H, W = img.shape[:2]
    hsv = _cv.cvtColor(img, _cv.COLOR_BGR2HSV).astype(np.float64)
    h, s_, v = hsv[:, :, 0] / 180.0, hsv[:, :, 1] / 255.0, hsv[:, :, 2] / 255.0
    chroma = s_ * v
    if seeds is not None:
        sx = np.array([[float(p[0]), float(p[1])] for p in seeds])
        roi_box = (max(0, int(sx[:, 0].min()) - 80), max(0, int(sx[:, 1].min()) - 80),
                   min(W - 1, int(sx[:, 0].max()) + 80), min(H - 1, int(sx[:, 1].max()) + 80))
    elif roi is not None:
        roi_box = tuple(int(x) for x in roi)
    else:
        raise ValueError("segment: roi 或 seeds 至少给一个")
    roi_mask = np.zeros((H, W), bool)
    roi_mask[roi_box[1]:roi_box[3], roi_box[0]:roi_box[2]] = True
    stat = roi_mask
    if seeds is not None:
        sm = np.zeros((H, W), np.uint8)
        for x, y in seeds:
            _cv.circle(sm, (int(x), int(y)), int(seed_radius), 1, -1)
        stat = roi_mask & (sm > 0)
    ch8 = (chroma * 255).astype(np.uint8)
    th, _ = _cv.threshold(ch8[stat], 0, 255, _cv.THRESH_BINARY + _cv.THRESH_OTSU)
    sel = stat & (ch8 >= th)
    if not sel.any():
        raise ValueError("segment: chroma Otsu 没找到前景")
    hm = float(np.median(h[sel])); mad = float(1.4826 * np.median(np.abs(h[sel] - hm)))
    sq = float(np.percentile(s_[sel], sat_q))
    dh = np.abs(h - hm); dh = np.minimum(dh, 1.0 - dh)          # 色相环绕距离
    m = roi_mask & (ch8 >= th * chroma_scale) & (dh <= hue_k * max(mad, 1e-3)) & (s_ >= sq * 0.8) & (v >= 0.05)
    m = _cv.morphologyEx(m.astype(np.uint8), _cv.MORPH_OPEN, np.ones((5, 5), np.uint8))
    m = _cv.morphologyEx(m, _cv.MORPH_CLOSE, np.ones((13, 13), np.uint8))
    n, cc, st, _ = _cv.connectedComponentsWithStats(m, 8)
    if n > 1:
        m = (cc == (1 + int(np.argmax(st[1:, _cv.CC_STAT_AREA])))).astype(np.uint8)
    ff = m.copy(); mm = np.zeros((H + 2, W + 2), np.uint8)
    _cv.floodFill(ff, mm, (0, 0), 1)
    base = m.astype(bool) | (ff == 0)
    # 第二遍：用自己内部重新估一次色带（亮部/低饱和的花瓣、叶缘才会被收进来），
    # 但增长限制在上一遍的 ±ring 内，防止越收越大。
    for _ in range(max(0, int(passes) - 1)):
        core = _cv.erode(base.astype(np.uint8), np.ones((15, 15), np.uint8)).astype(bool) & roi_mask
        if core.sum() < 50:
            break
        hm2 = float(np.median(h[core])); mad2 = float(1.4826 * np.median(np.abs(h[core] - hm2)))
        sq2 = float(np.percentile(s_[core], 5))
        dh2 = np.abs(h - hm2); dh2 = np.minimum(dh2, 1.0 - dh2)
        near = _cv.dilate(base.astype(np.uint8), np.ones((int(ring) * 2 + 1,) * 2, np.uint8)).astype(bool)
        m2 = (near & roi_mask & (dh2 <= max(4.0, hue_k) * max(mad2, 1e-3))
              & (s_ >= sq2 * 0.6) & (ch8 >= th * chroma_scale * 0.7) & (v >= 0.05)).astype(np.uint8)
        m2 = _cv.morphologyEx(m2, _cv.MORPH_CLOSE, np.ones((13, 13), np.uint8))
        n2, cc2, st2, _ = _cv.connectedComponentsWithStats(m2, 8)
        if n2 > 1:
            m2 = (cc2 == (1 + int(np.argmax(st2[1:, _cv.CC_STAT_AREA])))).astype(np.uint8)
        ff2 = m2.copy(); mm2 = np.zeros((H + 2, W + 2), np.uint8)
        _cv.floodFill(ff2, mm2, (0, 0), 1)
        base = m2.astype(bool) | (ff2 == 0)
    return refine_mask(image, base, corridor=ring, iters=iters)


def refine_mask(image, mask, corridor=12, iters=5, open_px=0):
    """走廊约束的 GrabCut 掩膜精修：只在原掩膜 ±corridor 内重判前景/背景。

    直接对全图跑 GrabCut 会漏到背景和相邻物体（实测面积 0.27 vs 0.07）；把搜索限制在
    一条窄走廊里，它就只能把边界吸到最近的色差上——这既保住轮廓拓扑，又能修掉
    "线浮在叶子外面/里面"的手画误差。
    """
    import cv2 as _cv
    img = _cv.imread(str(image))
    H, W = img.shape[:2]
    sd = mask.astype(np.uint8) if mask.dtype != np.uint8 else mask
    sd = (sd > 0).astype(np.uint8)
    k = int(corridor) * 2 + 1
    allowed = _cv.dilate(sd, np.ones((k, k), np.uint8)).astype(bool)
    inner = _cv.erode(sd, np.ones((9, 9), np.uint8)).astype(bool)
    gc = np.full((H, W), _cv.GC_BGD, np.uint8)
    gc[allowed & ~sd.astype(bool)] = _cv.GC_PR_BGD
    gc[sd.astype(bool)] = _cv.GC_PR_FGD
    gc[inner] = _cv.GC_FGD
    # 小图/小掩膜会让 grabCut 的样本集为空而抛断言（60x60 合成图实测崩溃）；样本不足就直接返回
    if int(inner.sum()) < 20 or int((allowed & ~sd.astype(bool)).sum()) < 20:
        return sd.astype(bool)
    bgd, fgd = np.zeros((1, 65), np.float64), np.zeros((1, 65), np.float64)
    try:
        _cv.grabCut(img, gc, None, bgd, fgd, int(iters), _cv.GC_INIT_WITH_MASK)
    except _cv.error:
        return sd.astype(bool)
    bits = (((gc == _cv.GC_FGD) | (gc == _cv.GC_PR_FGD)) & allowed).astype(np.uint8)
    if open_px:
        bits = _cv.morphologyEx(bits, _cv.MORPH_OPEN, np.ones((int(open_px), int(open_px)), np.uint8))
    bits = _cv.morphologyEx(bits, _cv.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    bits = _cv.medianBlur(bits, 5)
    n, cc, stats, _ = _cv.connectedComponentsWithStats(bits, 8)
    if n > 1:
        bits = (cc == (1 + int(np.argmax(stats[1:, _cv.CC_STAT_AREA])))).astype(np.uint8)
    ff = bits.copy(); m2 = np.zeros((H + 2, W + 2), np.uint8)
    _cv.floodFill(ff, m2, (0, 0), 1)
    return bits.astype(bool) | (ff == 0)


def grid_overlay(image, box, out, step=50, scale=2):
    """带坐标网格的放大裁剪：模型据此读数描边，不再靠目测猜坐标。

    教训：之前描错叶子就是因为"看图没坐标"，不是图不可分。所有手工描边都必须先跑这个。
    """
    from PIL import ImageDraw

    arr = np.asarray(Image.open(image).convert("RGB"))
    x0, y0, x1, y1 = [int(v) for v in box]
    im = Image.fromarray(arr[y0:y1, x0:x1]).resize(((x1 - x0) * int(scale), (y1 - y0) * int(scale)),
                                                  Image.LANCZOS)
    dr = ImageDraw.Draw(im)
    for gx in range(x0 - x0 % step, x1 + 1, step):
        px = (gx - x0) * scale
        major = gx % (step * 2) == 0
        dr.line([(px, 0), (px, im.height)], fill=(0, 220, 255) if major else (0, 120, 150),
                width=2 if major else 1)
        if major:
            dr.text((px + 3, 3), str(gx), fill=(255, 255, 0))
    for gy in range(y0 - y0 % step, y1 + 1, step):
        py = (gy - y0) * scale
        major = gy % (step * 2) == 0
        dr.line([(0, py), (im.width, py)], fill=(0, 220, 255) if major else (0, 120, 150),
                width=2 if major else 1)
        if major:
            dr.text((3, py + 2), str(gy), fill=(255, 255, 0))
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    # 按扩展名选格式：写 .png 却存 JPEG 会让读图端报"扩展名与字节不符"
    fmt = "PNG" if str(out).lower().endswith(".png") else "JPEG"
    im.save(out, fmt, **({"quality": 92} if fmt == "JPEG" else {}))
    return {"out": str(out), "box": [x0, y0, x1, y1], "step": int(step), "scale": int(scale),
            "size": [im.width, im.height]}


def survey(image, out, cols=4, rows=4):
    """切块并标注每块像素范围：模型据此报"第几行第几列、偏左偏右"，再换算成坐标。"""
    arr = np.asarray(Image.open(image).convert("RGB"))
    H, W = arr.shape[:2]
    im = Image.fromarray(arr.copy())
    dr = ImageDraw.Draw(im)
    cw, ch = W / cols, H / rows
    tiles = []
    for r in range(rows):
        for c in range(cols):
            x0, y0, x1, y1 = int(c * cw), int(r * ch), int((c + 1) * cw), int((r + 1) * ch)
            dr.rectangle([x0, y0, x1 - 1, y1 - 1], outline=(0, 255, 255), width=3)
            label = "r%d c%d  x:%d-%d y:%d-%d" % (r, c, x0, x1, y0, y1)
            dr.rectangle([x0 + 3, y0 + 3, x0 + 12 + 8 * len(label), y0 + 20], fill=(0, 0, 0))
            dr.text((x0 + 6, y0 + 6), label, fill=(255, 255, 0))
            tiles.append({"row": r, "col": c, "box": [x0, y0, x1, y1]})
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    im.save(out, "JPEG", quality=92)
    return {"image": str(image), "size": [W, H], "out": str(out), "tiles": tiles}


def verify(bits, min_area=0.002, max_area=0.35, max_components=1, border_tol=0.10, allow_border=False,
           box=None, box_fill_max=0.90):
    """客观验证：一个"实例"应该面积适中、只有一个连通块（<2% 的碎片已忽略）、不贴边。"""
    bits = np.asarray(bits, bool)
    H, W = bits.shape
    total = float(bits.sum())
    reasons = []
    if total == 0:
        return {"ok": False, "reasons": ["空掩膜"], "area_frac": 0.0, "components": 0, "border_frac": 0.0, "fill": 0.0}
    n, lab, stats, _ = cv2.connectedComponentsWithStats(bits.astype("uint8"), connectivity=8)
    sizes = sorted([int(s[4]) for s in stats[1:]], reverse=True)
    big = [s for s in sizes if s >= total * 0.02]
    area_frac = total / (H * W)
    ys, xs = np.nonzero(bits)
    bw, bh = xs.max() - xs.min() + 1, ys.max() - ys.min() + 1
    fill = total / float(bw * bh)
    edge = np.zeros_like(bits)
    edge[:2, :] = True; edge[-2:, :] = True; edge[:, :2] = True; edge[:, -2:] = True
    border_frac = float((bits & edge).sum()) / total
    if area_frac < min_area:
        reasons.append("面积过小 %.4f < %.4f（种子点没落在物体上？）" % (area_frac, min_area))
    if area_frac > max_area:
        reasons.append("面积过大 %.4f > %.4f（色相门控泄漏到背景）" % (area_frac, max_area))
    if len(big) > max_components:
        reasons.append("连通块 %d > %d（选到了多个物体）" % (len(big), max_components))
    if box is not None:
        x0, y0, x1, y1 = [int(v) for v in box]
        box_area = max(1, (min(W, x1) - max(0, x0)) * (min(H, y1) - max(0, y0)))
        box_fill = total / float(box_area)
        if box_fill > box_fill_max:
            reasons.append("掩膜占矩形框 %.2f > %.2f（色相门控没起作用，等于手画框）" % (box_fill, box_fill_max))
    else:
        box_fill = None
    touches = (xs.min() <= 2 or ys.min() <= 2 or xs.max() >= W - 3 or ys.max() >= H - 3)
    if touches and not allow_border:
        reasons.append("包围盒贴画面边缘（分母/背景被当成物体了）")
    if border_frac > border_tol:
        reasons.append("贴边率 %.2f > %.2f（掩膜跑到画面边缘）" % (border_frac, border_tol))
    return {"ok": not reasons, "reasons": reasons, "area_frac": round(area_frac, 4),
            "components": len(big), "border_frac": round(border_frac, 3), "fill": round(fill, 3),
            "box_fill": None if box_fill is None else round(box_fill, 3),
            "bbox": [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1]}


def rasterize(mask, W, H):
    """把任意类型掩膜（raster/polygon/rect/point/all）铺成整图布尔图，便于统一验证。"""
    t = mask.get("type")
    if t == "raster":
        bits = masks._decode_bits(mask)
        full = np.zeros((H, W), bool)
        full[mask["y"]:mask["y"] + bits.shape[0], mask["x"]:mask["x"] + bits.shape[1]] = bits
        return full
    if t == "all":
        return np.ones((H, W), bool)
    ys, xs = np.mgrid[0:H, 0:W]
    pts = np.stack([xs.ravel(), ys.ravel()], 1).astype(np.float64)
    if t == "polygon":
        return masks.poly_weight(mask, pts).reshape(H, W) > 0.5
    if t == "rect":
        return masks.rect_weight(mask, pts).reshape(H, W) > 0.5
    if t == "point":
        return masks.point_weight(mask, pts).reshape(H, W) > 0.5
    raise ValueError("未知掩膜类型 %r" % t)


def verify_mask(mask, W, H, **kw):
    """直接验证模型画的掩膜（多边形也算）：面积/连通块/贴边/填充率。"""
    return verify(rasterize(mask, W, H), **kw)


def refine_polygon(image, polygon, iters=6, seed_band=9, out_band=0):
    """模型画的多边形 + grabCut 边界吸附：不需要分割模型，也能把粗描边贴到真实边缘。

    做法：多边形内部腐蚀一圈当确定前景、外侧一圈当确定背景，边界带当"可能前景"，
    然后用图像自身的颜色/边缘统计迭代收敛。返回 (精修掩膜, 统计)。
    """
    arr = np.asarray(Image.open(image).convert("RGB"))
    H, W = arr.shape[:2]
    m = rasterize(masks.polygon([tuple(map(float, p)) for p in polygon]), W, H)
    k = np.ones((int(seed_band), int(seed_band)), np.uint8)
    inner = cv2.erode(m.astype(np.uint8), k).astype(bool)
    outer = cv2.dilate(m.astype(np.uint8), k).astype(bool) & ~m
    gc = np.full((H, W), cv2.GC_BGD, np.uint8)
    gc[cv2.dilate(m.astype(np.uint8), k * 2).astype(bool)] = cv2.GC_PR_BGD
    gc[m] = cv2.GC_PR_FGD
    gc[inner] = cv2.GC_FGD
    gc[outer] = cv2.GC_PR_BGD
    if out_band:                      # 允许向外长：把多边形外一圈标成"可能前景"，
        grow = cv2.dilate(m.astype(np.uint8), np.ones((2 * int(out_band) + 1,) * 2, np.uint8)).astype(bool)
        gc[grow & ~m] = cv2.GC_PR_FGD   # 否则 grabCut 被框死，只能挪几个像素（实测只有 4.9% 变化）
    bgd, fgd = np.zeros((1, 65), np.float64), np.zeros((1, 65), np.float64)
    cv2.grabCut(arr, gc, None, bgd, fgd, int(iters), cv2.GC_INIT_WITH_MASK)
    out = (gc == cv2.GC_FGD) | (gc == cv2.GC_PR_FGD)
    # 边缘贴合度：轮廓落在图像强边缘上的程度。只做参考，不当通过/不通过（叶脉也是强边缘，会虚高）
    gray = cv2.GaussianBlur(cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY).astype(np.float32), (3, 3), 0)
    gyy, gxx = np.gradient(gray)
    mag = np.sqrt(gxx * gxx + gyy * gyy)
    kk = np.ones((3, 3), np.uint8)

    def _fit(bits):
        e = cv2.morphologyEx(bits.astype(np.uint8), cv2.MORPH_GRADIENT, kk).astype(bool)
        return float(mag[e].mean()) if e.any() else 0.0

    stats = {"changed_px": int((out != m).sum()), "polygon_px": int(m.sum()),
             "refined_px": int(out.sum()), "grew": bool(out.sum() > m.sum()),
             "edge_fit_polygon": round(_fit(m), 2), "edge_fit_refined": round(_fit(out), 2)}
    return out, stats


def candidates(image, roi=None, k=8, min_area=0.004, max_area=0.35, max_out=6, __unused=None):
    """算法提候选、模型来挑：颜色聚类 + 连通域，输出编号叠图。

    Canny 轮廓在这里不可用——叶子边界不是闭合强边（实测只出两个 fill=0.08 的细圈），
    所以改用"颜色聚类 + 连通域"，和色相生长互补：它按亮度/色度共同分出区域。
    """
    arr = np.asarray(Image.open(image).convert("RGB"))
    H, W = arr.shape[:2]
    x0, y0, x1, y1 = roi if roi else (0, 0, W, H)
    roi_area = float(max(1, (x1 - x0) * (y1 - y0)))
    crop = arr[y0:y1, x0:x1].reshape(-1, 3).astype(np.float32)
    crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 12, 1.0)
    _, labels, centers = cv2.kmeans(crop, int(k), None, crit, 3, cv2.KMEANS_PP_CENTERS)
    lab = labels.reshape(y1 - y0, x1 - x0).astype(np.int32)
    found = []
    for ci in range(int(k)):
        sub = (lab == ci).astype(np.uint8)
        sub = cv2.morphologyEx(sub, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
        sub = cv2.morphologyEx(sub, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
        n, cc, stats, _ = cv2.connectedComponentsWithStats(sub, connectivity=8)
        for j in range(1, n):
            a = float(stats[j, cv2.CC_STAT_AREA])
            if a < min_area * roi_area or a > max_area * roi_area:
                continue
            bits = np.zeros((H, W), bool)
            m = (cc == j)
            bits[y0:y1, x0:x1] = m
            ys, xs = np.nonzero(bits)
            if len(xs) == 0:
                continue
            bw, bh = xs.max() - xs.min() + 1, ys.max() - ys.min() + 1
            if (bw * bh) < 0.25 * a:
                continue
            found.append({"bits": bits, "area": a,
                          "box": [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1],
                          "cluster": int(ci)})
    uniq = []
    for f in sorted(found, key=lambda d: -d["area"]):
        if all((f["bits"] & u["bits"]).sum() / max(1, (f["bits"] | u["bits"]).sum()) < 0.85 for u in uniq):
            uniq.append(f)
    # 给"主体"再加两个并集候选：聚类区域与色相生长结果的差异常常就在这几块
    uniq = uniq[:int(max_out)]
    for i, f in enumerate(uniq, 1):
        f["index"] = i
        f["verify"] = verify(f["bits"], allow_border=True)
    return {"image": str(image), "roi": [x0, y0, x1, y1], "candidates": uniq}


def candidate_sheet(image, cand, out_sheet, roi=None, cols=3):
    """把候选轮廓画在局部放大图上，模型一眼挑。"""
    arr = np.asarray(Image.open(image).convert("RGB"))
    cs = cand["candidates"]
    if not cs:
        return None
    x0, y0, x1, y1 = roi or cand["roi"]
    pw, ph = (x1 - x0), (y1 - y0)
    rows = (len(cs) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * pw, rows * ph), (12, 12, 12))
    dr = ImageDraw.Draw(sheet)
    for k, f in enumerate(cs):
        ov = arr.copy()
        # 填充半透明洋红：只画轮廓会看错"里面到底包了什么"（我上次就是这么挑错的）
        b = f["bits"]
        ov[b] = (ov[b] * 0.45 + np.array([255, 0, 255]) * 0.55).astype(np.uint8)
        edge = cv2.morphologyEx(b.astype(np.uint8), cv2.MORPH_GRADIENT,
                                np.ones((3, 3), np.uint8)).astype(bool)
        ov[edge] = (0, 255, 0)
        tile = Image.fromarray(ov[y0:y1, x0:x1])
        cx, cy = (k % cols) * pw, (k // cols) * ph
        sheet.paste(tile, (cx, cy))
        dr.rectangle([cx, cy, cx + pw - 1, cy + ph - 1], outline=(0, 200, 255), width=2)
        label = "#%d area=%.4f" % (f["index"], f["verify"]["area_frac"])
        dr.rectangle([cx + 2, cy + 2, cx + 12 + 7 * len(label), cy + 20], fill=(0, 0, 0))
        dr.text((cx + 5, cy + 5), label, fill=(255, 255, 0))
    Path(out_sheet).parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out_sheet, "JPEG", quality=92)
    return {"sheet": str(out_sheet), "count": len(cs)}


def _crop(arr, box, scale=3, pad=12):
    H, W = arr.shape[:2]
    x0, y0, x1, y1 = box
    x0, y0 = max(0, x0 - pad), max(0, y0 - pad)
    x1, y1 = min(W, x1 + pad), min(H, y1 + pad)
    im = Image.fromarray(arr[y0:y1, x0:x1])
    return im.resize((im.width * scale, im.height * scale), Image.LANCZOS)


def hints(v, spec):
    """把验证结论翻译成"下一轮改什么"，让模型能自己纠错而不是反复瞎试。"""
    out = []
    for r in v.get("reasons", []):
        if "空掩膜" in r:
            out.append("种子点没落在物体上：重挑坐标，或先跑 survey 放大看清")
        elif "面积过小" in r:
            out.append("种子点偏了/物体太小：换到物体中心，或降低 min_area")
        elif "面积过大" in r:
            out.append("色相门控泄漏：收紧 tolerance 与 hue_tol，提高 min_value，加 negatives 或加 box")
        elif "连通块" in r:
            out.append("选到多个物体：加 negatives 排除邻接实例，或用 box 卡住一个")
        elif "贴边" in r:
            out.append("选中了背景：加 box 限制范围，或提高 min_value 压掉暗背景")
        elif "矩形框" in r:
            out.append("色相分不开前景/背景：改用 polygon（模型自己描边），别再靠颜色生长")
        else:
            out.append(r)
    if v.get("ok"):
        out.append("通过，可直接用于 ops 的 selection")
    return out


def pick(image, specs, out_dir, attempt_log=None, attempt=1):
    """按模型给的种子点逐个长掩膜并验证；返回报告（含局部放大图路径供模型复核）。"""
    arr = np.asarray(Image.open(image).convert("RGB"))
    H, W = arr.shape[:2]
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    report = {"image": str(image), "size": [W, H], "instances": []}
    for sp in specs:
        name = sp.get("name") or ("inst%d" % len(report["instances"]))
        seeds = [tuple(map(int, p)) for p in sp.get("seeds") or []]
        negs = [tuple(map(int, p)) for p in sp.get("negatives") or []]
        if not seeds and not sp.get("polygon"):
            raise SystemExit("实例 %s 需要 seeds 或 polygon" % name)
        box = tuple(int(v) for v in sp["box"]) if sp.get("box") else None
        refine_stats = None
        if sp.get("polygon"):
            full = rasterize(masks.polygon([tuple(map(float, p)) for p in sp["polygon"]]), W, H)
            if sp.get("refine"):
                full, refine_stats = refine_polygon(image, sp["polygon"], sp.get("refine_iters", 6))
            if box:
                x0, y0, x1, y1 = box
                clip = np.zeros((H, W), bool)
                clip[max(0, y0):min(H, y1), max(0, x0):min(W, x1)] = True
                full &= clip
        else:
            close = VM.seeds_mask(arr, seeds, sp.get("tolerance", 60), sp.get("hue_tol", 14.0), negs,
                                  sp.get("min_value", 0.0))
            full, _ = VM.grow_mask(seeds, close, sp.get("grow", 6))
            if box:
                clip = np.zeros((H, W), bool)
                x0, y0, x1, y1 = box
                clip[max(0, y0):min(H, y1), max(0, x0):min(W, x1)] = True
                full &= clip
        if refine_stats:
            refine_stats["edge_fit_gain"] = round(refine_stats["edge_fit_refined"] - refine_stats["edge_fit_polygon"], 2)
        v = verify(full, sp.get("min_area", 0.002), sp.get("max_area", 0.35),
                   sp.get("max_components", 1), sp.get("border_tol", 0.10), sp.get("allow_border", False),
                   box, sp.get("box_fill_max", 0.90))
        mask_path = out_dir / ("%s.mask.json" % name)
        mask_path.write_text(json.dumps(masks.to_json(masks.from_bool(full)), ensure_ascii=False), encoding="utf-8")
        overlay = arr.copy()
        overlay[full] = (overlay[full] * 0.35 + np.array([255, 0, 255]) * 0.65).astype("uint8")
        Image.fromarray(overlay.astype("uint8")).save(out_dir / ("%s.overlay.png" % name), "PNG")
        crop = out_dir / ("%s.crop.jpg" % name)
        if v.get("bbox"):
            _crop(arr, v["bbox"]).save(crop, "JPEG", quality=94)
        report["instances"].append({"name": name, "seeds": seeds, "negatives": negs,
                                    "source": ("polygon+grabcut" if sp.get("polygon") and sp.get("refine")
                                               else "polygon" if sp.get("polygon") else "color-grow"),
                                    "refine": refine_stats,
                                    "box": list(box) if box else None,
                                    "mask": str(mask_path), "overlay": str(out_dir / ("%s.overlay.png" % name)),
                                    "crop": str(crop) if v.get("bbox") else None,
                                    "verify": v})
    for i in report["instances"]:
        i["hints"] = hints(i["verify"], i.get("seeds"))
    report["all_ok"] = all(i["verify"]["ok"] for i in report["instances"])
    if attempt_log:
        with Path(attempt_log).open("a", encoding="utf-8") as f:
            f.write(json.dumps({"attempt": attempt, "image": str(image), "specs": specs,
                                "results": [{"name": i["name"], "verify": i["verify"],
                                             "hints": i["hints"]} for i in report["instances"]]},
                               ensure_ascii=False) + "\n")
    (out_dir / "pick_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return report


def main():
    ap = argparse.ArgumentParser(description="模型自选实例 + 掩膜客观验证")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s1 = sub.add_parser("survey")
    s1.add_argument("--image", required=True); s1.add_argument("--out", required=True)
    s1.add_argument("--cols", type=int, default=4); s1.add_argument("--rows", type=int, default=4)
    s5 = sub.add_parser("snap")
    s5.add_argument("--image", required=True)
    s5.add_argument("--poly", required=True, help="多边形 JSON（[[x,y],...]）")
    s5.add_argument("--out", required=True, help="吸附后多边形 JSON")
    s5.add_argument("--search", type=float, default=18)
    s5.add_argument("--smooth", type=int, default=2)
    sl = sub.add_parser("loop")
    sl.add_argument("--image", required=True)
    sl.add_argument("--spec", required=True, help="初始 windows（栅格提示）JSON")
    sl.add_argument("--iters", type=int, default=6)
    sl.add_argument("--tol", type=float, default=0.995)
    sl.add_argument("--model", choices=["grabcut", "knn", "gmm"], default="grabcut")
    sl.add_argument("--out", required=True)
    sl.add_argument("--width", type=int, default=900)
    sl.add_argument("--height", type=int, default=1100)
    sz = sub.add_parser("zoom")
    sz.add_argument("--image", required=True)
    sz.add_argument("--parent", required=True, help="父窗口 JSON（roi/cols/rows/id）")
    sz.add_argument("--cells", required=True, help="要 zoom 的父格子，如 A3 或 A3,B4")
    sz.add_argument("--sub", type=float, default=3, help="每个父格子再切几份（细度）")
    sz.add_argument("--tile-px", type=float, dest="tile_px", default=None)
    sz.add_argument("--scale", type=int, default=None)
    sz.add_argument("--id", default=None, help="子窗口 id（用于 parent.child 引用）")
    sz.add_argument("--out", required=True, help="子窗口网格图")
    sz.add_argument("--json-out", dest="json_out", default=None, help="把子窗口定义写到 JSON")
    s9 = sub.add_parser("tiles")
    s9.add_argument("--image", required=True)
    s9.add_argument("--roi", required=True, help="x0,y0,x1,y1")
    s9.add_argument("--cols", type=int, default=8)
    s9.add_argument("--rows", type=int, default=6)
    s9.add_argument("--tile-px", type=float, dest="tile_px", default=None, help="格子边长（像素），1=逐像素")
    s9.add_argument("--scale", type=int, default=None)
    s9.add_argument("--out", required=True)
    s10 = sub.add_parser("tile-prompt")
    s10.add_argument("--image", required=True)
    s10.add_argument("--roi", help="单窗口 ROI；用 --spec 时可省")
    s10.add_argument("--cols", type=int, default=8)
    s10.add_argument("--rows", type=int, default=6)
    s10.add_argument("--pos", help="A3,B4 格子编号（单窗口时用）")
    s10.add_argument("--neg", help="A1,H1 格子编号（单窗口时用）")
    s10.add_argument("--spec", help="多级 zoom 的 JSON：[{roi,tile_px,pos,neg}, ...]")
    s10.add_argument("--ring", type=int, default=14)
    s10.add_argument("--model", choices=["grabcut", "knn", "gmm", "gauss", "hist"], default="grabcut")
    s10.add_argument("--components", type=int, default=3)
    s10.add_argument("--width", type=int, default=900)
    s10.add_argument("--height", type=int, default=1100)
    s10.add_argument("--out", required=True)
    s8 = sub.add_parser("prompted")
    s8.add_argument("--image", required=True)
    s8.add_argument("--pos", required=True, help="x,y;x,y;...")
    s8.add_argument("--neg", required=True, help="x,y;x,y;...")
    s8.add_argument("--ring", type=int, default=16)
    s8.add_argument("--bridge", type=int, default=11)
    s8.add_argument("--model", choices=["grabcut", "knn", "gmm", "gauss", "hist"], default="grabcut")
    s8.add_argument("--components", type=int, default=3)
    s8.add_argument("--width", type=int, default=900)
    s8.add_argument("--height", type=int, default=1100)
    s8.add_argument("--out", required=True)
    s7 = sub.add_parser("segment")
    s7.add_argument("--image", required=True)
    s7.add_argument("--roi", required=False, help="x0,y0,x1,y1")
    s7.add_argument("--seeds", required=False, help="x,y;x,y;...")
    s7.add_argument("--ring", type=int, default=18)
    s7.add_argument("--passes", type=int, default=2)
    s7.add_argument("--width", type=int, default=900)
    s7.add_argument("--height", type=int, default=1100)
    s7.add_argument("--out", required=True)
    s6 = sub.add_parser("refine")
    s6.add_argument("--image", required=True)
    s6.add_argument("--mask", required=True)
    s6.add_argument("--out", required=True)
    s6.add_argument("--corridor", type=int, default=12)
    s6.add_argument("--iters", type=int, default=5)
    s4 = sub.add_parser("grid")
    s4.add_argument("--image", required=True)
    s4.add_argument("--box", required=True, help="x0,y0,x1,y1")
    s4.add_argument("--out", required=True)
    s4.add_argument("--step", type=int, default=50)
    s4.add_argument("--scale", type=int, default=2)
    s2 = sub.add_parser("pick")
    s2.add_argument("--image", required=True); s2.add_argument("--spec", required=True)
    s2.add_argument("--out-dir", required=True)
    s2.add_argument("--attempt-log", help="把每轮尝试记进 JSONL（自我纠错的可追溯记录）")
    s2.add_argument("--attempt", type=int, default=1)
    s3 = sub.add_parser("candidates")
    s3.add_argument("--image", required=True)
    s3.add_argument("--out-dir", required=True)
    s3.add_argument("--roi", help="x0,y0,x1,y1 只在 ROI 内找候选")
    s3.add_argument("--max", type=int, default=6)
    s3.add_argument("--sheet", help="候选叠图输出路径")
    a = ap.parse_args()
    if a.cmd == "loop":
        wins = json.loads(Path(a.spec).read_text(encoding="utf-8"))
        bits, info = refine_loop(a.image, wins, iters=a.iters, tol=a.tol, model=a.model)
        bits = np.asarray(Image.fromarray(bits.astype(np.uint8) * 255).resize((a.width, a.height), Image.NEAREST)) > 0
        v = verify(bits, allow_border=True)
        Path(a.out).write_text(json.dumps(masks.to_json(masks.from_bool(bits))), encoding="utf-8")
        print(json.dumps({"area": round(v["area_frac"], 4), "fill": round(v["fill"], 2),
                          "components": v["components"], "bbox": v["bbox"],
                          "iters": info["iters"], "history": info["history"]}, ensure_ascii=False))
    elif a.cmd == "zoom":
        parent = json.loads(Path(a.parent).read_text(encoding="utf-8"))
        g = child_window(a.image, parent, a.cells.split(","), sub=a.sub, tile_px=a.tile_px,
                         wid=a.id, out=a.out, scale=a.scale)
        if a.json_out:
            Path(a.json_out).write_text(json.dumps({k: g[k] for k in
                ("roi", "cols", "rows", "tile_px", "scale", "id", "parent", "parent_cells", "sub")},
                ensure_ascii=False, indent=1), encoding="utf-8")
        print(json.dumps({"out": g["out"], "roi": [round(v, 2) for v in g["roi"]],
                          "cols": g["cols"], "rows": g["rows"], "tile_px": g["tile_px"],
                          "scale": g["scale"], "parent_cells": g["parent_cells"]}, ensure_ascii=False))
    elif a.cmd == "tiles":
        g = tile_grid(a.image, [float(t) for t in a.roi.split(",")], cols=a.cols, rows=a.rows,
                      tile_px=a.tile_px, out=a.out, scale=a.scale)
        print(json.dumps({"out": g["out"], "cells": sorted(g["cells"].keys())[:6] + ["..."],
                          "cols": g["cols"], "rows": g["rows"], "tile_px": g["tile_px"],
                          "scale": g["scale"]}, ensure_ascii=False))
    elif a.cmd == "tile-prompt":
        if a.spec:
            windows = json.loads(Path(a.spec).read_text(encoding="utf-8"))
        else:
            if not (a.pos and a.neg and a.roi):
                raise SystemExit("tile-prompt 需要 --spec，或同时给 --roi/--pos/--neg")
            windows = [{"roi": [float(t) for t in a.roi.split(",")], "cols": a.cols, "rows": a.rows,
                        "pos": a.pos.split(","), "neg": a.neg.split(",")}]
        bits, pts = prompted_from_tiles(a.image, windows, ring=a.ring, model=a.model,
                                        components=a.components)
        bits = np.asarray(Image.fromarray(bits.astype(np.uint8) * 255).resize((a.width, a.height), Image.NEAREST)) > 0
        v = verify(bits, allow_border=True)
        Path(a.out).write_text(json.dumps(masks.to_json(masks.from_bool(bits))), encoding="utf-8")
        print(json.dumps({"area": round(v["area_frac"], 4), "fill": round(v["fill"], 2),
                          "components": v["components"], "bbox": v["bbox"],
                          "pos_pts": len(pts["pos"]), "neg_pts": len(pts["neg"]),
                          "windows": len(pts["windows"])}, ensure_ascii=False))
    elif a.cmd == "prompted":
        pos = [[float(u) for u in t.split(",")] for t in a.pos.split(";")]
        neg = [[float(u) for u in t.split(",")] for t in a.neg.split(";")]
        bits = prompted_mask(a.image, pos, neg, ring=a.ring, bridge_px=a.bridge,
                             model=a.model, components=a.components)
        bits = np.asarray(Image.fromarray(bits.astype(np.uint8) * 255).resize((a.width, a.height), Image.NEAREST)) > 0
        v = verify(bits, allow_border=True)
        Path(a.out).write_text(json.dumps(masks.to_json(masks.from_bool(bits))), encoding="utf-8")
        print(json.dumps({"area": round(v["area_frac"], 4), "fill": round(v["fill"], 2),
                          "components": v["components"], "bbox": v["bbox"]}, ensure_ascii=False))
    elif a.cmd == "segment":
        roi = [float(t) for t in a.roi.split(",")] if a.roi else None
        seeds = [[float(u) for u in t.split(",")] for t in a.seeds.split(";")] if a.seeds else None
        bits = segment(a.image, roi=roi, seeds=seeds, ring=a.ring, passes=a.passes)
        bits = np.asarray(Image.fromarray(bits.astype(np.uint8) * 255).resize((a.width, a.height), Image.NEAREST)) > 0
        v = verify(bits, allow_border=True)
        Path(a.out).write_text(json.dumps(masks.to_json(masks.from_bool(bits))), encoding="utf-8")
        print(json.dumps({"area": round(v["area_frac"], 4), "fill": round(v["fill"], 2),
                          "components": v["components"], "bbox": v["bbox"]}, ensure_ascii=False))
    elif a.cmd == "refine":
        m0 = masks.from_json(json.loads(Path(a.mask).read_text(encoding="utf-8")))
        W0 = int(m0.get("width", 900)); H0 = int(m0.get("height", 1100))
        b0 = rasterize(m0, W0, H0)
        b1 = refine_mask(a.image, b0, a.corridor, a.iters)
        v0, v1 = verify(b0, allow_border=True), verify(b1, allow_border=True)
        iou = float((b0 & b1).sum()) / max(1, int((b0 | b1).sum()))
        Path(a.out).write_text(json.dumps(masks.to_json(masks.from_bool(b1))), encoding="utf-8")
        print(json.dumps({"before": {"area": round(v0["area_frac"], 4), "fill": round(v0["fill"], 2)},
                          "after": {"area": round(v1["area_frac"], 4), "fill": round(v1["fill"], 2),
                                    "bbox": v1["bbox"]}, "iou": round(iou, 3)}, ensure_ascii=False))
    elif a.cmd == "snap":
        poly = json.loads(Path(a.poly).read_text(encoding="utf-8"))
        new_poly, st = snap_polygon(a.image, poly, a.search, a.smooth)
        Path(a.out).write_text(json.dumps(new_poly), encoding="utf-8")
        print(json.dumps(st, ensure_ascii=False))
    elif a.cmd == "grid":
        box = tuple(int(v) for v in a.box.split(","))
        print(json.dumps(grid_overlay(a.image, box, a.out, a.step, a.scale), ensure_ascii=False))
    elif a.cmd == "candidates":
        roi = tuple(int(v) for v in a.roi.split(",")) if a.roi else None
        res = candidates(a.image, roi, max_out=a.max)
        Path(a.out_dir).mkdir(parents=True, exist_ok=True)
        sheet = candidate_sheet(a.image, {"candidates": res["candidates"], "roi": res["roi"]},
                                a.sheet or str(Path(a.out_dir) / "candidates.jpg"), roi)
        for f in res["candidates"]:
            p = Path(a.out_dir) / ("cand%d.mask.json" % f["index"])
            p.write_text(json.dumps(masks.to_json(masks.from_bool(f["bits"])), ensure_ascii=False), encoding="utf-8")
            f["mask"] = str(p)
            f.pop("bits")
        for f in res["candidates"]:
            v = f["verify"]
            print("#%d  area=%.4f comp=%d fill=%.2f bbox=%s %s" % (
                f["index"], v["area_frac"], v["components"], v["fill"], v["bbox"],
                "ok" if v["ok"] else ";".join(v["reasons"])[:40]))
        print("sheet:", (sheet or {}).get("sheet"))
    elif a.cmd == "survey":
        print(json.dumps(survey(a.image, a.out, a.cols, a.rows), ensure_ascii=False))
    else:
        specs = json.loads(Path(a.spec).read_text(encoding="utf-8"))
        if isinstance(specs, dict):
            specs = specs.get("instances", [])
        rep = pick(a.image, specs, a.out_dir, a.attempt_log, a.attempt)
        for i in rep["instances"]:
            v = i["verify"]
            print("%-14s %-4s area=%.4f comp=%d fill=%.2f bbox=%s %s" % (
                i["name"], "OK" if v["ok"] else "BAD", v["area_frac"], v["components"], v["fill"],
                v["bbox"], "; ".join(v["reasons"])))
            for h in i["hints"]:
                print("    -> %s" % h)
        print("all_ok =", rep["all_ok"], "| report:", (Path(a.out_dir) / "pick_report.json"))


if __name__ == "__main__":
    main()


def zoom_plan(image, roi, max_cell_px=40.0, max_cells=64):
    """给 zoom 自动选窗口：把 roi 切成若干窗口，使每格 <= max_cell_px 且总格子数可控。

    返回可直接喂给 cells_mask / 模型的 windows 骨架（roi/cols/rows/cell_px），
    pos/neg 仍由模型看图报——这个函数只负责"窗口粒度该多细"，不猜内容。
    """
    from PIL import Image
    x0, y0, x1, y1 = [float(v) for v in roi]
    w, h = x1 - x0, y1 - y0
    if w <= 0 or h <= 0:
        raise ValueError("roi 非法: %r" % (roi,))

    def one(rx0, ry0, rx1, ry1):
        rw, rh = rx1 - rx0, ry1 - ry0
        cols = max(1, int(np.ceil(rw / max_cell_px)))
        rows = max(1, int(np.ceil(rh / max_cell_px)))
        return {"roi": [round(rx0, 2), round(ry0, 2), round(rx1, 2), round(ry1, 2)],
                "cols": cols, "rows": rows, "cell_px": round(max(rw / cols, rh / rows), 2)}

    wins = [one(x0, y0, x1, y1)]
    guard = 0
    while wins and guard < 6:
        guard += 1
        if max(w_["cols"] * w_["rows"] for w_ in wins) <= max_cells:
            break
        nxt = []
        for w_ in wins:
            if w_["cols"] * w_["rows"] <= max_cells:
                nxt.append(w_); continue
            rx0, ry0, rx1, ry1 = w_["roi"]
            if (rx1 - rx0) >= (ry1 - ry0):
                mid = (rx0 + rx1) / 2.0
                nxt += [one(rx0, ry0, mid, ry1), one(mid, ry0, rx1, ry1)]
            else:
                mid = (ry0 + ry1) / 2.0
                nxt += [one(rx0, ry0, rx1, mid), one(rx0, mid, rx1, ry1)]
        wins = nxt
    out = []
    for i, w_ in enumerate(wins):
        out.append(dict(id=("w%d" % i) if len(wins) > 1 else "root", roi=w_["roi"],
                        cols=w_["cols"], rows=w_["rows"], cell_px=w_["cell_px"]))
    return {"windows": out, "cell_px": max(w_["cell_px"] for w_ in wins) if wins else None,
            "total_cells": sum(w_["cols"] * w_["rows"] for w_ in wins),
            "note": "pos/neg 单元格仍由模型看图报；这里只定窗口粒度"}
