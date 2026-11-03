"""笔刷校准辅助函数：查表插值、按目标沉积量选笔、反推尺寸与不透明度。

表结构（native/brush.json）：
  {名字: {"r": [33], "a": [33], "dep": [33], "kind"?: "bitmap"}}
  索引对应压力 0.0..1.0 等分 33 点；r=实际半径/标称半径，a=峰值覆盖，dep=沉积量/面积。

用法：
  from brush_calib import load_table, at, pick_for_deposit, size_for_radius, opacity_for_alpha
"""
import json
from pathlib import Path

import numpy as np

N = 33
PRES = np.linspace(0.0, 1.0, N)
_TABLE_PATH = Path(__file__).resolve().parent / "native" / "brush.json"


def load_table(path=None):
    p = Path(path) if path else _TABLE_PATH
    return json.loads(p.read_text(encoding="utf-8"))


def _interp(arr, pressure):
    x = float(np.clip(pressure, 0.0, 1.0)) * (N - 1)
    i = int(np.floor(x))
    j = min(i + 1, N - 1)
    f = x - i
    return float(arr[i]) * (1.0 - f) + float(arr[j]) * f


def at(table, name, pressure):
    """返回 (r, a, dep) 在任意压力下的插值；名字不存在返回 None。"""
    e = table.get(name)
    if not e:
        return None
    return (_interp(e["r"], pressure), _interp(e["a"], pressure), _interp(e["dep"], pressure))


def size_for_radius(table, name, pressure, target_radius):
    """要让实际半径等于 target_radius，标称尺寸该给多少。"""
    r, _, _ = at(table, name, pressure)
    return 2.0 * target_radius / max(r, 1e-4)


def opacity_for_alpha(table, name, pressure, target_alpha):
    """要让峰值覆盖等于 target_alpha，不透明度该给多少（上限 1）。"""
    _, a, _ = at(table, name, pressure)
    return float(min(1.0, target_alpha / max(a, 1e-4)))


_DEP_CACHE = {}


def _dep_matrix(table, pool, op_max):
    """把「池 × 压力点」的候选预排成矩阵，按池缓存；顺序与逐个循环完全一致。"""
    key = (id(table), tuple(pool), float(op_max))
    hit = _DEP_CACHE.get(key)
    if hit is not None:
        return hit
    idx = np.where((PRES >= 0.25) & (PRES <= 1.0))[0]
    names, deps, rs, alphas, press = [], [], [], [], []
    for name in pool:
        e = table.get(name)
        if not e:
            continue
        for i in idx:
            dep_i = float(e["dep"][i])
            if dep_i <= 1e-6:
                continue
            names.append(name)
            deps.append(dep_i)
            rs.append(float(e["r"][i]))
            alphas.append(float(e["a"][i]))
            press.append(float(PRES[i]))
    arr = (names, np.asarray(deps, np.float64), rs, alphas, press)
    _DEP_CACHE[key] = arr
    return arr


def pick_for_deposit(table, pool, target_dep, radius, pressure_min=0.25, pressure_max=1.0,
                     op_max=1.0):
    """在候选笔尖里找「沉积量最接近目标」的组合。

    返回 dict(key, pressure, r, a, dep, size, opacity, err)；找不到返回 None。
    评分 = |实际沉积 - 目标|，实际沉积 = dep(压力) × 反推不透明度。
    与逐笔循环版本逐位一致：候选顺序相同、并列取第一个。
    """
    names, deps, rs, alphas, press = _dep_matrix(table, pool, op_max)
    m = deps.size
    if m == 0:
        return None
    target = float(target_dep)
    op = np.minimum(op_max, target / deps)
    ok = op <= op_max
    score = np.abs(deps - target) + 0.5 * np.abs(1.0 - op) * target
    score = np.where(ok, score, np.inf)
    k = int(np.argmin(score))
    if not ok[k]:
        return None
    dep_i = float(deps[k])
    return {"key": names[k], "pressure": press[k],
            "r": rs[k], "a": alphas[k], "dep": dep_i,
            "size": 2.0 * radius / max(rs[k], 1e-4),
            "opacity": float(op[k]), "err": abs(dep_i * float(op[k]) - target),
            "score": float(score[k])}


def pick_many(table, pool, rng, target_dep, radius, spread=0.12, op_max=1.0):
    """带抖动的版本：目标沉积量在 ±spread 内随机，避免整片死平。"""
    dep = float(target_dep) * float(rng.uniform(1.0 - spread, 1.0 + spread))
    return pick_for_deposit(table, pool, dep, radius, op_max=op_max)


def parse_profile(text):
    """把 "0.30,0.18,0.45" 或 "0.30,0.18,0.45,0.55" 解析成 {bed,band,edge,ink}；缺省项不覆盖。"""
    keys = ("bed", "band", "edge", "ink")
    out = {}
    for k, v in zip(keys, [x for x in str(text).split(",")]):
        v = v.strip()
        if v:
            out[k] = float(v)
    return out


_PROFILES_PATH = Path(__file__).resolve().parent / "native" / "profiles.json"
_STYLE_PATH = Path(__file__).resolve().parent / "native" / "style.json"


def load_style(path=None):
    """读取风格数据文件（所有笔触尺寸/不透明度/密度/阈值）。"""
    p = Path(path) if path else _STYLE_PATH
    if not p.exists():
        raise FileNotFoundError("style file not found: %s" % p)
    return json.loads(p.read_text(encoding="utf-8"))


_PATHS_PATH = Path(__file__).resolve().parent / "native" / "paths.json"


def load_paths(path=None):
    """读取工作流路径默认值。"""
    p = Path(path) if path else _PATHS_PATH
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def style_get(style, keys, default=None):
    """按路径取值：style_get(STYLE, ("band","count"), 6)。"""
    cur = style
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


def load_profiles(path=None):
    """读取沉积量剖面表（外部数据文件，非代码常量）。"""
    p = Path(path) if path else _PROFILES_PATH
    if not p.exists():
        return {}
    data = json.loads(p.read_text(encoding="utf-8"))
    return {k: v for k, v in data.items() if not k.startswith("_")}


def get_profile(name, path=None):
    """按名字取剖面；不存在时抛出带可选名字的错误。"""
    table = load_profiles(path)
    if name not in table:
        raise KeyError("unknown profile %r; available: %s" % (name, ", ".join(sorted(table))))
    prof = dict(table[name])
    for k in ("bed", "band", "edge", "ink"):
        prof.setdefault(k, 0.0)
    return prof


def list_profiles(path=None):
    return sorted(load_profiles(path))


def pool_by_keywords(table, *keywords, prefix="tip:"):
    """按名字关键词挑候选笔尖（返回带前缀的键）。"""
    return [k for k in table if k.startswith(prefix) and any(s in k for s in keywords)]


def report(table, pool, targets=(0.20, 0.30, 0.40), radius=35.0):
    rows = []
    for t in targets:
        b = pick_for_deposit(table, pool, t, radius)
        if b:
            rows.append({"target": t, "key": b["key"], "p": round(b["pressure"], 3),
                         "dep": round(b["dep"], 4), "size": round(b["size"], 1),
                         "opacity": round(b["opacity"], 3), "err": round(b["err"], 4)})
    return rows


if __name__ == "__main__":
    import sys
    t = load_table()
    pool = [k for k in t if k.startswith("tip:")]
    if len(sys.argv) > 1 and sys.argv[1] == "info":
        name = sys.argv[2]
        for p in (0.3, 0.6, 0.9):
            print(name, "p=%.1f" % p, ["%.4f" % v for v in at(t, name, p)])
    else:
        print(json.dumps(report(t, pool, radius=35.0), ensure_ascii=False, indent=1))
