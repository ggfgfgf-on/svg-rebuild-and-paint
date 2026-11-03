"""plan 差量：按稳定笔触 id 记录改参数 / 改几何 / 增删，支持跨版本结构差异。

用法:
    d = make(plan_a, plan_b, spec)
    save(d, "edit.diff.npz")
    plan_b2 = apply(plan_a, load("edit.diff.npz"), spec)

要求 plan 是 plan_store 的数组形式（含 sid）。代价与实际改动成正比，
与整份 plan 大小无关。
"""
"""diff：基于稳定笔触 id 的改参数 / 改几何 / 增删，支持跨版本结构差异。"""
import json
import numpy as np


def to_map(plan, name):
    d = plan[name]
    offs, pts = d["offs"], d["pts"]
    sid = d["sid"]
    n = offs.size - 1
    out = {}
    for i in range(n):
        b, e = int(offs[i]), int(offs[i + 1])
        out[int(sid[i])] = (i, pts[b:e].copy(), d["params"][i].copy(), int(d["rids"][i]))
    return out


def make(plan_a, plan_b, spec):
    d = {"layers": {}, "stats": {"layers": [], "upd": 0, "geom": 0, "add": 0, "remove": 0}}
    for name, _ in spec:
        a = to_map(plan_a, name) if name in plan_a else {}
        b = to_map(plan_b, name) if name in plan_b else {}
        order = [int(s) for s in plan_b[name]["sid"]] if name in plan_b else []
        ops = {"upd": [], "add": [], "remove": [], "order": order}
        for sid, (_, gb, pb, rid) in b.items():
            if sid in a:
                _, ga, pa, _ = a[sid]
                if pa.shape != pb.shape or np.any(np.abs(pa - pb) > 1e-7):
                    ops["upd"].append(("params", sid, pb))
                if ga.shape != gb.shape or np.any(np.abs(ga - gb) > 1e-6):
                    ops["upd"].append(("geom", sid, (gb, pb)))
            else:
                ops["add"].append((order.index(sid), sid, gb, pb, rid))
        for sid in a:
            if sid not in b:
                ops["remove"].append(sid)
        if ops["upd"] or ops["add"] or ops["remove"]:
            d["layers"][name] = ops
            d["stats"]["layers"].append(name)
            d["stats"]["upd"] += sum(1 for u in ops["upd"] if u[0] == "params")
            d["stats"]["geom"] += sum(1 for u in ops["upd"] if u[0] == "geom")
            d["stats"]["add"] += len(ops["add"])
            d["stats"]["remove"] += len(ops["remove"])
    return d


def apply(plan_a, d, spec):
    out = {}
    for name, _ in spec:
        if name not in plan_a:
            out[name] = {"pts": np.zeros(0, np.float32), "offs": np.asarray([0], np.int32),
                         "params": np.zeros((0, 8), np.float32), "rids": np.zeros(0, np.int32),
                         "sid": np.zeros(0, np.int32)}
            continue
        m = to_map(plan_a, name)
        ops = d["layers"].get(name)
        # (index, block, params, rid, sid) 按原顺序
        lst = [(m[int(sid)][0], m[int(sid)][1], m[int(sid)][2], m[int(sid)][3], int(sid))
               for sid in plan_a[name]["sid"]]
        if ops:
            bysid = {s: k for k, (_, _, _, _, s) in enumerate(lst)}
            for kind, sid, val in ops["upd"]:
                k = bysid[int(sid)]
                if kind == "params":
                    lst[k] = (lst[k][0], lst[k][1], np.asarray(val, np.float32), lst[k][3], lst[k][4])
                else:
                    g, p = val
                    lst[k] = (lst[k][0], np.ascontiguousarray(g, np.float32).ravel(),
                              np.asarray(p, np.float32), lst[k][3], lst[k][4])
            drop = set(int(s) for s in ops["remove"])
            if drop:
                lst = [x for x in lst if x[4] not in drop]
            for pos, sid, g, p, rid in sorted(ops["add"], key=lambda z: z[0]):
                lst.insert(int(pos), (int(pos), np.ascontiguousarray(g, np.float32).ravel(),
                                      np.asarray(p, np.float32), int(rid), int(sid)))
        parts, offs, pars, rids, sids = [], [0], [], [], []
        cur = 0
        for _, block, par, rid, sid in lst:
            parts.append(block); cur += int(block.size); offs.append(cur)
            pars.append(par); rids.append(rid); sids.append(sid)
        out[name] = {"pts": np.concatenate(parts).astype(np.float32) if parts else np.zeros(0, np.float32),
                     "offs": np.asarray(offs, np.int32),
                     "params": np.asarray(pars, np.float32).reshape(-1, 8),
                     "rids": np.asarray(rids, np.int32),
                     "sid": np.asarray(sids, np.int32)}
    return out


def save(d, path):
    arr = {"__meta__": np.frombuffer(json.dumps(d["stats"]).encode(), np.uint8)}
    for name, ops in d["layers"].items():
        up = [u for u in ops["upd"] if u[0] == "params"]
        arr[name + "/upd_sid"] = np.asarray([u[1] for u in up], np.int32)
        arr[name + "/upd_par"] = np.asarray([u[2] for u in up], np.float32).reshape(-1, 8)
        gm = [u for u in ops["upd"] if u[0] == "geom"]
        arr[name + "/geom_sid"] = np.asarray([u[1] for u in gm], np.int32)
        arr[name + "/geom_par"] = np.asarray([u[2][1] for u in gm], np.float32).reshape(-1, 8)
        arr[name + "/geom_pts"] = (np.concatenate([u[2][0].ravel() for u in gm]).astype(np.float32)
                                   if gm else np.zeros(0, np.float32))
        arr[name + "/geom_off"] = (np.cumsum([0] + [u[2][0].size for u in gm]).astype(np.int32)
                                   if gm else np.zeros(1, np.int32))
        arr[name + "/rem"] = np.asarray(ops["remove"], np.int32)
        arr[name + "/add_pos"] = np.asarray([a[0] for a in ops["add"]], np.int32)
        arr[name + "/add_sid"] = np.asarray([a[1] for a in ops["add"]], np.int32)
        arr[name + "/add_rid"] = np.asarray([a[4] for a in ops["add"]], np.int32)
        arr[name + "/add_par"] = np.asarray([a[3] for a in ops["add"]], np.float32).reshape(-1, 8)
        arr[name + "/add_pts"] = (np.concatenate([a[2].ravel() for a in ops["add"]]).astype(np.float32)
                                  if ops["add"] else np.zeros(0, np.float32))
        arr[name + "/add_off"] = (np.cumsum([0] + [a[2].size for a in ops["add"]]).astype(np.int32)
                                  if ops["add"] else np.zeros(1, np.int32))
    np.savez_compressed(path, **arr)


def load(path):
    z = np.load(path)
    d = {"layers": {}, "stats": json.loads(bytes(z["__meta__"]).decode())}
    names = sorted({k.split("/")[0] for k in z.files if "/" in k})
    for name in names:
        ops = {"upd": [], "add": [], "remove": []}
        if name + "/upd_sid" in z.files:
            for sid, par in zip(z[name + "/upd_sid"].tolist(), z[name + "/upd_par"]):
                ops["upd"].append(("params", sid, par))
        if name + "/geom_sid" in z.files and z[name + "/geom_sid"].size:
            pts, off = z[name + "/geom_pts"], z[name + "/geom_off"]
            for k, sid in enumerate(z[name + "/geom_sid"].tolist()):
                seg = pts[off[k]:off[k + 1]].reshape(-1, 3).copy()
                ops["upd"].append(("geom", sid, (seg, z[name + "/geom_par"][k])))
        if name + "/rem" in z.files:
            ops["remove"] = z[name + "/rem"].tolist()
        if name + "/add_sid" in z.files and z[name + "/add_sid"].size:
            pts, off = z[name + "/add_pts"], z[name + "/add_off"]
            for k, sid in enumerate(z[name + "/add_sid"].tolist()):
                seg = pts[off[k]:off[k + 1]].reshape(-1, 3).copy()
                ops["add"].append((int(z[name + "/add_pos"][k]), sid, seg,
                                   z[name + "/add_par"][k], int(z[name + "/add_rid"][k])))
        d["layers"][name] = ops
    return d
