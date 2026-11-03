"""plan 的二进制快存：每层 points / offsets / params / region_ids / stroke_ids。

与 plan_io.py 的分工：plan_io 是 JSON 交换格式（可读、可 diff），这里是渲染用的
数组快路径（几十万笔也能秒开）。offsets 是 **float 索引**（C 端约定），末尾带总长度。
"""
"""plan 的二进制存取：每层 = points / offsets / params / region_ids / stroke_ids。"""
import json
import numpy as np

META = "__meta__"


def pack(strokes):
    parts, offs, params, rids = [], [], [], []
    cur = 0
    for st in strokes:
        a = np.ascontiguousarray(st["points"], np.float32).reshape(-1, 3)
        parts.append(a.ravel()); offs.append(cur); cur += a.size
        col = st["color"]
        params.append([float(st["size"]), float(st["opacity"]), col[0] / 255.0, col[1] / 255.0,
                       col[2] / 255.0, float(st.get("hardness", 0.7)), 1.0, float(st["preset_id"])])
        rids.append(int(st.get("region_id", 0)))
    offs.append(cur)
    pts = np.concatenate(parts) if parts else np.zeros(0, np.float32)
    sid = np.arange(len(strokes), dtype=np.int32)
    return (pts.astype(np.float32), np.asarray(offs, np.int32),
            np.asarray(params, np.float32).reshape(-1, 8), np.asarray(rids, np.int32), sid)


def save(path, layers, spec):
    arrays = {}
    for name, blend in spec:
        pts, offs, params, rids, sid = pack(layers.get(name) or [])
        arrays[name + "/pts"] = pts
        arrays[name + "/offs"] = offs
        arrays[name + "/params"] = params
        arrays[name + "/rids"] = rids
        arrays[name + "/sid"] = sid
    arrays[META] = np.frombuffer(json.dumps({"spec": spec}).encode(), np.uint8)
    np.savez_compressed(path, **arrays)


def normalize(plan):
    """补齐旧文件缺失的字段：offs 末尾总长度、stroke id。"""
    for name, d in plan.items():
        n = int(d["params"].shape[0])
        if d["offs"].size == n:
            d["offs"] = np.concatenate([d["offs"], np.asarray([int(d["pts"].size)], np.int32)])
        if "sid" not in d or d["sid"].size != n:
            d["sid"] = np.arange(n, dtype=np.int32)
    return plan


def load(path):
    z = np.load(path)
    meta = json.loads(bytes(z[META]).decode())
    plan = {}
    for name, _ in meta["spec"]:
        if name + "/pts" not in z.files:
            continue
        plan[name] = {"pts": z[name + "/pts"], "offs": z[name + "/offs"],
                      "params": z[name + "/params"], "rids": z[name + "/rids"]}
        if name + "/sid" in z.files:
            plan[name]["sid"] = z[name + "/sid"]
    return normalize(plan), meta


def write(path, plan, spec):
    arrays = {}
    for name, _ in spec:
        d = plan[name]
        for f in ("pts", "offs", "params", "rids", "sid"):
            if f in d:
                arrays[name + "/" + f] = d[f]
    arrays[META] = np.frombuffer(json.dumps({"spec": spec}).encode(), np.uint8)
    np.savez_compressed(path, **arrays)
