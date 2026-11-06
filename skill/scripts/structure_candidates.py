"""Render N structure candidates, measure them, and build a contact sheet.

This does not generate candidates. Generate them first with the same prompt, then:
    python scripts/structure_candidates.py cand_1.plan.json cand_2.plan.json ... \
        --layers stag --ref ref.plan.json --ref-layers stag,stag_detail,rim \
        --thumb 320 --sheet out/candidates.png --out out/ranking.json

Without --ref, --spec can gate bbox_aspect / fill_ratio / landmarks / segments; if no
candidate passes, best is null. Without either, no automatic ranking is claimed and
the contact sheet is for human/vision selection.
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import plan_io
import render_util as RU
import structure_metric as SM


def white_composite(arr):
    a = arr[..., 3:4].astype(np.float32) / 255.0
    return np.clip(arr[..., :3].astype(np.float32) * a + 250.0 * (1 - a), 0, 255).astype(np.uint8)


def thumb(plan_path, width):
    doc, w, h = plan_io.load_plan(str(plan_path))
    arr = RU.render_arr(doc, gpu=False, nthreads=8)
    im = Image.fromarray(white_composite(arr))
    th = max(1, int(round(width * h / float(w))))
    return im.resize((width, th), Image.LANCZOS)


def main():
    ap = argparse.ArgumentParser(description="Render and rank structure candidates")
    ap.add_argument("plans", nargs="+")
    ap.add_argument("--layers", required=True, help="candidate subject layers")
    ap.add_argument("--ref", help="reference plan for mask comparison")
    ap.add_argument("--ref-layers", help="reference subject layers; default --layers")
    ap.add_argument("--spec", help="normalized structure spec")
    ap.add_argument("--thumb", type=int, default=320)
    ap.add_argument("--cols", type=int, default=3)
    ap.add_argument("--sheet", help="contact sheet PNG")
    ap.add_argument("--out", help="ranking JSON")
    ap.add_argument("--require-best", action="store_true",
                    help="exit 2 when no candidate passes the available gates")
    a = ap.parse_args()

    ref_mask = None
    if a.ref:
        ref_mask, _, _, _ = SM.load_mask(a.ref, a.ref_layers or a.layers)
    spec = json.loads(Path(a.spec).read_text(encoding="utf-8")) if a.spec else None

    rows = []
    for plan in a.plans:
        mask, w, h, layers = SM.load_mask(plan, a.layers)
        row = {"plan": plan, "canvas": [w, h], "layers": layers, "mask": SM.mask_metrics(mask)}
        if ref_mask is not None:
            row["comparison"] = SM.compare_masks(ref_mask, mask)
        if spec:
            doc, _, _ = plan_io.load_plan(plan)
            pred = SM.load_landmarks(None, doc)
            bbox = row["mask"].get("bbox")
            row["shape"] = SM.check_shape_metrics(row["mask"], spec)
            row["landmarks"] = SM.check_landmarks(pred, spec, bbox) if bbox else {"available": False}
        rows.append(row)

    if ref_mask is not None:
        rows.sort(key=lambda r: (-r["comparison"].get("shape_iou", 0.0), r["comparison"].get("shape_chamfer_px", 1e9)))
    elif spec:
        def key(r):
            sh = r.get("shape") or {}
            lm = r.get("landmarks") or {}
            shape_ok = sh.get("pass") if sh.get("available") else True
            lm_ok = lm.get("pass") if lm.get("available") else True
            maxe = lm.get("max_error") if lm.get("max_error") is not None else 9
            return (0 if (shape_ok and lm_ok) else 1, float(maxe))
        rows.sort(key=key)
    best = None
    if ref_mask is not None and rows:
        best = rows[0]["plan"]
    elif spec:
        passing = []
        for r in rows:
            sh = r.get("shape") or {}
            lm = r.get("landmarks") or {}
            ok_shape = sh.get("pass") if sh.get("available") else True
            ok_lm = lm.get("pass") if lm.get("available") else True
            if ok_shape and ok_lm and (sh.get("available") or lm.get("available")):
                passing.append(r)
        best = passing[0]["plan"] if passing else None

    if a.sheet:
        ims = []
        for r in rows:
            try:
                ims.append((r, thumb(r["plan"], a.thumb)))
            except Exception as ex:
                print("skip thumb %s: %s" % (r["plan"], ex), file=sys.stderr)
        if ims:
            cw = a.thumb
            ch = max(im.height for _, im in ims)
            label_h = 54
            cols = max(1, a.cols)
            rowsn = (len(ims) + cols - 1) // cols
            sheet = Image.new("RGB", (cols * cw, rowsn * (ch + label_h)), (255, 255, 255))
            dr = ImageDraw.Draw(sheet)
            for i, (r, im) in enumerate(ims):
                x = (i % cols) * cw
                y = (i // cols) * (ch + label_h)
                sheet.paste(im, (x, y))
                line = Path(r["plan"]).name
                if "comparison" in r:
                    line += "  shape_iou=%.3f" % r["comparison"].get("shape_iou", 0.0)
                lm = r.get("landmarks") or {}
                if lm.get("available"):
                    line += "  lm=%.3f" % (lm.get("max_error") or 9)
                dr.text((x + 4, y + ch + 3), line[:60], fill=(20, 20, 20))
                if r["plan"] == best:
                    dr.rectangle([x, y, x + cw - 1, y + ch + label_h - 1], outline=(30, 140, 220), width=3)
            Path(a.sheet).parent.mkdir(parents=True, exist_ok=True)
            sheet.save(a.sheet)

    report = {"candidates": rows, "best": best, "mode": "reference" if ref_mask is not None else ("spec" if spec else "manual")}
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"best": best, "mode": report["mode"], "n": len(rows), "sheet": a.sheet}, ensure_ascii=False))
    if a.require_best and best is None:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
