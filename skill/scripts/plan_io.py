"""plan JSON 契约：与渲染器解耦的笔画目录（可导出、可导入、可被其它工具消费）。

契约（与 daub 的 plan 同构，字段略宽）：
{
  "canvas": [w, h],
  "layers": [{"name": "bed", "blend": "normal"}, ...],   // 默认首次出现顺序；layer_order 可显式
  "strokes": [
    {"layer": "bed", "preset": "round_hard", "size": 12, "opacity": 0.9,
     "color": "#2244cc" 或 [r,g,b], "hardness": 0.7,
     "points": [[x, y, pressure], ...]}
  ]
}
未知 preset 直接报错并给出候选，绝不静默回退。
"""
import sys, json, difflib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np
import paint_native


def _preset_map(painter=None):
    m = {k: v for k, v in paint_native.PRESETS.items()}
    return m


def resolve_preset(name, painter, allow_tips=True, catalog=None):
    """名字 -> id。公式预设直接命中；位图笔尖需已注册；未知则报错并给候选。

    catalog 可传入「已注册好的 {名字: id}」——批量解析时务必复用，
    否则每笔都会重扫 native/tips/*.png 并重建 278 个笔尖（O(笔触数 × 笔尖数)）。"""
    presets = paint_native.PRESETS
    if name in presets:
        return presets[name]
    if allow_tips and painter is not None:
        cat = catalog if catalog is not None else paint_native.load_catalog(painter)
        if name in cat:
            return cat[name]
        cands = difflib.get_close_matches(name, list(presets) + list(cat), n=3, cutoff=0.4)
    else:
        cands = difflib.get_close_matches(name, list(presets), n=3, cutoff=0.4)
    raise ValueError("unknown brush preset %r%s" % (name, ("; did you mean: " + ", ".join(cands)) if cands else ""))


def _color(c):
    if isinstance(c, str):
        c = c.lstrip("#")
        return [int(c[i:i + 2], 16) for i in (0, 2, 4)]
    return [float(x) for x in c]


def dump_plan(path, w, h, strokes, layer_blends=None, regions=None, subject_layers=None, tip_names=None, layer_order=None):
    """strokes: [{layer,preset,size,opacity,color,hardness,points}]（preset 可为 id 或名字）。

    layer_order: 可选层序（如 ["bed","subject","light"]）。不给时按首笔出现顺序；
    给定时先按它排，未列出的笔画层按首笔追加。显式层序可包含没有笔画的空层。
    """
    layer_blends = layer_blends or {}
    order = []
    if layer_order:
        for name in layer_order:
            name = str(name)
            if name not in order:
                order.append(name)
    for s in strokes:
        if s["layer"] not in order:
            order.append(s["layer"])
    id2name = {v: k for k, v in paint_native.PRESETS.items()}
    if tip_names:
        id2name.update(tip_names)
    out = []
    for s in strokes:
        p = s["preset"]
        name = id2name.get(p, str(p)) if isinstance(p, (int, np.integer)) else str(p)
        entry = {"layer": s["layer"], "preset": name, "size": float(s["size"]),
                 "opacity": float(s["opacity"]), "color": [round(float(x), 2) for x in s["color"]],
                 "hardness": float(s.get("hardness", 0.7)),
                 "region_id": int(s.get("region_id", 0)),
                 "points": [[round(float(v), 2) for v in pt] for pt in np.asarray(s["points"]).tolist()]}
        if s.get("tag"):
            entry["tag"] = str(s["tag"])
        out.append(entry)
    doc = {"canvas": [int(w), int(h)],
           "layers": [{"name": n, "blend": layer_blends.get(n, "normal")} for n in order],
           "strokes": out, "count": len(out)}
    if regions:
        doc["regions"] = regions
        doc["subject_layers"] = list(subject_layers or [])
    Path(path).write_text(json.dumps(doc, separators=(",", ":")), encoding="utf-8")
    return len(out)


def load_plan(path):
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    if "canvas" not in doc or "strokes" not in doc:
        raise ValueError("plan missing canvas/strokes")
    w, h = [int(v) for v in doc["canvas"]]
    return doc, w, h


def render_plan(path, out_png, threads=8):
    doc, w, h = load_plan(path)
    p = paint_native.Painter(w, h)
    p.clear_layer(0, (255, 255, 255), 0.0)      # CPU 的 layer 0 就是背景，不要再 add_layer
    layers = doc.get("layers") or []
    for spec in layers:
        p.add_layer(spec.get("blend", "normal"))
    rid = fg = None
    regions = doc.get("regions")
    if regions:
        z = __import__("numpy").load(regions)
        rid, fg = z["rid"], z["fg"]
        p.set_region(rid)
    subject = set(doc.get("subject_layers") or [])
    catalog = paint_native.load_catalog(p)   # 每张计划只扫一次笔尖目录
    by_layer = {}
    for s in doc["strokes"]:
        by_layer.setdefault(s["layer"], []).append(s)
    for i, spec in enumerate(layers):
        name = spec["name"]
        strokes = []
        p.set_region(fg if (fg is not None and name in subject) else rid) if regions else None
        for s in by_layer.get(name, []):
            pid = resolve_preset(s["preset"], p, allow_tips=True, catalog=catalog)
            strokes.append({"points": np.asarray(s["points"], np.float32), "size": float(s["size"]),
                            "opacity": float(s["opacity"]), "color": np.asarray(_color(s["color"]), np.float32),
                            "preset": pid, "hardness": float(s.get("hardness", 0.7)),
                            "region_id": int(s.get("region_id", 0))})
        if strokes:
            p.stroke_batch(i + 1, strokes, nthreads=threads)
    comp = p.composite()
    p.close()
    from PIL import Image
    Image.fromarray(comp, "RGBA").save(out_png)
    return {"canvas": [w, h], "layers": len(layers), "strokes": sum(len(v) for v in by_layer.values())}


def checkpoint(doc, name, out_dir, *, layer_blends=None, layer_order=None,
               render=True, threads=8):
    """落一个命名检查点：dump plan，可选渲染，返回路径/统计。

    name 是自由标签，不是阶段枚举；中间稿/完成稿怎么命名、分几段由调用方决定。
    doc 必须有 canvas 和 strokes。render=False 时只写 plan，不启动渲染器。
    """
    if "canvas" not in doc or "strokes" not in doc:
        raise ValueError("checkpoint doc needs canvas/strokes")
    label = str(name)
    if not label or label in (".", "..") or "/" in label or "\\" in label:
        raise ValueError("checkpoint name must be a plain file label")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    plan_path = out / (label + ".plan.json")
    png_path = out / (label + ".png")
    if layer_blends is None:
        layer_blends = {l.get("name"): l.get("blend", "normal")
                        for l in (doc.get("layers") or []) if l.get("name")}
    if layer_order is None:
        layer_order = [l.get("name") for l in (doc.get("layers") or []) if l.get("name")]
    count = dump_plan(str(plan_path), int(doc["canvas"][0]), int(doc["canvas"][1]),
                      doc["strokes"], layer_blends=layer_blends,
                      layer_order=layer_order or None)
    result = {"plan": str(plan_path), "png": None, "strokes": count,
              "layers": len({s.get("layer") for s in doc["strokes"] if s.get("layer")})}
    if render:
        result["render"] = render_plan(str(plan_path), str(png_path), threads=threads)
        result["png"] = str(png_path)
    return result


def main():
    if len(sys.argv) < 2:
        print("usage: plan_io.py <render|presets|check> ...")
        return
    cmd = sys.argv[1]
    if cmd == "render":
        print(json.dumps(render_plan(sys.argv[2], sys.argv[3],
                                     int(sys.argv[sys.argv.index("--threads") + 1]) if "--threads" in sys.argv else 8)))
    elif cmd == "presets":
        print(json.dumps({"formula": sorted(paint_native.PRESETS), "note": "bitmap tips need a painter; see catalog"}))
    elif cmd == "check":
        doc, w, h = load_plan(sys.argv[2])
        known = set(paint_native.PRESETS)
        bad = sorted({s["preset"] for s in doc["strokes"] if s["preset"] not in known})
        print(json.dumps({"canvas": [w, h], "strokes": len(doc["strokes"]),
                          "layers": [l["name"] for l in doc.get("layers", [])],
                          "non_formula_presets": bad[:8], "count_bad": len(bad)}))
    else:
        print("unknown command")


if __name__ == "__main__":
    main()
