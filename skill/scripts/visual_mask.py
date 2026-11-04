"""视觉提示 → 光栅掩膜：模型在图上给点/框，辅助函数做区域生长。

不依赖任何分割模型：种子点 + 颜色容差 + 连通性，就能把"这朵玫瑰"变成像素级掩膜。
补丁：掩膜先叠成洋红色 overlay 供模型自己检查，确认后再用于 ops（--mask-file）。

用法:
    python scripts/visual_mask.py image.png --seed 620,150 --seed 600,240 --tolerance 60 \
        --grow 6 --out mask.json --overlay overlay.png
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import cv2
import numpy as np
from PIL import Image

import masks


def seeds_mask(arr, seeds, tolerance, hue_tol=14.0, negatives=(), min_value=0.0):
    """与任意种子"同色系"的像素：先比色相（避免暗红和暗绿混淆），再比明度。

    纯 RGB 阈值会在暗部失效：深红 (67,0,1) 与深绿 (8,18,16) 的逐通道差只有 45~59，
    容差 55 就会把背景吃进玫瑰。色相分离度足够大，所以先卡色相。
    """
    hsv = cv2.cvtColor(arr, cv2.COLOR_RGB2HSV).astype(np.float32)
    h, sat, val = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    close = np.zeros(arr.shape[:2], dtype=bool)
    for x, y in seeds:
        hs, vs, ss = h[y, x], val[y, x], sat[y, x]
        dh = np.abs(h - hs)
        dh = np.minimum(dh, 180.0 - dh)                 # 色相环绕
        near = (dh <= hue_tol) & (np.abs(val - vs) <= tolerance) & (sat >= ss * 0.35)
        close |= near
    # 负样本：把"看起来像但其实不是"的实例排除（同色相邻物体最典型的串扰来源）
    if min_value > 0:
        close &= (val >= min_value)   # 暗部色相不可靠，用亮度地板挡住背景
    for x, y in negatives:
        hs, vs, ss = h[y, x], val[y, x], sat[y, x]
        dh = np.abs(h - hs)
        dh = np.minimum(dh, 180.0 - dh)
        close &= ~((dh <= hue_tol) & (np.abs(val - vs) <= tolerance))
    return close


def grow_mask(seeds, close, grow=0):
    """只保留与种子连通的区域，再按 grow 像素膨胀补边缘笔触。"""
    h, w = close.shape
    seed_mask = np.zeros((h, w), dtype=np.uint8)
    for x, y in seeds:
        seed_mask[y, x] = 1
    n, lab = cv2.connectedComponents(close.astype(np.uint8), connectivity=8)
    keep = np.zeros((h, w), dtype=np.uint8)
    for x, y in seeds:
        keep |= (lab == lab[y, x]).astype(np.uint8)
    if grow > 0:
        keep = cv2.dilate(keep, np.ones((3, 3), np.uint8), iterations=int(grow))
    return keep.astype(bool), int(seed_mask.sum())


def overlay(arr, m, path):
    """掩膜叠洋红，供模型目视检查边界。"""
    out = arr.astype(np.float32).copy()
    out[m] = out[m] * 0.45 + np.array([255.0, 0.0, 220.0]) * 0.55
    Image.fromarray(out.clip(0, 255).astype(np.uint8)).save(path)


def run(image, seeds, tolerance=60, grow=6, out=None, overlay_path=None, box=None, hue_tol=14.0,
        negatives=(), add_masks=(), sub_masks=()):
    arr = np.asarray(Image.open(image).convert("RGB"))
    close = seeds_mask(arr, seeds, tolerance, hue_tol, negatives)
    if box:
        x0, y0, x1, y1 = box
        clip = np.zeros_like(close)
        clip[y0:y1, x0:x1] = True
        close &= clip           # 模型给的框是硬约束：框外一律不要
    m, _ = grow_mask(seeds, close, grow)
    # 掩膜编辑：并上/减去已有掩膜（模型可以"这块要、那块不要"地逐步修正）
    for p in add_masks:
        other = masks.from_json(json.loads(Path(p).read_text(encoding="utf-8")))
        ob = masks._decode_bits(other)
        mm = np.zeros_like(m)
        mm[other["y"]:other["y"] + ob.shape[0], other["x"]:other["x"] + ob.shape[1]] = ob
        m = m | mm
    for p in sub_masks:
        other = masks.from_json(json.loads(Path(p).read_text(encoding="utf-8")))
        ob = masks._decode_bits(other)
        mm = np.zeros_like(m)
        mm[other["y"]:other["y"] + ob.shape[0], other["x"]:other["x"] + ob.shape[1]] = ob
        m = m & ~mm
    if out:
        Path(out).write_text(json.dumps(masks.to_json(masks.from_bool(m)), ensure_ascii=False),
                             encoding="utf-8")
    if overlay_path:
        overlay(arr, m, overlay_path)
    ys, xs = np.nonzero(m)
    res = {"image": str(image), "seeds": seeds, "box": box, "hue_tol": hue_tol, "tolerance": tolerance, "grow": grow,
           "pixels": int(m.sum()), "share": round(float(m.mean()), 4),
           "bbox": [int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())],
           "mask": out, "overlay": overlay_path}
    return res


def main():
    ap = argparse.ArgumentParser(description="视觉提示（种子点）→ 光栅掩膜")
    ap.add_argument("image")
    ap.add_argument("--seed", action="append", required=True, help="x,y（可重复）")
    ap.add_argument("--tolerance", type=int, default=60, help="明度容差（HSV 的 V）")
    ap.add_argument("--hue-tol", type=float, default=14.0, help="色相容差（度，0-180 制）")
    ap.add_argument("--grow", type=int, default=6, help="膨胀像素，补回笔触边缘")
    ap.add_argument("--out", help="掩膜 JSON（masks.from_bool）")
    ap.add_argument("--overlay", help="检查用 overlay PNG")
    ap.add_argument("--box", help="x0,y0,x1,y1 硬裁剪：框外一律不要（模型给框，防连通泄漏）")
    ap.add_argument("--negative", action="append", default=[], help="负样本点 x,y（排除同色干扰）")
    ap.add_argument("--add-mask", action="append", default=[], help="并上已有掩膜 JSON")
    ap.add_argument("--sub-mask", action="append", default=[], help="减去已有掩膜 JSON")
    a = ap.parse_args()
    seeds = [tuple(int(v) for v in s.split(",")) for s in a.seed]
    box = tuple(int(v) for v in a.box.split(",")) if a.box else None
    negs = [tuple(int(v) for v in s.split(",")) for s in a.negative]
    print(json.dumps(run(a.image, seeds, a.tolerance, a.grow, a.out, a.overlay, box, a.hue_tol,
                         negs, a.add_mask, a.sub_mask), ensure_ascii=False))


if __name__ == "__main__":
    main()
