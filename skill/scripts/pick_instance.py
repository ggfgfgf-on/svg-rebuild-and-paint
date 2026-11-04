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
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw

import masks
import visual_mask as VM


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
    im.save(out, "JPEG", quality=92)
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
    if a.cmd == "grid":
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
