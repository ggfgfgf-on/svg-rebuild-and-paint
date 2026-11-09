"""plan JSON -> 画作：与 daub 的 plan 契约同构的自有实现。

plan = {
  "canvas": [w, h],
  "layers": [{"name": "base", "blend": "normal", "fill": "#101820"}],   # 可省略
  "strokes": [
    {"layer": 0, "preset": "round_soft", "size": 12, "opacity": 0.9,
     "color": "#e04a3a" 或 [r,g,b], "hardness": 0.6, "spacing": 1.0,
     "region_id": 0,
     "points": [[x, y, pressure], ...]}
  ]
}

用法: python paint_plan.py plan.json out.png [--kra a.kra] [--psd a.psd]
"""
from __future__ import annotations
import json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np
from PIL import Image
import paint_native
from export_layers import write_kra, write_psd


def _color(c, default=(255, 255, 255)):
    if c is None:
        return default
    if isinstance(c, str):
        c = c.lstrip("#")
        return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))
    return tuple(c)


def render_plan(plan, threads=8, brush=None, region=None):
    """按 plan 渲染，返回 (composite_rgba, layers[(name, rgba)])。"""
    w, h = plan["canvas"]
    layers_spec = plan.get("layers") or [{"name": "base", "blend": "normal"}]
    p = paint_native.Painter(int(w), int(h))
    for i, spec in enumerate(layers_spec):
        if i == 0:
            p.clear_layer(0, _color(spec.get("fill"), (0, 0, 0)), 1.0 if spec.get("fill") else 0.0)
        else:
            p.add_layer(spec.get("blend", "normal"))
        if i > 0 and spec.get("fill"):
            p.clear_layer(i, _color(spec["fill"]), 1.0)
    if region is not None:
        p.set_region(region)
    brush = brush if brush is not None else paint_native.Painter.load_brush()

    grouped = {}
    for s in plan.get("strokes", []):
        grouped.setdefault(int(s.get("layer", 0)), []).append({
            "points": s.get("points") or s.get("pts"),
            "size": s.get("size", 8.0),
            "opacity": s.get("opacity", 1.0),
            "color": _color(s.get("color")),
            "preset": s.get("preset", "round_soft"),
            "hardness": s.get("hardness", 0.6),
            "spacing": s.get("spacing", 1.0),
            "region_id": int(s.get("region_id", 0)),
        })
    # 分层按笔触所属层提交，保证层内顺序 = plan 顺序
    for layer in sorted(grouped):
        buf = []
        for it in grouped[layer]:
            if str(it.get("preset", "")) == "fill":
                if buf:
                    p.stroke_batch(layer, buf, nthreads=threads)
                    buf = []
                pts = [(float(q[0]), float(q[1])) for q in (it.get("points") or [])]
                if len(pts) >= 3:
                    p.fill_polygon(layer, pts, it.get("color"), it.get("opacity", 1.0))
            else:
                buf.append(it)
        if buf:
            p.stroke_batch(layer, buf, nthreads=threads)

    comp = p.composite()
    names = [spec.get("name", "layer%d" % i) for i, spec in enumerate(layers_spec)]
    out_layers = [(names[i], p.layer_rgba(i)) for i in range(p.layer_count())]
    p.close()
    return comp, out_layers


def main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 2
    plan = json.loads(Path(argv[1]).read_text(encoding="utf-8-sig"))
    comp, layers = render_plan(plan)
    Image.fromarray(comp, "RGBA").save(argv[2])
    for flag, writer in (("--kra", write_kra), ("--psd", write_psd)):
        if flag in argv:
            writer(argv[argv.index(flag) + 1], layers, merged=comp)
    print(json.dumps({"canvas": plan["canvas"], "layers": len(layers),
                      "strokes": len(plan.get("strokes", [])), "out": argv[2]}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
