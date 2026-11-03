"""效果 + 增量重绘的管线验收（自包含）。

场景：先整份画出基础图 → 局部改画（tile 重绘）→ 只对脏矩形补算效果。
判定：脏矩形内 == 整份重画 + 整图效果（逐位），矩形外保持不动。
"""
import sys, time
import numpy as np
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import paint_native, plan_store, tile_inc, brush_ext as bx

W, H = 200, 160
SPECS = [("a", "normal"), ("b", "normal")]
FIELDS = ("pts", "offs", "params", "rids", "sid")
REGION = np.zeros((H, W), np.int32)
ok = True


def check(label, cond, extra=""):
    global ok
    ok = ok and bool(cond)
    print(("PASS " if cond else "FAIL ") + label + ("  " + extra if extra else ""))


def make_plan(seed=11):
    rng = np.random.default_rng(seed)
    plan = {}
    for name, _ in SPECS:
        strokes = []
        for _ in range(60):
            x, y = float(rng.uniform(12, W - 12)), float(rng.uniform(12, H - 12))
            pts = np.array([[x, y, 0.6],
                            [x + float(rng.uniform(-12, 12)), y + float(rng.uniform(-12, 12)), 0.9]],
                           np.float32)
            strokes.append({"points": pts, "size": float(rng.uniform(5, 14)), "opacity": 0.85,
                            "color": (int(rng.integers(40, 255)), int(rng.integers(40, 255)),
                                      int(rng.integers(40, 255))), "hardness": 0.7, "preset_id": 0})
        plan[name] = dict(zip(FIELDS, plan_store.pack(strokes)))
    return plan


def new_painter():
    p = paint_native.Painter(W, H)
    p.add_layer("normal")
    p.clear_layer(0, (18, 18, 18), 1.0)
    for _, bl in SPECS:
        p.add_layer(bl)
    return p


def render(plan):
    p = new_painter()
    t0 = time.time()
    n = tile_inc.full(p, plan, SPECS, lambda nm: REGION, gpu=False, nthreads=4)
    return p, np.ascontiguousarray(p.composite()), time.time() - t0, n


def main():
    A = make_plan()
    rect = (0, 0, 79, 79)

    # 基础帧
    p0, base, t_full, n0 = render(A)
    t0 = time.time(); eff_full = bx.impasto_rgba(base); t_eff_full = time.time() - t0

    # 局部改画笔触参数
    B = {k: {f: v.get(f).copy() for f in FIELDS} for k, v in A.items()}
    bb = tile_inc.bboxes(B, "a")
    sel = [i for i in range(bb.shape[0])
           if bb[i, 2] >= rect[0] and bb[i, 0] <= rect[2] and bb[i, 3] >= rect[1] and bb[i, 1] <= rect[3]]
    par = B["a"]["params"].copy()
    par[sel, 2:5] = np.clip(par[sel, 2:5] * 1.5, 0, 1)
    B["a"] = {**B["a"], "params": par}

    # 增量：tile 重绘脏矩形
    p1 = new_painter()
    tile_inc.full(p1, A, SPECS, lambda nm: REGION, gpu=False, nthreads=4)
    t0 = time.time()
    tile_inc.rebuild(p1, B, SPECS, {"a": [rect]}, lambda nm: REGION, gpu=False, nthreads=4)
    inc = np.ascontiguousarray(p1.composite())
    t_inc = time.time() - t0

    # 只对脏矩形补算效果（带 halo）
    t0 = time.time()
    eff_inc = bx.apply_in_rect(inc, rect, lambda c, ox, oy: bx.impasto_rgba(c), pad=2)[0]
    t_eff_inc = time.time() - t0

    # 参照：整份重画 + 整图效果
    _, full2, t_full2, _ = render(B)
    eff_full2 = bx.impasto_rgba(full2)

    x0, y0, x1, y1 = rect
    check("increment == full rebuild (base)", np.array_equal(inc[y0:y1 + 1, x0:x1 + 1],
                                                             full2[y0:y1 + 1, x0:x1 + 1]))
    check("effect in rect == full effect", np.array_equal(eff_inc[y0:y1 + 1, x0:x1 + 1],
                                                         eff_full2[y0:y1 + 1, x0:x1 + 1]))
    outside = np.ones((H, W), bool); outside[y0:y1 + 1, x0:x1 + 1] = False
    check("outside rect untouched by effect", np.array_equal(eff_inc[outside], inc[outside]))
    check("effect changes something", float(np.abs(eff_full2 - full2).max()) > 0.05)
    check("pipeline deterministic", np.array_equal(
        bx.apply_in_rect(inc, rect, lambda c, ox, oy: bx.impasto_rgba(c), pad=2)[0], eff_inc))

    print("times  full_render=%.2fs tile_rebuild=%.3fs effect_full=%.3fs effect_rect=%.4fs"
          % (t_full, t_inc, t_eff_full, t_eff_inc))
    print("RESULT", "OK" if ok else "FAILED")


if __name__ == "__main__":
    main()
