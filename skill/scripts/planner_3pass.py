
"""三遍实测规划器：色床 -> 明度带 -> 线稿 -> 提亮。

做法参考 daub-planner 公开描述的思路（三遍实测 + 笔刷沉积量表），代码为本项目自写。
- 色床：每个面色区域一个基色，大笔平涂
- 明度带：面内亮度分位数量化到 6 带，逐带补笔（笔触可见，不插值）
- 线稿：Canny 链取最暗一档颜色，压在色床之上
- 提亮：最亮 3% 点笔
沉积量见 native/brush.json 的 dep 字段（压力 -> 单位面积覆盖当量）。
"""
import sys, time, json, hashlib
sys.path.insert(0, r"D:\deepseek-harness-dsh-v0.2.1rc2\.dsh-home\skills\generic-pixel-svg-rebuild\scripts")
import numpy as np, cv2
from PIL import Image, ImageFilter
import paint_native
from export_layers import write_kra, write_psd

import argparse

def parse_args(argv=None):
    ap = argparse.ArgumentParser(description="三遍实测规划器")
    ap.add_argument("ref", nargs="?", default=r"D:\DSHwork\flower\rose5-full.png")
    ap.add_argument("regions", nargs="?", default=r"D:\DSHwork\flower\plan\regions2.npz")
    ap.add_argument("out", nargs="?", default=r"D:\DSHwork\flower\plan\demo4")
    ap.add_argument("--bed-min", type=float, default=14.0)
    ap.add_argument("--bed-max", type=float, default=40.0)
    ap.add_argument("--bed-gap", type=float, default=0.55)      # 行距 = 笔宽 * 该系数
    ap.add_argument("--band-min", type=float, default=4.0)
    ap.add_argument("--band-max", type=float, default=14.0)
    ap.add_argument("--band-scale", type=float, default=1.1)
    ap.add_argument("--band-gap", type=float, default=0.95)
    ap.add_argument("--bands", type=int, default=6)
    ap.add_argument("--band-op", type=float, default=0.85)
    ap.add_argument("--jitter", type=float, default=0.06)
    ap.add_argument("--threads", type=int, default=8)
    ap.add_argument("--clamp-gain", type=float, default=1.6)    # 笔宽上限 = 区域厚度 * 该系数
    ap.add_argument("--no-clamp", action="store_true")
    ap.add_argument("--textures", action="store_true")   # 明度带改用目录里的真实笔尖          # 关掉厚度限幅
    return ap.parse_args(argv)


ARGS = parse_args()
REF, REG, OUT = ARGS.ref, ARGS.regions, ARGS.out
THREADS = ARGS.threads
N_BANDS = ARGS.bands

ref = np.asarray(Image.open(REF).convert("RGB"))
H, W = ref.shape[:2]
rid = np.load(REG)["rid"]
brush = paint_native.Painter.load_brush()
lum = (ref @ np.array([0.299, 0.587, 0.114], np.float32))
lum_s = cv2.GaussianBlur(lum, (0, 0), 1.4)
gy, gx = np.gradient(lum_s)
gmag = np.hypot(gx, gy)


def region_thickness(mask):
    """区域平均厚度 = 2 * 面积 / 周长（等面积、等周长的圆直径），细瓣自动得小值。"""
    m8 = mask.astype(np.uint8)
    cnts, _ = cv2.findContours(m8, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not cnts:
        return 0.0
    peri = max(cv2.arcLength(c, True) for c in cnts)
    area = float(m8.sum())
    if peri <= 1.0:
        return float(np.sqrt(area))
    return 2.0 * area / peri


def main_dir(mask):
    ys, xs = np.nonzero(mask)
    if len(xs) < 12:
        return 0.0
    ang = np.arctan2(gy[ys, xs], gx[ys, xs]) + np.pi / 2
    w = gmag[ys, xs] + 0.05
    return float(0.5 * np.arctan2((np.sin(2 * ang) * w).sum(), (np.cos(2 * ang) * w).sum()))


def hatch(mask, a0, gap, thick, minlen, maxlen, rng):
    """沿方向 a0 成行取样，返回若干条折线（坐标 n×3: x,y,pressure）。"""
    ys, xs = np.nonzero(mask)
    if len(xs) < 6:
        return []
    t = np.array([np.cos(a0), np.sin(a0)]); nrm = np.array([-t[1], t[0]])
    pn = xs * nrm[0] + ys * nrm[1]; pt = xs * t[0] + ys * t[1]
    r0 = pn.min(); nb = int((pn.max() - r0) / gap) + 1
    bins = ((pn - r0) / gap).astype(np.int32)
    out = []
    for b in range(nb):
        sel = np.nonzero(bins == b)[0]
        if len(sel) < 3:
            continue
        sel = sel[np.argsort(pt[sel])]
        ts = pt[sel]
        brk = np.nonzero(np.diff(ts) > gap * 1.8)[0]
        for s0, en in zip(np.r_[0, brk + 1], np.r_[brk, len(ts) - 1]):
            seg = sel[s0:en + 1]
            L = ts[en] - ts[s0]
            if L < minlen or len(seg) < 3:
                continue
            nseg = max(1, int(np.ceil(L / maxlen)))
            step = max(1, len(seg) // nseg)
            for c0 in range(0, len(seg) - 1, step):
                part = seg[c0:c0 + step + 1]
                if len(part) < 2:
                    continue
                px = xs[part].astype(np.float32); py = ys[part].astype(np.float32)
                pr = np.clip(1.05 - lum[ys[part], xs[part]] / 255.0 * 0.35, 0.6, 1.0).astype(np.float32)
                out.append(np.stack([px, py, pr], 1))
    return out


TEX_BY_BAND = ["14_texture-rock", "07_big-grain", "03_default-paper",
               "04_paper-c-grain", "chalk", "chalk_round_hard"]


def build(seed, P):
    rng = np.random.default_rng(seed)
    # 画布先建好：位图笔尖要注册进同一个 ctx，id 才能用
    p = paint_native.Painter(W, H)
    p.add_layer("normal"); p.clear_layer(0, tuple(int(v) for v in ref[2, 2]), 1.0)
    for _ in range(3):
        p.add_layer("normal")
    p.add_layer("multiply")
    tips = paint_native.load_catalog(p) if P.textures else None
    layers = {"ink": [], "bed": [], "band": [], "lift": [], "texture": []}

    def add(name, pts, size, op, col, preset, hardness=0.7, rid_=0):
        layers[name].append({"points": np.asarray(pts, np.float32), "size": float(size),
                             "opacity": float(op),
                             "color": np.clip(np.asarray(col, np.float32), 0, 255),
                             "preset": preset, "hardness": float(hardness), "region_id": int(rid_)})

    # ---------- 1) 线稿：Canny 链 + 暗部结构 ----------
    blur = cv2.GaussianBlur(ref, (5, 5), 1.6)
    edges = cv2.Canny(cv2.cvtColor(blur, cv2.COLOR_RGB2GRAY), 55, 140)
    cnts, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
    ink_n = 0
    for ct in cnts:
        if len(ct) < 24:
            continue
        pa = ct[:, 0, :]
        stepn = max(1, len(pa) // 30)
        for s0 in range(0, len(pa) - 1, stepn):
            seg = pa[s0:s0 + stepn + 1]
            if len(seg) < 2:
                continue
            xi, yi = int(seg[len(seg) // 2][0]), int(seg[len(seg) // 2][1])
            rr = int(rid[yi, xi])
            x0, x1 = max(0, xi - 2), min(W, xi + 3)
            y0, y1 = max(0, yi - 2), min(H, yi + 3)
            patch = ref[y0:y1, x0:x1].reshape(-1, 3)
            dark = np.percentile(patch, 15, axis=0) * 0.75      # 结构线取"最暗一档"
            pts = np.stack([seg[:, 0].astype(np.float32), seg[:, 1].astype(np.float32),
                            np.full(len(seg), 0.9, np.float32)], 1)
            add("ink", pts, rng.uniform(1.3, 2.0), 0.6, dark, "liner",
                hardness=0.85, rid_=rr if rr >= 0 else -2)
            ink_n += 1

    # ---------- 2) 色床 + 3) 明度带 ----------
    ids, counts = np.unique(rid[rid >= 0], return_counts=True)
    order = ids[np.argsort(-counts)]
    band_n = bed_n = 0
    for r in order:
        m = rid == r
        area = float(m.sum())
        if area < 50:
            continue
        ys, xs = np.nonzero(m)
        base = np.median(ref[ys, xs], 0)
        a0 = main_dir(m)

        # 色床：大笔平涂，行距 ~ 笔宽的一半
        thick = region_thickness(m)
        bed_hi = P.bed_max
        if not P.no_clamp and thick > 0:
            bed_hi = min(P.bed_max, max(P.bed_min, thick * P.clamp_gain))
        bed_size = float(np.clip(np.sqrt(area) / 3.2, P.bed_min, bed_hi))
        bed_a0 = a0 + rng.normal(0, 0.05)
        for pts in hatch(m, bed_a0, bed_size * P.bed_gap, bed_size, 8, 240, rng):
            add("bed", pts, bed_size, 0.98, base * rng.uniform(0.98, 1.02), "round_hard",
                hardness=0.85, rid_=int(r))
            bed_n += 1

        # 明度带：区域内按亮度分位数量化
        lvals = lum[ys, xs]
        qs = np.percentile(lvals, np.linspace(0, 100, N_BANDS + 1))
        for bi in range(N_BANDS):
            lo, hi = qs[bi], qs[bi + 1]
            bm = m & (lum >= lo) & (lum <= hi)
            cnt = int(bm.sum())
            if cnt < 40:
                continue
            bys, bxs = np.nonzero(bm)
            bcol = np.median(ref[bys, bxs], 0)
            bsize = float(np.clip(np.sqrt(cnt) / 4.0, P.band_min, P.band_max)) * P.band_scale
            if not P.no_clamp:
                bsize = min(bsize, max(P.band_min, thick * 0.55))
            ba0 = main_dir(bm)
            # 带内换笔实测：只有 flat 的定向沟槽能留住结构；dry/sponge/chalk 的孔洞
            # 会被相邻笔触（0.85 不透明度、行距 0.95）填平，反而更平滑，故大面积带用 flat。
            preset = "dry" if 1 <= bi <= 3 else "flat"

            for pts in hatch(bm, ba0, bsize * P.band_gap, bsize * 1.1, 6, 120, rng):
                add("band", pts, bsize * 1.1, P.band_op,
                    bcol * rng.uniform(1 - P.jitter, 1 + P.jitter),
                    preset, hardness=0.7, rid_=int(r))
                band_n += 1

    # ---------- 4) 纹理叠加：真纸纹笔尖，只在中性色区低不透明度叠一层 ----------
    if tips:
        tex_names = [n for n in ("03_default-paper", "04_paper-c-grain", "01_canvas",
                                 "05_paper-torchon") if n in tips]
        if tex_names:
            for y in range(0, H, 46):
                for x in range(0, W, 46):
                    if rid[y, x] < 0:
                        continue
                    nm = tex_names[(x // 46 + y // 46) % len(tex_names)]
                    add("texture", [[x - 24, y, 0.7], [x + 24, y, 0.7]], 96.0, 0.30,
                        ref[y, x], tips[nm], rid_=int(rid[y, x]))

    # ---------- 5) 提亮：最亮 3% 的点笔 ----------
    thr = np.percentile(lum, 97)
    hy, hx = np.nonzero((lum >= thr) & (rid >= 0))
    for k in range(0, len(hx), 6):
        xi, yi = int(hx[k]), int(hy[k])
        ang = np.arctan2(gy[yi, xi], gx[yi, xi]) + np.pi / 2
        Ls = rng.uniform(6, 14)
        add("lift", [[xi - np.cos(ang) * Ls / 2, yi - np.sin(ang) * Ls / 2, 0.9],
                     [xi + np.cos(ang) * Ls / 2, yi + np.sin(ang) * Ls / 2, 0.9]],
            rng.uniform(3.0, 5.0), 0.42, ref[yi, xi] * 1.06, "round_hard", rid_=int(rid[yi, xi]))

    p.set_region(rid)
    for i, key in enumerate(("bed", "band", "ink", "lift", "texture")):
        if layers[key]:
            p.stroke_batch(i, layers[key], nthreads=THREADS)
    comp = p.composite()
    outs = [("ink", p.layer_rgba(0)), ("bed", p.layer_rgba(1)),
            ("band", p.layer_rgba(2)), ("lift", p.layer_rgba(3)),
            ("texture", p.layer_rgba(4))]
    p.close()
    stats = {"ink": ink_n, "bed": bed_n, "band": band_n, "lift": len(layers["lift"]),
             "texture": len(layers["texture"])}
    return comp, outs, stats


t0 = time.time()
comp, outs, stats = build(21, ARGS)
Image.fromarray(comp, "RGBA").save(OUT + ".png")
write_kra(OUT + ".kra", outs, merged=comp)
write_psd(OUT + ".psd", outs, merged=comp)
t1 = time.time()
h1 = hashlib.sha256(comp.tobytes()).hexdigest()[:16]
comp2, _, _ = build(21, ARGS)
h2 = hashlib.sha256(comp2.tobytes()).hexdigest()[:16]
print(json.dumps({"strokes": stats, "total": sum(stats.values()),
                  "seconds": round(t1 - t0, 2), "deterministic": h1 == h2}))
