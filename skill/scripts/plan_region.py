"""区域笔画生成（像素表版）：不再对全画布做 numpy 掩膜扫描。

主进程一次性算出每个区域的像素坐标（region_pix），worker 只按切片取用。
"""
import numpy as np
import cv2
import brush_calib
import paint_native

_G = {}


def init(ref, lum, rid, gx, gy, gmag, cfg, xs=None, ys=None, starts=None, ends=None):
    _G.update(ref=ref, lum=lum, rid=rid, gx=gx, gy=gy, gmag=gmag, cfg=cfg,
              H=ref.shape[0], W=ref.shape[1],
              xs=xs, ys=ys, starts=starts, ends=ends)


def init_paths(paths, cfg):
    def _ld(k):
        return np.load(paths[k], mmap_mode="r")
    init(_ld("ref"), _ld("lum"), _ld("rid"), _ld("gx"), _ld("gy"), _ld("gmag"), cfg,
         _ld("xs"), _ld("ys"), _ld("starts"), _ld("ends"))


def _pix(r):
    xs, ys = _G["xs"], _G["ys"]
    a, b = int(_G["starts"][r]), int(_G["ends"][r])
    return xs[a:b], ys[a:b]


def region_thickness(xs, ys):
    """厚度估计：区域内接圆直径近似（面积/周长 * 2），用裁剪掩膜算一次轮廓。"""
    x0, x1 = int(xs.min()), int(xs.max())
    y0, y1 = int(ys.min()), int(ys.max())
    bw, bh = x1 - x0 + 1, y1 - y0 + 1
    m = np.zeros((bh, bw), np.uint8)
    m[ys - y0, xs - x0] = 1
    cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not cnts:
        return 0.0
    peri = max(cv2.arcLength(c, True) for c in cnts)
    area = float(m.sum())
    if peri <= 1.0:
        return float(np.sqrt(area))
    return 2.0 * area / peri


def main_dir(xs, ys, sel=None):
    gx, gy, gmag = _G["gx"], _G["gy"], _G["gmag"]
    if sel is not None:
        xs, ys = xs[sel], ys[sel]
    if len(xs) < 12:
        return 0.0
    ang = np.arctan2(gy[ys, xs], gx[ys, xs]) + np.pi / 2
    w = gmag[ys, xs] + 0.05
    return float(0.5 * np.arctan2((np.sin(2 * ang) * w).sum(), (np.cos(2 * ang) * w).sum()))


def hatch_sel(xs, ys, sel, a0, gap, minlen, maxlen):
    """用区域像素 + 选中掩膜走 C++ 内核；只扫区域包围盒。"""
    if sel is not None:
        xs, ys = xs[sel], ys[sel]
    if len(xs) < 6:
        return []
    x0, x1 = int(xs.min()), int(xs.max())
    y0, y1 = int(ys.min()), int(ys.max())
    bw, bh = x1 - x0 + 1, y1 - y0 + 1
    m = np.zeros((bh, bw), np.uint8)
    m[ys - y0, xs - x0] = 1
    lum_crop = _G["lum"][y0:y1 + 1, x0:x1 + 1]
    lines = paint_native.paint_hatch(m, a0, gap, minlen, maxlen, lum_crop)
    out = []
    for p in lines:
        q = np.array(p, np.float32, copy=True)
        q[:, 0] += x0
        q[:, 1] += y0
        out.append(q)
    return out


def _pack(out, pts, cols):
    """(点数组, 偏移, 元数据, 颜色)：压成大数组跨进程传，颜色整块裁剪一次。"""
    if not pts:
        return (np.zeros((0, 3), np.float32), np.zeros(1, np.int32), out,
                np.zeros((0, 3), np.float32))
    lens = np.fromiter((len(p) for p in pts), np.int32, len(pts))
    offs = np.zeros(len(pts) + 1, np.int32)
    np.cumsum(lens, out=offs[1:])
    carr = np.clip(np.array(cols, np.float32).reshape(-1, 3), 0, 255)
    return (np.concatenate(pts, axis=0), offs, out, carr)


def region_task(r):
    import os as _os
    if _os.environ.get("PAINT_NOOP") == "1":
        return []
    cfg = _G["cfg"]
    ref, lum = _G["ref"], _G["lum"]
    style = cfg["style"]
    dep = cfg["dep"]
    pools = cfg["pools"]
    brush = cfg["brush"]
    names = cfg["names"]
    ed = style.get("edge_strokes", {})
    reg = style.get("region", {})
    bd = style.get("band", {})
    sh = style.get("shadow", {})
    out = []
    _pts = []
    _cols = []
    rng = np.random.default_rng([cfg["seed"], int(r)])

    xs, ys = _pix(r)
    area = float(len(xs))
    if area < reg.get("min_area", 50):
        return _pack(out, _pts, _cols)

    def tip(cands, fallback):
        for n in cands:
            if n in names:
                return n
        return fallback

    def dep_pick(layer, radius):
        if dep.get(layer, 0.0) <= 0 or not cfg["use_tips"]:
            return None
        pool = pools.get(layer) or []
        if not pool:
            return None
        pick = brush_calib.pick_many(brush, pool, rng, dep[layer], radius=radius,
                                     spread=cfg["dep_spread"])
        if pick and pick["key"][4:] in names:
            return pick["key"][4:], pick["size"], pick["opacity"]
        return None

    def add(k, pts, size, opacity, col, preset, hardness):
        _pts.append(np.asarray(pts, np.float32))
        _cols.append((float(col[0]), float(col[1]), float(col[2])))
        out.append((k, preset, float(size), float(opacity), float(hardness), int(r)))

    base = np.median(ref[ys, xs], 0)
    thick = region_thickness(xs, ys)
    a0 = main_dir(xs, ys)
    bed_hi = min(reg.get("bed_max", 80.0), max(reg.get("bed_min", 60.0), thick * reg.get("bed_thick_mul", 1.6)))
    bed_size = float(np.clip(np.sqrt(area) / reg.get("bed_area_div", 3.2), reg.get("bed_min", 60.0), bed_hi))
    bh = reg.get("bed_hatch", {})
    cj = reg.get("bed_color_jitter", [0.98, 1.02])
    every = max(1, int(reg.get("rock_every", 3)))
    band_n = int(bd.get("count", 6))
    lvals = lum[ys, xs] if not cfg.get("cpp_plan") else None
    qs = np.percentile(lvals, np.linspace(0, 100, band_n + 1)) if lvals is not None else None
    bj = bd.get("color_jitter", [0.94, 1.06])
    bhat = bd.get("hatch", {})
    scl = bd.get("size_scale", 1.5)
    LX, LY = cfg["light"]
    cpp_plan = bool(cfg.get("cpp_plan"))

    if cpp_plan:
        params = dict(band_count=band_n, bed_min=float(reg.get("bed_min", 60.0)),
                      bed_max=float(reg.get("bed_max", 80.0)),
                      bed_thick_mul=float(reg.get("bed_thick_mul", 1.6)),
                      bed_area_div=float(reg.get("bed_area_div", 3.2)),
                      bed_opacity=float(reg.get("bed_opacity", 0.98)),
                      bed_gap_frac=float(bh.get("gap_frac", 0.55)),
                      bed_minlen=float(bh.get("minlen", 8)), bed_maxlen=float(bh.get("maxlen", 240)),
                      bed_thick=float(bed_size),
                      band_size_div=float(bd.get("size_div", 4.0)),
                      band_clamp_lo=float(bd.get("size_clamp", [4.0, 16.0])[0]),
                      band_clamp_hi=float(bd.get("size_clamp", [4.0, 16.0])[1]),
                      band_size_scale=float(scl), band_thick_frac=float(bd.get("thick_frac", 0.55)),
                      band_gap_frac=float(bhat.get("gap_frac", 0.95)),
                      band_stroke_scale=float(bhat.get("stroke_scale", 1.1)),
                      band_minlen=float(bhat.get("minlen", 6)), band_maxlen=float(bhat.get("maxlen", 120)),
                      band_opacity=float(bd.get("opacity", 0.85)), angle=float(a0),
                      thick=float(thick), band_min_px=int(bd.get("min_px", 40)),
                      sh_bands=int(sh.get("bands", 2)),
                      sh_off_frac=float(sh.get("off_frac", 0.55)),
                      sh_gap_mul=float(sh.get("hatch", {}).get("gap_mul", 1.6)),
                      sh_stroke_scale=float(sh.get("hatch", {}).get("stroke_scale", 1.4)),
                      sh_opacity=float(sh.get("opacity", 0.42)),
                      sh_color_mul=float(sh.get("color_mul", 0.72)),
                      sh_minlen=float(sh.get("hatch", {}).get("minlen", 6)),
                      sh_maxlen=float(sh.get("hatch", {}).get("maxlen", 90)),
                      lx=float(LX), ly=float(LY))
        raw = paint_native.plan_region_emit(xs, ys, lum, ref, params, _G["gx"], _G["gy"], _G["gmag"])
        rock_names = reg.get("rock_names", [])
        edge_names = bd.get("edge_tip_names", [])
        dark_from = bd.get("dark_from", 1); dark_to = bd.get("dark_to", 3)
        for (layer, band, size, op, cr, cg, cb, pts) in raw:
            if layer == 0:
                key = (int(band) % every)
                preset = tip(rock_names, "round_hard") if key else "round_hard"
                add("bed", pts, size, op, np.array([cr, cg, cb]) * rng.uniform(cj[0], cj[1]), preset, 0.9)
            elif layer == 2:
                add("shadow", pts, size, op, np.array([cr, cg, cb]),
                    sh.get("preset", "flat"), sh.get("hardness", 0.8))
            else:
                if band == 0 or band == band_n - 1:
                    preset = tip(edge_names, "dry" if dark_from <= band <= dark_to else "flat")
                else:
                    preset = ("dry" if dark_from <= band <= dark_to else "flat")
                add("band", pts, size, op, np.array([cr, cg, cb]) * rng.uniform(bj[0], bj[1]),
                    preset, bd.get("hardness", 0.7))
    else:
        for bi_, pts in enumerate(hatch_sel(xs, ys, None, a0 + rng.normal(0, reg.get("bed_angle_jitter", 0.05)),
                                            bed_size * bh.get("gap_frac", 0.55),
                                            bh.get("minlen", 8), bh.get("maxlen", 240))):
            picked = dep_pick("bed", bed_size / 2.0)
            if picked:
                add("bed", pts, picked[1], picked[2], base * rng.uniform(cj[0], cj[1]), picked[0], 0.9)
                continue
            rock = "round_hard" if bi_ % every else tip(reg.get("rock_names", []), "round_hard")
            add("bed", pts, bed_size, reg.get("bed_opacity", 0.98), base * rng.uniform(cj[0], cj[1]), rock, 0.9)

    if names and ed.get("enabled", True):
        chalk = tip(ed.get("chalk_names", []), "dry")
        x0, x1 = int(xs.min()), int(xs.max())
        y0, y1 = int(ys.min()), int(ys.max())
        m = np.zeros((y1 - y0 + 1, x1 - x0 + 1), np.uint8)
        m[ys - y0, xs - x0] = 1
        cts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        ecj = ed.get("color_jitter", [0.95, 1.05])
        for c in cts:
            if len(c) < ed.get("contour_min", 12):
                continue
            step = max(4, len(c) // int(ed.get("segments", 36)))
            for k in range(0, len(c) - 1, step):
                px0, py0 = c[k][0]
                px1, py1 = c[k + 1][0]
                ang = np.arctan2(py1 - py0, px1 - px0)
                Lh = rng.uniform(*ed.get("len", [4, 9]))
                esize = rng.uniform(cfg["edge_lo"], cfg["edge_hi"])
                epick = dep_pick("edge", esize / 2.0)
                e_preset = epick[0] if epick else chalk
                e_size = epick[1] if epick else esize
                e_op = epick[2] if epick else cfg["edge_op"]
                add("bed", [[px0 + x0 - np.cos(ang) * Lh, py0 + y0 - np.sin(ang) * Lh, ed.get("pressure", 0.85)],
                            [px0 + x0 + np.cos(ang) * Lh, py0 + y0 + np.sin(ang) * Lh, ed.get("pressure", 0.85)]],
                    e_size, e_op, base * cfg["edge_tint"] * rng.uniform(ecj[0], ecj[1]), e_preset,
                    ed.get("hardness", 0.8))

    if cpp_plan:
        return _pack(out, _pts, _cols)
    for bi in range(band_n):
        sel = (lvals >= qs[bi]) & (lvals <= qs[bi + 1])
        cnt = int(sel.sum())
        if cnt < bd.get("min_px", 40):
            continue
        bcol = np.median(ref[ys[sel], xs[sel]], 0)
        sc = bd.get("size_clamp", [4.0, 16.0])
        bsize = min(float(np.clip(np.sqrt(cnt) / bd.get("size_div", 4.0), sc[0], sc[1])) * scl,
                    max(6.0, thick * bd.get("thick_frac", 0.55)))
        ba0 = main_dir(xs, ys, sel)
        preset = (bd.get("dark_preset", "dry")
                  if bd.get("dark_from", 1) <= bi <= bd.get("dark_to", 3)
                  else bd.get("light_preset", "flat"))
        if bi == 0 or bi == band_n - 1:
            preset = tip(bd.get("edge_tip_names", []), preset)
        bstroke = bsize * bhat.get("stroke_scale", 1.1)
        if cpp_plan:
            pts_iter = []
        else:
            pts_iter = hatch_sel(xs, ys, sel, ba0, bsize * bhat.get("gap_frac", 0.95),
                                 bhat.get("minlen", 6), bhat.get("maxlen", 120))
        for pts in pts_iter:
            bpick = dep_pick("band", bstroke / 2.0)
            if bpick:
                add("band", pts, bpick[1], bpick[2], bcol * rng.uniform(bj[0], bj[1]), bpick[0],
                    bd.get("hardness", 0.7))
            else:
                add("band", pts, bstroke, bd.get("opacity", 0.85), bcol * rng.uniform(bj[0], bj[1]),
                    preset, bd.get("hardness", 0.7))
        if bi < sh.get("bands", 2):
            off = sh.get("off_frac", 0.55) * bsize
            shat = sh.get("hatch", {})
            for pts in hatch_sel(xs, ys, sel, ba0, bsize * shat.get("gap_mul", 1.6),
                                 shat.get("minlen", 6), shat.get("maxlen", 90)):
                pp = np.asarray(pts, np.float32).copy()
                pp[:, 0] += -LX * off
                pp[:, 1] += -LY * off
                add("shadow", pp, bsize * shat.get("stroke_scale", 1.4), sh.get("opacity", 0.42),
                    bcol * sh.get("color_mul", 0.72), sh.get("preset", "flat"), sh.get("hardness", 0.8))
    return _pack(out, _pts, _cols)
