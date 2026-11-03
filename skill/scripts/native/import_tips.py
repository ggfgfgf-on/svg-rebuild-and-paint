
"""把一堆笔尖文件（Krita .kpp / PNG / 灰度图）规范化进 native/tips/ 并写 index.json。

用法：python import_tips.py <源目录> [--limit N]
.kpp 本身就是 PNG（带 Krita 元数据块），PIL 能直接开；取 alpha 作笔尖掩膜。
"""
import json, re, sys, argparse
from pathlib import Path
import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import paint_native

EXT = {".kpp", ".png", ".gbr", ".gih"}


def _load_gimp(path, kind):
    """Krita bundle 笔尖：前 20 字节是包裹头 [size, ver, w, h, bpp]，像素数据在文件尾部。

    .gbr = 单格；.gih = 多格（文本头里有 ncells），取第一格。
    """
    b = Path(path).read_bytes()
    if len(b) < 40:
        return None
    # .gih 前面还有文本头，包裹头在 "GIMP" 前 20 字节；.gbr 的包裹头就在文件开头
    base = 0
    if kind == ".gih":
        j = b.find(b"GIMP")
        if j < 20:
            return None
        base = j - 20
    w = int.from_bytes(b[base + 8:base + 12], "big")
    h = int.from_bytes(b[base + 12:base + 16], "big")
    bpp = int.from_bytes(b[base + 16:base + 20], "big")
    if not (2 <= w <= 4096 and 2 <= h <= 4096 and bpp in (1, 2, 4)):
        return None
    cells = 1
    if kind == ".gih":
        m = re.search(rb"ncells:(\d+)", b[:400])
        if m:
            cells = max(1, int(m.group(1)))
    cell = w * h * bpp
    need = cell * cells
    if len(b) < need:
        return None
    px = b[len(b) - need:len(b) - need + cell]
    if bpp == 1:
        return np.frombuffer(px, np.uint8).reshape(h, w).astype(np.float32) / 255.0
    arr = np.frombuffer(px, np.uint8).reshape(h, w, bpp)
    return arr[..., 3 if bpp == 4 else 0].astype(np.float32) / 255.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("--dst", default=str(paint_native.TIPS_DIR))
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    src, dst = Path(a.src), Path(a.dst)
    dst.mkdir(parents=True, exist_ok=True)
    files = [p for p in sorted(src.rglob("*")) if p.suffix.lower() in EXT]
    if a.limit:
        files = files[:a.limit]
    index, skipped = [], 0
    for p in files:
        try:
            if p.suffix.lower() in (".gbr", ".gih"):
                arr = _load_gimp(p, p.suffix.lower())
            else:
                arr = paint_native.load_tip_image(p)
        except Exception:
            skipped += 1
            continue
        if arr is None or arr.size < 16:
            skipped += 1
            continue
        # 过滤"预设预览图"：近乎整块、且四边都触边 → 不是笔尖
        cov = float((arr > 0.5).mean())
        edges = [arr[0].max(), arr[-1].max(), arr[:, 0].max(), arr[:, -1].max()]
        if p.suffix.lower() in (".kpp", ".png") and cov > 0.85 and all(e > 0.5 for e in edges):
            skipped += 1
            continue
        name = p.stem.lower().replace(" ", "_").replace(")", "").replace("(", "")
        out = dst / (name + ".png")
        Image.fromarray((np.clip(arr, 0, 1) * 255).astype(np.uint8)).save(out)
        index.append({"name": name, "w": int(arr.shape[1]), "h": int(arr.shape[0]), "src": p.name})
    (dst / "index.json").write_text(json.dumps(index, indent=1), encoding="utf-8")
    print(json.dumps({"imported": len(index), "skipped": skipped, "dir": str(dst)}))


if __name__ == "__main__":
    main()
