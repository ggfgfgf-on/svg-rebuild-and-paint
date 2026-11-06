"""Structure metric: measure subject silhouette, bbox, grounding, and landmarks.

This is a measurement helper, not a drawing helper. It never generates strokes and
never contains subject geometry; a candidate plan supplies the silhouette/landmarks.

Examples:
    python scripts/structure_metric.py cand.plan.json --layers stag --ref ref.plan.json --out report.json
    python scripts/structure_metric.py cand.plan.json --layers stag --spec my-spec.json --out report.json
    python scripts/structure_metric.py cand.plan.json --layers stag --spec specs/stag.example.json --landmarks lm.json --guide guide.png
"""
import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import plan_io
import render_util as RU


def _layer_list(doc, layers):
    if layers:
        out = [x.strip() for x in layers.split(",") if x.strip()]
        if out:
            return out
    subj = list(doc.get("subject_layers") or [])
    if subj:
        return subj
    raise ValueError("no subject layers: pass --layers or set subject_layers in the plan")


def load_mask(plan_path, layers=None, alpha_thresh=8):
    """Return (mask uint8 HxW, width, height, resolved layer names)."""
    doc, w, h = plan_io.load_plan(str(plan_path))
    use = _layer_list(doc, layers)
    mask = np.zeros((h, w), np.uint8)
    for layer in use:
        arr = RU.render_layer_rgba(doc, layer, gpu=False)
        mask = np.maximum(mask, (arr[..., 3] > int(alpha_thresh)).astype(np.uint8))
    return mask, int(w), int(h), use


def mask_metrics(mask):
    """Return bbox/aspect/fill/centroid/bottom metrics for a binary mask."""
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        return {"empty": True, "area": 0}
    x0, x1 = int(xs.min()), int(xs.max()) + 1
    y0, y1 = int(ys.min()), int(ys.max()) + 1
    bw, bh = max(1, x1 - x0), max(1, y1 - y0)
    area = int(len(xs))
    fill = float(area) / float(bw * bh)
    bottom_row = mask[y1 - 1]
    bottom_x = np.nonzero(bottom_row)[0]
    bottom_width = int(bottom_x.max() - bottom_x.min() + 1) if len(bottom_x) else 0
    return {
        "empty": False,
        "bbox": [x0, y0, x1, y1],
        "bbox_wh": [bw, bh],
        "bbox_aspect": round(float(bw) / float(bh), 4),
        "area": area,
        "fill_ratio": round(fill, 4),
        "centroid": [round(float(xs.mean()), 2), round(float(ys.mean()), 2)],
        "bottom_y": int(y1 - 1),
        "bottom_width": bottom_width,
    }


def check_shape_metrics(metrics, spec):
    """Optional bbox_aspect / fill_ratio gates. Fields absent = not checked."""
    checks = {}
    for key in ("bbox_aspect", "fill_ratio"):
        target = spec.get(key)
        if target is None:
            continue
        tol = float((spec.get("tolerances") or {}).get(key, 0.0))
        got = float(metrics.get(key, 0.0))
        err = abs(got - float(target))
        checks[key] = {"candidate": round(got, 4), "target": round(float(target), 4),
                       "error": round(err, 4), "tolerance": tol,
                       "pass": bool(err <= tol)}
    return {"available": bool(checks),
            "pass": all(v["pass"] for v in checks.values()) if checks else None,
            "checks": checks}


def _resize(mask, shape):
    if mask.shape == shape:
        return mask
    return cv2.resize(mask, (shape[1], shape[0]), interpolation=cv2.INTER_NEAREST)


def _crop_mask(mask, bbox):
    x0, y0, x1, y1 = bbox
    return mask[y0:y1, x0:x1]


def _fit_mask(mask, size=256):
    return cv2.resize((mask > 0).astype(np.uint8), (size, size), interpolation=cv2.INTER_NEAREST)


def _shape_compare(ref_crop, cand_crop):
    r = _fit_mask(ref_crop)
    c = _fit_mask(cand_crop)
    inter = int(np.logical_and(r, c).sum())
    union = int(np.logical_or(r, c).sum())
    iou = float(inter) / union if union else 0.0
    dice = float(2 * inter) / (int(r.sum()) + int(c.sum())) if (r.sum() + c.sum()) else 0.0
    dt_r = cv2.distanceTransform(1 - r, cv2.DIST_L2, 5)
    dt_c = cv2.distanceTransform(1 - c, cv2.DIST_L2, 5)
    d1 = float(dt_r[c > 0].mean()) if c.any() else 0.0
    d2 = float(dt_c[r > 0].mean()) if r.any() else 0.0
    return {"shape_iou": round(iou, 4), "shape_dice": round(dice, 4),
            "shape_chamfer_px": round((d1 + d2) * 0.5, 3)}


def compare_masks(ref_mask, cand_mask):
    """Return aligned shape_iou/dice/chamfer and placement metrics."""
    if ref_mask.shape != cand_mask.shape:
        cand_mask = _resize(cand_mask, ref_mask.shape)
    rm = mask_metrics(ref_mask)
    cm = mask_metrics(cand_mask)
    out = {"ref": rm, "candidate": cm}
    if not rm.get("empty") and not cm.get("empty"):
        rc = _crop_mask(ref_mask, rm["bbox"])
        cc = _crop_mask(cand_mask, cm["bbox"])
        out.update(_shape_compare(rc, cc))
        out["placement"] = {
            "bbox_aspect_ratio": round(cm["bbox_aspect"] / max(rm["bbox_aspect"], 1e-6), 4),
            "area_ratio": round(cm["area"] / max(float(rm["area"]), 1e-6), 4),
            "centroid_dx": round(cm["centroid"][0] - rm["centroid"][0], 2),
            "centroid_dy": round(cm["centroid"][1] - rm["centroid"][1], 2),
            "bottom_y_diff": int(cm["bottom_y"] - rm["bottom_y"]),
        }
    return out


def _bbox_norm_point(x, y, bbox):
    x0, y0, x1, y1 = bbox
    return [(float(x) - x0) / max(1.0, x1 - x0), (float(y) - y0) / max(1.0, y1 - y0)]


def landmarks_from_tags(doc, prefix="landmark:"):
    out = {}
    for s in doc.get("strokes") or []:
        tag = str(s.get("tag") or "")
        if not tag.startswith(prefix):
            continue
        name = tag[len(prefix):]
        pts = np.asarray(s["points"], np.float32)
        if pts.size == 0:
            continue
        out[name] = [float(pts[:, 0].mean()), float(pts[:, 1].mean())]
    return out


def load_landmarks(path_or_none, doc):
    if path_or_none:
        raw = json.loads(Path(path_or_none).read_text(encoding="utf-8"))
        raw = raw.get("landmarks", raw) if isinstance(raw, dict) else raw
        return {str(k): [float(v[0]), float(v[1])] for k, v in raw.items()}
    return landmarks_from_tags(doc)


def check_landmarks(pred, spec, cand_bbox):
    """Return landmark and segment errors; pass is theirs, not an aesthetic score."""
    raw_target = spec.get("landmarks_bbox") or {}
    target = {}
    for name, val in raw_target.items():
        try:
            if len(val) == 2:
                float(val[0]); float(val[1])
                target[name] = val
        except (TypeError, ValueError):
            continue
    if not pred or not target:
        return {"available": False}
    rows = []
    for name, want in target.items():
        if name not in pred:
            rows.append({"name": name, "missing": True})
            continue
        p = _bbox_norm_point(pred[name][0], pred[name][1], cand_bbox)
        err = float(np.hypot(p[0] - float(want[0]), p[1] - float(want[1])))
        rows.append({"name": name, "pred_bbox_norm": [round(p[0], 4), round(p[1], 4)],
                     "target_bbox_norm": [round(float(want[0]), 4), round(float(want[1]), 4)],
                     "error": round(err, 4)})
    ok = [r for r in rows if "error" in r]
    errs = [r["error"] for r in ok]
    tol = float((spec.get("tolerances") or {}).get("landmark", 0.06))
    landmark_ok = bool(ok and len(ok) == len(target) and max(errs) <= tol)

    seg_rows = []
    segment_tol = float((spec.get("tolerances") or {}).get("segment", 0.10))
    pred_norm = {}
    for name, xy in pred.items():
        pred_norm[name] = _bbox_norm_point(xy[0], xy[1], cand_bbox)
    for name, pair in (spec.get("segments") or {}).items():
        a, b = str(pair[0]), str(pair[1])
        if a not in pred_norm or b not in pred_norm or a not in target or b not in target:
            seg_rows.append({"name": name, "missing": True})
            continue
        pa, pb = pred_norm[a], pred_norm[b]
        ta, tb = target[a], target[b]
        got = float(np.hypot(pa[0] - pb[0], pa[1] - pb[1]))
        want = float(np.hypot(float(ta[0]) - float(tb[0]), float(ta[1]) - float(tb[1])))
        err = abs(got - want)
        seg_rows.append({"name": name, "pred_bbox_norm_len": round(got, 4),
                         "target_bbox_norm_len": round(want, 4), "error": round(err, 4)})
    seg_ok = [r for r in seg_rows if "error" in r]
    segment_ok = bool(seg_ok and len(seg_ok) == len(seg_rows) and
                      max(r["error"] for r in seg_ok) <= segment_tol) if seg_rows else None

    return {
        "available": True,
        "tolerance": tol,
        "mean_error": round(float(np.mean(errs)), 4) if errs else None,
        "max_error": round(float(np.max(errs)), 4) if errs else None,
        "pass": bool(landmark_ok and segment_ok is not False),
        "landmark_pass": landmark_ok,
        "segment_pass": segment_ok if seg_rows else None,
        "segment_tolerance": segment_tol,
        "segments": seg_rows,
        "landmarks": rows,
    }


def draw_guide(mask, spec, out_path, bbox=None):
    h, w = mask.shape[:2]
    canvas = np.full((h, w, 3), 245, np.uint8)
    # faint grid
    for x in range(0, w, max(20, w // 10)):
        cv2.line(canvas, (x, 0), (x, h), (225, 225, 225), 1)
    for y in range(0, h, max(20, h // 10)):
        cv2.line(canvas, (0, y), (w, y), (225, 225, 225), 1)
    if bbox:
        x0, y0, x1, y1 = bbox
        cv2.rectangle(canvas, (x0, y0), (x1 - 1, y1 - 1), (30, 120, 220), 2)
    sb = spec.get("subject_bbox")
    if isinstance(sb, (list, tuple)) and len(sb) == 4:
        sx0, sy0, sx1, sy1 = [int(sb[0] * w), int(sb[1] * h), int(sb[2] * w), int(sb[3] * h)]
        cv2.rectangle(canvas, (sx0, sy0), (sx1, sy1), (220, 120, 30), 2)
        lm = spec.get("landmarks_bbox") or {}
        for name, val in lm.items():
            try:
                u, v = float(val[0]), float(val[1])
            except (TypeError, ValueError):
                continue
            x = int(sx0 + u * (sx1 - sx0))
            y = int(sy0 + v * (sy1 - sy0))
            cv2.drawMarker(canvas, (x, y), (20, 20, 20), cv2.MARKER_CROSS, 14, 2)
            cv2.putText(canvas, name, (x + 6, y - 6), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (40, 40, 40), 1, cv2.LINE_AA)
    gy = spec.get("ground_y")
    if gy is not None:
        cv2.line(canvas, (0, int(float(gy) * h)), (w, int(float(gy) * h)), (30, 180, 80), 1)
    cv2.imwrite(str(out_path), canvas)


def main():
    ap = argparse.ArgumentParser(description="Measure subject structure of a plan")
    ap.add_argument("plan")
    ap.add_argument("--layers", help="comma-separated subject layer names; default plan.subject_layers")
    ap.add_argument("--ref", help="reference plan silhouette (not a bitmap); shape_iou is bbox-normalized")
    ap.add_argument("--ref-layers", help="reference subject layers; default --layers")
    ap.add_argument("--spec", help="normalized shape/landmark/segment ruler")
    ap.add_argument("--landmarks", help="candidate landmark JSON; default: strokes tagged landmark:<name>")
    ap.add_argument("--alpha-thresh", type=int, default=8)
    ap.add_argument("--out", help="write report JSON")
    ap.add_argument("--guide", help="write guide PNG")
    ap.add_argument("--require-pass", action="store_true",
                    help="exit 2 unless structure_pass is true (requires available spec gates)")
    a = ap.parse_args()

    doc, w, h = plan_io.load_plan(a.plan)
    mask, _, _, layers = load_mask(a.plan, a.layers, a.alpha_thresh)
    report = {"plan": a.plan, "canvas": [w, h], "layers": layers, "mask": mask_metrics(mask)}
    if a.ref:
        ref_mask, _, _, ref_layers = load_mask(a.ref, a.ref_layers or a.layers, a.alpha_thresh)
        report["ref"] = a.ref
        report["ref_layers"] = ref_layers
        report["comparison"] = compare_masks(ref_mask, mask)
    spec = None
    if a.spec:
        spec = json.loads(Path(a.spec).read_text(encoding="utf-8"))
        report["spec"] = a.spec
        report["shape"] = check_shape_metrics(report["mask"], spec)
        pred = load_landmarks(a.landmarks, doc)
        bbox = report["mask"].get("bbox")
        report["landmarks"] = check_landmarks(pred, spec, bbox) if bbox else {"available": False}
        gy = spec.get("ground_y")
        if gy is not None and bbox:
            bottom_norm = float(bbox[3]) / float(h)
            gerr = abs(bottom_norm - float(gy))
            gtol = float((spec.get("tolerances") or {}).get("ground", 0.03))
            report["ground"] = {"candidate_bottom_norm": round(bottom_norm, 4),
                                "target_y": round(float(gy), 4),
                                "error": round(gerr, 4), "tolerance": gtol,
                                "pass": bool(gerr <= gtol)}
        parts = []
        for key in ("shape", "landmarks", "ground"):
            node = report.get(key) or {}
            if node.get("available", key == "ground") and "pass" in node:
                parts.append(bool(node["pass"]))
        report["structure_pass"] = all(parts) if parts else None
        if a.guide:
            draw_guide(mask, spec, a.guide, bbox)
    elif a.guide:
        raise SystemExit("--guide requires --spec")
    if a.out:
        Path(a.out).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=1))
    if a.require_pass and report.get("structure_pass") is not True:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
