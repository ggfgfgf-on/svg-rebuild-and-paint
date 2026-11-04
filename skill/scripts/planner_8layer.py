"""九层规划：色床/明度带/线稿/阴影(multiply)/环境色(overlay)/高光(screen)/罩染(softlight)/纸纹(multiply)。

风格数字全部来自 native/style.json；沉积量剖面来自 native/profiles.json；
笔刷校准来自 native/brush.json。本文件只留算法与调度。
"""
import sys, os, time, json
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
import numpy as np, cv2
import paint_native
import brush_calib


# --- Windows spawn 根治：子进程不再 import 调用方的主模块 ---
# multiprocessing 的 spawn 会把主模块路径写进准备数据，子进程无条件重新 import 它。
# 任何忘了写 if __name__ == "__main__": 的脚本都会因此被反复执行（fork bomb）。
# 这里的任务只用模块级可 pickle 的函数/整数，不需要主模块，所以直接剥掉这些键。
def _dsh_no_main_reimport():
    try:
        import multiprocessing.spawn as _sp
        if getattr(_sp, "_dsh_patched", False):
            return
        _orig = _sp.get_preparation_data

        def _prep(name=None):
            d = _orig(name)
            d.pop("main_path", None)
            d.pop("init_main_from_path", None)
            d.pop("init_main_from_name", None)
            return d

        _sp.get_preparation_data = _prep
        _sp._dsh_patched = True
    except Exception:
        pass


_dsh_no_main_reimport()


def _arg(flag, default=None, conv=str):
    if flag in sys.argv:
        try:
            return conv(sys.argv[sys.argv.index(flag) + 1])
        except (IndexError, ValueError):
            return default
    return default


STYLE = brush_calib.load_style(_arg("--style-file"))


def S(*keys, default=None):
    return brush_calib.style_get(STYLE, keys, default)


PATHS = brush_calib.load_paths(_arg("--paths-file"))

_VALUE_FLAGS = {"--layer-parallel": 1, "--workers": 1, "--plan": 1, "--ink-tip": 1, "--profiles-file": 1, "--deposit": 1, "--dep-profile": 1,
                "--dep-spread": 1, "--tex-op": 1, "--threads": 1, "--style-file": 1,
                "--paths-file": 1, "--profile": 1, "--preset": 1, "--edge": 4}


def _positional():
    """只取不带 -- 的位置参数，跳过带值选项的参数（避免把 --tips 当文件名）。"""
    out, i = [], 1
    while i < len(sys.argv):
        a = sys.argv[i]
        n = _VALUE_FLAGS.get(a)
        if n is not None:
            i += 1 + n
            continue
        if not a.startswith("--"):
            out.append(a)
        i += 1
    return out


POS = _positional()
REF = POS[0] if len(POS) > 0 else PATHS.get("reference", "rose5-full.png")
REG = POS[1] if len(POS) > 1 else PATHS.get("regions", "regions2.npz")
OUT = POS[2] if len(POS) > 2 else PATHS.get("output", "demo")
PAPER = POS[3] if len(POS) > 3 else S("render", "paper", default="02_rough-canvas")
THREADS = _arg("--threads", S("render", "threads", default=4), int)
TEX_OP = _arg("--tex-op", S("texture_layer", "opacity", default=0.30), float)
LAYER_PARALLEL = _arg("--layer-parallel", 1, int)
WORKERS = _arg("--workers", 0, int)
if WORKERS <= 0:
    WORKERS = min(4, max(2, (os.cpu_count() or 4) // 8))
DEP_SPREAD = _arg("--dep-spread", S("deposit_profile", "spread", default=0.12), float)
EDGE_ON = S("edge_strokes", "enabled", default=True)
EDGE_LO, EDGE_HI = S("edge_strokes", "size", default=[9.0, 14.0])
EDGE_OP = S("edge_strokes", "opacity", default=0.75)
EDGE_TINT = S("edge_strokes", "tint", default=1.0)
if "--edge" in sys.argv:
    EDGE_LO, EDGE_HI, EDGE_OP, EDGE_TINT = [float(x) for x in sys.argv[sys.argv.index("--edge") + 1:][:4]]
INK_TIP = _arg("--ink-tip")
BRUSH_TABLE = json.loads((_HERE / "native" / "brush.json").read_text(encoding="utf-8"))
_samples_file = _arg("--profiles-file")
DEP_PROFILE = {"bed": 0.0, "band": 0.0, "edge": 0.0, "ink": 0.0}
for _flag in ("--profile", "--preset"):
    if _flag in sys.argv:
        DEP_PROFILE.update(brush_calib.get_profile(sys.argv[sys.argv.index(_flag) + 1], _samples_file))
if "--deposit" in sys.argv:
    DEP_PROFILE["bed"] = float(_arg("--deposit", 0.0, float))
if "--dep-profile" in sys.argv:
    DEP_PROFILE.update(brush_calib.parse_profile(_arg("--dep-profile")))
USE_TIPS = "--tips" in sys.argv

ref = None
H = W = 0
rid = lum = lum_s = gy = gx = gmag = None


def _bootstrap():
    """参考图/区域/梯度：只在主进程算一次；worker 通过 init() 收数组，不重跑这里。"""
    global ref, H, W, rid, lum, lum_s, gy, gx, gmag
    from PIL import Image
    ref = np.asarray(Image.open(REF).convert("RGB"))
    H, W = ref.shape[:2]
    rid = np.load(REG)["rid"]
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


def estimate_light(mask, sigma_frac=None):
    """主光方向：重度模糊亮度的梯度加权和（指向更亮的一侧，单位向量）。"""
    import math
    sig = sigma_frac if sigma_frac is not None else S("light", "sigma_frac", default=0.10)
    sig = max(8.0, max(H, W) * sig)
    Lb = cv2.GaussianBlur(lum, (0, 0), sig)
    gy2, gx2 = np.gradient(Lb)
    w = mask.astype(np.float32)
    vx = float((gx2 * w).sum())
    vy = float((gy2 * w).sum())
    n = math.hypot(vx, vy) + 1e-9
    return vx / n, vy / n


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


FG = None


TIPS = {}


def build(seed, p):
    global FG, TIPS
    tips = paint_native.load_catalog(p) if USE_TIPS else {}
    TIPS = tips
    dep = S("deposit_profile", default={})
    ink_cfg = S("ink", default={})
    pools_cfg = dep.get("pools", {})

    INK_DEP = None
    if DEP_PROFILE.get("ink", 0.0) > 0 and tips:
        ink_pool = brush_calib.pool_by_keywords(BRUSH_TABLE, *pools_cfg.get("ink", ["liner"]))
        pick = brush_calib.pick_for_deposit(BRUSH_TABLE, ink_pool, DEP_PROFILE["ink"],
                                            radius=dep.get("ink_radius", 0.9))
        if pick and pick["key"][4:] in tips:
            INK_DEP = (pick["size"], pick["opacity"], tips[pick["key"][4:]])
            print("ink deposit", DEP_PROFILE["ink"], "->", pick["key"][4:],
                  "size", round(pick["size"], 2), "opacity", round(pick["opacity"], 3))

    INK_CAL = None
    if INK_TIP and INK_TIP in tips:
        entry = BRUSH_TABLE.get("tip:" + INK_TIP)
        if entry:
            i9 = int(round(0.9 * 32))
            r9, a9 = entry["r"][i9], entry["a"][i9]
            INK_CAL = (2.0 * ink_cfg.get("tip_radius", 1.0) / max(r9, 1e-3),
                       ink_cfg.get("tip_alpha", 0.6) / max(a9, 1e-3), tips[INK_TIP])
            print("ink tip", INK_TIP, "r@0.9", r9, "a@0.9", a9,
                  "-> size", round(INK_CAL[0], 2), "opacity", round(INK_CAL[1], 3))

    rng = np.random.default_rng(seed)

    def tip(names, fallback):
        """笔尖多样选择：命中名字组就用真笔尖 id，否则回退公式预设。"""
        if tips:
            for n in names:
                if n in tips:
                    return tips[n]
        return fallback

    fg_cfg = S("foreground", default={})
    band_n = int(S("foreground", "bg_band", default=20))
    bgc = np.median(np.concatenate([ref[:band_n].reshape(-1, 3), ref[-band_n:].reshape(-1, 3),
                                    ref[:, :band_n].reshape(-1, 3), ref[:, -band_n:].reshape(-1, 3)]), 0)
    fg = (np.abs(ref.astype(np.float32) - bgc).max(2) > fg_cfg.get("color_diff", 26)) & (rid >= 0)
    dk = fg_cfg.get("dilate", 5)
    fg = cv2.dilate(fg.astype(np.uint8), np.ones((dk, dk), np.uint8)).astype(bool)
    FG = np.where(fg, 0, -1).astype(np.int32)
    print("fg px", int(fg.sum()), "of", H * W)

    layers = {name: [] for name in ("bed", "band", "ink", "shadow", "ambient", "highlight", "glaze", "texture")}
    hard_default = S("render", "hardness_default", default=0.7)

    def add(k, pts, size, opacity, col, preset, hardness=None, r=0):
        layers[k].append({"points": np.asarray(pts, np.float32), "size": float(size),
                          "opacity": float(opacity),
                          "color": np.clip(np.asarray(col, np.float32), 0, 255),
                          "preset": preset,
                          "hardness": float(hard_default if hardness is None else hardness),
                          "region_id": int(r)})

    POOL_BED = brush_calib.pool_by_keywords(BRUSH_TABLE, *pools_cfg.get("bed", []))
    POOL_BAND = brush_calib.pool_by_keywords(BRUSH_TABLE, *pools_cfg.get("band", []))
    POOL_EDGE = brush_calib.pool_by_keywords(BRUSH_TABLE, *pools_cfg.get("edge", []))
    dep_used = {"bed": {}, "band": {}, "edge": {}}

    def dep_pick(layer, radius):
        """按该层的目标沉积量选笔；未启用返回 None。"""
        if DEP_PROFILE.get(layer, 0.0) <= 0 or not tips:
            return None
        pool = {"bed": POOL_BED, "band": POOL_BAND, "edge": POOL_EDGE}.get(layer, [])
        if not pool:
            return None
        pick = brush_calib.pick_many(BRUSH_TABLE, pool, rng, DEP_PROFILE[layer],
                                     radius=radius, spread=DEP_SPREAD)
        if pick and pick["key"][4:] in tips:
            key = pick["key"][4:]
            dep_used[layer][key] = dep_used[layer].get(key, 0) + 1
            return tips[key], pick["size"], pick["opacity"]
        return None

    # ---------- 线稿 ----------
    blur = cv2.GaussianBlur(ref, (5, 5), ink_cfg.get("blur_sigma", 1.6))
    cs = ink_cfg.get("canny", [55, 140])
    edges = cv2.Canny(cv2.cvtColor(blur, cv2.COLOR_RGB2GRAY), cs[0], cs[1])
    cnts, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
    ink_size = ink_cfg.get("size", [1.3, 2.0])
    ink_patch = int(ink_cfg.get("patch_radius", 2))
    for ct in cnts:
        if len(ct) < ink_cfg.get("min_len", 24):
            continue
        pa = ct[:, 0, :]
        stepn = max(1, len(pa) // int(ink_cfg.get("segments", 30)))
        for s0 in range(0, len(pa) - 1, stepn):
            seg = pa[s0:s0 + stepn + 1]
            if len(seg) < 2:
                continue
            xi, yi = int(seg[len(seg) // 2][0]), int(seg[len(seg) // 2][1])
            rr = int(rid[yi, xi])
            patch = ref[max(0, yi - ink_patch):yi + ink_patch + 1,
                        max(0, xi - ink_patch):xi + ink_patch + 1].reshape(-1, 3)
            dark = np.percentile(patch, ink_cfg.get("dark_pct", 15), axis=0) * ink_cfg.get("dark_mul", 0.75)
            pts = np.stack([seg[:, 0].astype(np.float32), seg[:, 1].astype(np.float32),
                            np.full(len(seg), ink_cfg.get("pressure", 0.9), np.float32)], 1)
            if INK_DEP is not None:
                add("ink", pts, INK_DEP[0], INK_DEP[1], dark, INK_DEP[2],
                    ink_cfg.get("hardness", 0.85), rr if rr >= 0 else -2)
            elif INK_CAL is not None:
                add("ink", pts, INK_CAL[0], INK_CAL[1], dark, INK_CAL[2],
                    ink_cfg.get("hardness", 0.85), rr if rr >= 0 else -2)
            else:
                add("ink", pts, rng.uniform(ink_size[0], ink_size[1]), ink_cfg.get("opacity", 0.6),
                    dark, "liner", ink_cfg.get("hardness", 0.85), rr if rr >= 0 else -2)

    # ---------- 主光方向 ----------
    _tm = [time.time()]
    def _tlab(lab):
        import os
        if os.environ.get("PAINT_TIMING"):
            _now = time.time(); print("[plan] %s %.0f ms" % (lab, (_now - _tm[0]) * 1000)); _tm[0] = _now
    LX, LY = estimate_light(fg)
    print("light dir", round(LX, 3), round(LY, 3))
    _tlab("light")

    reg = S("region", default={})
    ed = S("edge_strokes", default={})
    bd = S("band", default={})
    sh = S("shadow", default={})
    band_n = int(bd.get("count", 6))

    ids, counts = np.unique(rid[rid >= 0], return_counts=True)
    region_ids = [int(x) for x in ids[np.argsort(-counts)]]
    import plan_region
    cfg = {"style": STYLE, "dep": dict(DEP_PROFILE), "dep_spread": DEP_SPREAD,
           "pools": {"bed": POOL_BED, "band": POOL_BAND, "edge": POOL_EDGE},
           "brush": BRUSH_TABLE, "names": set(tips) if tips else set(), "use_tips": bool(tips),
           "seed": int(seed), "edge_lo": EDGE_LO, "edge_hi": EDGE_HI,
           "edge_op": EDGE_OP, "edge_tint": EDGE_TINT, "light": (LX, LY),
           "cpp_plan": "--cpp-plan" in sys.argv}
    flat = rid.ravel()
    _order = np.argsort(flat, kind="stable")
    _srt = flat[_order]
    _rng_ids = np.arange(int(rid.max()) + 1)
    _starts = np.searchsorted(_srt, _rng_ids)
    _ends = np.searchsorted(_srt, _rng_ids, side="right")
    _xs = (_order % W).astype(np.int32)
    _ys = (_order // W).astype(np.int32)
    _tlab("region tables")
    init_args = (ref, lum, rid, gx, gy, gmag, cfg, _xs, _ys, _starts, _ends)
    _tm = [time.time()]
    def _tlab(lab):
        import os
        if os.environ.get("PAINT_TIMING"):
            _now = time.time(); print("[plan] %s %.0f ms" % (lab, (_now - _tm[0]) * 1000)); _tm[0] = _now
    import os as _os2
    _pool_init, _pool_args = plan_region.init, init_args
    if _os2.environ.get("PAINT_MMAP") == "1":
        import tempfile, shutil
        _td = os.path.join(tempfile.gettempdir(), "paint_plan_%d" % os.getpid())
        os.makedirs(_td, exist_ok=True)
        _paths = {}
        for _k, _v in (("ref", ref), ("lum", lum), ("rid", rid), ("gx", gx), ("gy", gy),
                       ("gmag", gmag), ("xs", _xs), ("ys", _ys), ("starts", _starts), ("ends", _ends)):
            _fp = os.path.join(_td, _k + ".npy")
            np.save(_fp, _v)
            _paths[_k] = _fp
        _pool_init, _pool_args = plan_region.init_paths, (_paths, cfg)
        import atexit
        atexit.register(shutil.rmtree, _td, True)
    plan_region.init(*init_args)
    _tlab("init")
    if WORKERS > 1 and len(region_ids) > 2:
        import os as _os
        if _os.environ.get("PAINT_THREADS") == "1":
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=WORKERS) as ex:
                chunks = list(ex.map(plan_region.region_task, region_ids))
            _tlab("threads.map")
        else:
            import multiprocessing as mp
            with mp.Pool(WORKERS, initializer=_pool_init, initargs=_pool_args) as pool:
                chunks = pool.map(plan_region.region_task, region_ids, chunksize=int(_os.environ.get("PAINT_CHUNK", "8")))
            _tlab("pool.map")
        print("parallel regions", len(region_ids), "workers", WORKERS)
    else:
        chunks = [plan_region.region_task(r) for r in region_ids]
    name2id = dict(tips) if tips else {}
    _tlab("regions ready")
    for chunk in chunks:
        if not chunk:
            continue
        pts_all, offs, metas, cols = chunk
        for i, (k, preset, size, op, hard, rr) in enumerate(metas):
            if isinstance(preset, (int, np.integer)):
                pid = int(preset)
            else:
                pid = name2id.get(preset, paint_native.PRESETS.get(preset, preset))
            layers[k].append({"points": pts_all[offs[i]:offs[i + 1]], "size": size,
                              "opacity": op, "color": cols[i], "preset": pid,
                              "hardness": hard, "region_id": rr})
    _tlab("assemble strokes")

    # ---------- 环境色 ----------
    am = S("ambient", default={})
    cold = ref.reshape(-1, 3).mean(0) * np.array(am.get("color_mul", [0.82, 0.92, 1.15]))
    sp = int(am.get("spacing", 120)); half = am.get("half", 60)
    for y in range(0, H, sp):
        for x in range(0, W, sp):
            if not fg[y, x]:
                continue
            add("ambient", [[x - half, y, am.get("pressure", 0.7)], [x + half, y, am.get("pressure", 0.7)]],
                am.get("size", 260.0), am.get("opacity", 0.05), cold, am.get("preset", "round_soft"),
                am.get("hardness", 0.5), 0)

    _tlab("ambient")
    # ---------- 高光 ----------
    hl = S("highlight", default={})
    thr = np.percentile(lum, hl.get("pct", 98))
    hy, hx = np.nonzero((lum >= thr) & (rid >= 0))
    hlen = hl.get("len", [5, 12]); hsize = hl.get("size", [3.0, 6.0])
    for k in range(0, len(hx), int(hl.get("step", 6))):
        xi, yi = int(hx[k]), int(hy[k])
        ang = np.arctan2(gy[yi, xi], gx[yi, xi]) + np.pi / 2
        s = rng.uniform(hlen[0], hlen[1])
        add("highlight", [[xi - np.cos(ang) * s, yi - np.sin(ang) * s, hl.get("pressure", 0.9)],
                          [xi + np.cos(ang) * s, yi + np.sin(ang) * s, hl.get("pressure", 0.9)]],
            rng.uniform(hsize[0], hsize[1]), hl.get("opacity", 0.32),
            ref[yi, xi] * hl.get("color_mul", 1.05) + hl.get("color_add", 8),
            tip(hl.get("tip_names", []), hl.get("fallback", "round_hard")), hl.get("hardness", 0.9), 0)

    _tlab("highlight")
    # ---------- 罩染 ----------
    gl = S("glaze", default={})
    warm = np.array(gl.get("color", [255.0, 214.0, 170.0]))
    gsp = int(gl.get("spacing", 150)); ghalf = gl.get("half", 60)
    for y in range(0, H, gsp):
        for x in range(0, W, gsp):
            if not fg[y, x]:
                continue
            add("glaze", [[x - ghalf, y, gl.get("pressure", 0.7)], [x + ghalf, y, gl.get("pressure", 0.7)]],
                gl.get("size", 280.0), gl.get("opacity", 0.045), warm, gl.get("preset", "round_soft"),
                gl.get("hardness", 0.4), 0)

    _tlab("glaze")
    # ---------- 纸纹层 ----------
    tx = S("texture_layer", default={})
    if tips and PAPER and PAPER not in tips:
        print("warning: paper", repr(PAPER), "not in tip catalog; texture layer skipped")
    if tips and PAPER in tips:
        tsp = int(tx.get("spacing", 46)); thalf = tx.get("half", 24)
        for y in range(0, H, tsp):
            for x in range(0, W, tsp):
                if not fg[y, x]:
                    continue
                add("texture", [[x - thalf, y, tx.get("pressure", 0.7)],
                                [x + thalf, y, tx.get("pressure", 0.7)]],
                    tx.get("size", 96.0), TEX_OP, ref[y, x], tips[PAPER], tx.get("hardness", 0.5), 0)

    _tlab("texture")
    if any(DEP_PROFILE.values()):
        print("dep-profile", DEP_PROFILE, "pools", len(POOL_BED), len(POOL_BAND), len(POOL_EDGE),
              "used", {k: (len(v), sorted(v.items(), key=lambda kv: -kv[1])[:2]) for k, v in dep_used.items()})
    return layers



def main():
    t0 = time.time()
    from export_layers import write_kra, write_psd
    from PIL import Image
    _bootstrap()
    p = paint_native.Painter(W, H)
    p.add_layer("normal"); p.clear_layer(0, tuple(int(v) for v in ref[2, 2]), 1.0)
    spec = [(l["name"], l.get("blend", "normal")) for l in S("layers", default=[])]
    _t0 = time.time()
    layers = build(21, p)
    _hi_mode = _arg("--highlight-mode", "drop")
    if _hi_mode == "drop":
        # highlight 层 1,838 个 splat 点几乎全落在叶子上（screen 后叶子 +17 luma），
        # 玫瑰只 +1.4；旧 raster 出图没有这层，所以默认去掉，--highlight-mode keep 可恢复。
        layers["highlight"] = []
    _sh_mode = _arg("--shadow-mode", "drop")
    if _sh_mode != "keep":
        try:
            import rebuild_shadow_layer as _rs
            _before = len(layers.get("shadow") or [])
            layers["shadow"] = _rs.apply_shadow_mode(layers.get("shadow") or [], W, H,
                                                     mode=_sh_mode, seed=int(_arg("--seed", 21, int)))
            print("shadow-mode %s: %d -> %d strokes" % (_sh_mode, _before, len(layers["shadow"])))
        except Exception as ex:
            print("shadow-mode skipped:", ex)
    _t_build = time.time() - _t0
    for name, blend in spec:
        p.add_layer(blend)
    p.set_region(rid)
    subj_layers = {"ambient", "glaze", "highlight", "texture"}
    import render_util as _RU
    def _send(idx, name, nthreads):
        # 笔触发送只有一条路径：render_util.send_strokes（planner / 标定 / 测试共用）
        reg = FG if name in subj_layers else rid
        _RU.send_strokes(p, idx, layers[name], nthreads=nthreads, region=reg)

    outs = []
    todo = [(i + 1, nm) for i, (nm, bl) in enumerate(spec) if layers.get(nm)]
    use_lp = LAYER_PARALLEL > 1 and len(todo) > 1
    if use_lp:
        from concurrent.futures import ThreadPoolExecutor
        per = max(1, THREADS // LAYER_PARALLEL)
        with ThreadPoolExecutor(max_workers=LAYER_PARALLEL) as ex:
            list(ex.map(lambda a: _send(a[0], a[1], per), todo))
    else:
        for idx, nm in todo:
            _send(idx, nm, THREADS)
    for i, (name, blend) in enumerate(spec):
        outs.append((name, p.layer_rgba(i + 1)))
    _t_render = time.time() - _t0
    comp = p.composite()
    p.close()
    Image.fromarray(comp, "RGBA").save(OUT + ".png")
    _blends = {n: b for n, b in spec}
    write_kra(OUT + ".kra", outs, merged=comp, blends=_blends)
    write_psd(OUT + ".psd", outs, merged=comp, blends=_blends)
    print("timing: plan/build %.1fs | stamp+marshal %.1fs" % (_t_build, _t_render - _t_build))
    _plan_out = _arg("--plan")
    if _plan_out:
        import plan_io
        _all = [dict(st, layer=nm) for nm, sts in layers.items() for st in sts]
        _reg = _plan_out.rsplit(".", 1)[0] + ".regions.npz"
        np.savez_compressed(_reg, rid=rid, fg=FG)
        _n = plan_io.dump_plan(_plan_out, W, H, _all, layer_blends={n: b for n, b in spec},
                               regions=_reg, subject_layers=sorted(subj_layers),
                               tip_names={v: k for k, v in TIPS.items()})
        print("plan written", _plan_out, _n, "strokes")
    stats = {k: len(v) for k, v in layers.items()}
    _report = _arg("--report")
    if _report:
        # 亮度与统计写进报告：出图后不用再手算，也方便和参考图对比
        import render_util as _RU2
        _obj = {"paper": PAPER, "tips": USE_TIPS, "canvas": [W, H],
                "layers": [{n: b} for n, b in spec],
                "blends": {n: b for n, b in spec},
                "strokes": stats, "total": sum(stats.values()),
                "luma": round(_RU2.luma_of(comp), 2),
                "outputs": {ext: OUT + ext for ext in (".png", ".kra", ".psd")},
                "plan": _plan_out or None,
                "seconds": round(time.time() - t0, 2)}
        with open(_report, "w", encoding="utf-8") as f:
            json.dump(_obj, f, ensure_ascii=False, indent=1)
        print("report written", _report, "luma", _obj["luma"])
    _cal = _arg("--calibrate")
    if _cal and _plan_out:
        # 用参考图现场反解 tone gain（不硬编码），写出可直接喂给 render_plan 的 ops
        import calibrate_tone as _CT
        import plan_io as _PIO
        import render_util as _RU3
        _doc, _w, _h = _PIO.load_plan(_plan_out)
        _target = _RU3.target_luma_from_image(_cal)
        _gain, _luma, _renders = _CT.calibrate(_doc, _target, gpu=False, nthreads=THREADS)
        _ops_path = _plan_out.rsplit(".", 1)[0] + ".tone.json"
        _specs = [{"op": "tone_map", "gain": _gain}]
        with open(_ops_path, "w", encoding="utf-8") as f:
            json.dump(_specs, f, ensure_ascii=False, indent=1)
        print(json.dumps({"calibrate": _cal, "target_luma": round(_target, 2),
                          "gain": _gain, "achieved_luma": _luma, "renders": _renders,
                          "ops": _ops_path}, ensure_ascii=False))
    print(json.dumps({"paper": PAPER, "tips": USE_TIPS, "strokes": stats,
                      "total": sum(stats.values()), "seconds": round(time.time() - t0, 2)}))


if __name__ == "__main__":
    main()
