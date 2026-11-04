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
        if not seeds:
            raise SystemExit("实例 %s 缺少 seeds" % name)
        box = tuple(int(v) for v in sp["box"]) if sp.get("box") else None
        close = VM.seeds_mask(arr, seeds, sp.get("tolerance", 60), sp.get("hue_tol", 14.0), negs,
                              sp.get("min_value", 0.0))
        full, _ = VM.grow_mask(seeds, close, sp.get("grow", 6))
        if box:
            clip = np.zeros((H, W), bool)
            x0, y0, x1, y1 = box
            clip[max(0, y0):min(H, y1), max(0, x0):min(W, x1)] = True
            full &= clip
        v = verify(full, sp.get("min_area", 0.002), sp.get("max_area", 0.35),
                   sp.get("max_components", 1), sp.get("border_tol", 0.10), sp.get("allow_border", False),
                   box, sp.get("box_fill_max", 0.90))
        mask_path = out_dir / ("%s.mask.json" % name)
        mask_path.write_text(json.dumps(masks.to_json(masks.from_bool(full)), ensure_ascii=False), encoding="utf-8")
        overlay = arr.copy()
        overlay[full] = (overlay[full] * 0.35 + np.array([255, 0, 255]) * 0.65).astype("uint8")
        Path(out_dir, "%s.overlay.png" % name).write_bytes(
            __import__("io").BytesIO(_to_png(overlay)).getvalue())
        crop = out_dir / ("%s.crop.jpg" % name)
        if v.get("bbox"):
            _crop(arr, v["bbox"]).save(crop, "JPEG", quality=94)
        report["instances"].append({"name": name, "seeds": seeds, "negatives": negs,
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


def _to_png(arr):
    import io
    buf = io.BytesIO()
    Image.fromarray(arr.astype("uint8")).save(buf, "PNG")
    return buf.getvalue()


def main():
    ap = argparse.ArgumentParser(description="模型自选实例 + 掩膜客观验证")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s1 = sub.add_parser("survey")
    s1.add_argument("--image", required=True); s1.add_argument("--out", required=True)
    s1.add_argument("--cols", type=int, default=4); s1.add_argument("--rows", type=int, default=4)
    s2 = sub.add_parser("pick")
    s2.add_argument("--image", required=True); s2.add_argument("--spec", required=True)
    s2.add_argument("--out-dir", required=True)
    s2.add_argument("--attempt-log", help="把每轮尝试记进 JSONL（自我纠错的可追溯记录）")
    s2.add_argument("--attempt", type=int, default=1)
    a = ap.parse_args()
    if a.cmd == "survey":
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
