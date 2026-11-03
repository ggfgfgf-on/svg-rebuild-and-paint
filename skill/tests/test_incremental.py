"""局部改画验收（自包含，不依赖外部素材）。

覆盖：差量 apply / 存读回环 / 单 tile 重绘 / 多 tile 重绘 / 合成缓存补片。
全部以「与整份重画逐位相同」为判定标准。
"""
import sys
import numpy as np
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import paint_native
import plan_store
import paint_diff
import tile_inc

W, H = 200, 160
REGION = np.zeros((H, W), np.int32)   # 全图同一区域，笔触 rid 都是 0
SPECS = [("a", "normal"), ("b", "normal")]
FIELDS = ("pts", "offs", "params", "rids", "sid")


def make_plan(seed=7):
    rng = np.random.default_rng(seed)
    plan = {}
    for name, _ in SPECS:
        strokes = []
        for _ in range(60):
            x, y = float(rng.uniform(12, W - 12)), float(rng.uniform(12, H - 12))
            pts = np.array([[x, y, 0.6],
                            [x + float(rng.uniform(-10, 10)), y + float(rng.uniform(-10, 10)), 0.9]],
                           np.float32)
            strokes.append({"points": pts, "size": float(rng.uniform(5, 13)), "opacity": 0.85,
                            "color": (int(rng.integers(40, 255)), int(rng.integers(40, 255)),
                                      int(rng.integers(40, 255))),
                            "hardness": 0.7, "preset_id": 0})
        plan[name] = dict(zip(FIELDS, plan_store.pack(strokes)))
    return plan


def clone(plan):
    return {k: {f: v[f].copy() for f in FIELDS} for k, v in plan.items()}


def new_painter():
    p = paint_native.Painter(W, H)
    p.add_layer("normal")
    p.clear_layer(0, (18, 18, 18), 1.0)
    for _, bl in SPECS:
        p.add_layer(bl)
    return p


def render(p, plan):
    tile_inc.full(p, plan, SPECS, lambda nm: REGION, gpu=False, nthreads=2)
    return np.ascontiguousarray(p.composite())


def contained(plan, name, rect, limit):
    bb = tile_inc.bboxes(plan, name)
    x0, y0, x1, y1 = rect
    idx = [i for i in range(bb.shape[0])
           if bb[i, 0] >= x0 and bb[i, 2] <= x1 and bb[i, 1] >= y0 and bb[i, 3] <= y1]
    return np.array(idx[:limit], np.int64)


def main():
    ok = True

    def check(label, cond):
        nonlocal ok
        ok = ok and bool(cond)
        print(("PASS " if cond else "FAIL ") + label)

    A = make_plan()
    B = clone(A)

    rect1 = (0, 0, 63, 63)
    rect2 = (120, 80, 183, 143)
    sel1 = contained(A, "a", rect1, 8)
    sel2 = contained(A, "a", rect2, 8)

    par = B["a"]["params"].copy()
    par[sel1, 2:5] = np.clip(par[sel1, 2:5] * 0.4, 0, 1)
    par[sel2, 2:5] = np.clip(par[sel2, 2:5] * 1.4, 0, 1)
    B["a"] = {**B["a"], "params": par}

    gd = B["b"]["offs"]
    pts = B["b"]["pts"].copy()
    pts[gd[0]:gd[1]].reshape(-1, 3)[:, 0] += 6.0
    B["b"] = {**B["b"], "pts": pts}

    # 追加 2 笔
    extra = np.concatenate([np.array([[10, 150, 0.5], [30, 130, 0.8]], np.float32).ravel(),
                            np.array([[170, 20, 0.5], [150, 40, 0.8]], np.float32).ravel()])
    offs = list(B["b"]["offs"])
    offs += [offs[-1] + 6, offs[-1] + 12]
    B["b"] = {**B["b"],
              "pts": np.concatenate([B["b"]["pts"], extra]),
              "offs": np.asarray(offs, np.int32),
              "params": np.concatenate([B["b"]["params"], np.array([[9, 0.9, 1, 0, 0, 1.0, 1.0, 0]] * 2, np.float32)]),
              "rids": np.concatenate([B["b"]["rids"], np.zeros(2, np.int32)]),
              "sid": np.concatenate([B["b"]["sid"], np.array([9001, 9002], np.int32)])}

    # 末尾删 2 笔（再测一次删除）
    plan_b = clone(B)
    keep = plan_b["a"]["offs"].size - 3
    end = int(plan_b["a"]["offs"][keep])
    plan_b["a"] = {**plan_b["a"], "pts": plan_b["a"]["pts"][:end],
                   "offs": plan_b["a"]["offs"][:keep + 1],
                   "params": plan_b["a"]["params"][:keep],
                   "rids": plan_b["a"]["rids"][:keep],
                   "sid": plan_b["a"]["sid"][:keep]}

    diff = paint_diff.make(A, plan_b, SPECS)
    back = paint_diff.apply(A, diff, SPECS)
    check("diff apply == target", all(np.array_equal(back[k][f], plan_b[k][f]) for k, _ in SPECS for f in FIELDS))

    p = new_painter()
    img_a = render(p, A)

    for name, rects in (("single tile", [rect1]), ("multi tile", [rect1, rect2])):
        q = new_painter()
        render(q, A)
        edit = clone(A)
        sel = np.concatenate([contained(A, "a", r, 8) for r in rects])
        par2 = edit["a"]["params"].copy()
        par2[sel, 2:5] = np.clip(par2[sel, 2:5] * 0.3, 0, 1)
        edit["a"] = {**edit["a"], "params": par2}
        tile_inc.rebuild(q, edit, SPECS, {"a": rects}, lambda nm: REGION, gpu=False, nthreads=2)
        got = np.ascontiguousarray(q.composite())
        q.close()
        ref_p = new_painter()
        ref = render(ref_p, edit)
        ref_p.close()
        check(name + " == full rebuild (bitwise)", np.array_equal(got, ref))

    # 缓存补片：连续两次改动后只 patch 脏矩形
    cache_p = new_painter()
    cache = render(cache_p, A)
    cur = clone(A)
    for i, rect in enumerate([rect1, rect2]):
        sel = contained(A, "a", rect, 8)
        par3 = cur["a"]["params"].copy()
        par3[sel, 2:5] = np.clip(par3[sel, 2:5] * 0.5, 0, 1)
        cur["a"] = {**cur["a"], "params": par3}
        tile_inc.rebuild(cache_p, cur, SPECS, {"a": [rect]}, lambda nm: REGION, gpu=False, nthreads=2)
        frame = np.ascontiguousarray(cache_p.composite())
        x0, y0, x1, y1 = rect
        cache[y0:y1 + 1, x0:x1 + 1] = frame[y0:y1 + 1, x0:x1 + 1]
    cache_p.close()
    ref_p = new_painter()
    ref = render(ref_p, cur)
    ref_p.close()
    check("cache patched == full render (bitwise)", np.array_equal(cache, ref))

    print("RESULT", "OK" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
