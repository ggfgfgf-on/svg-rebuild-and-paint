"""compile_ops.py — 把「改画指令」编译成算子 JSON（对应扩散里的文本编码器）。

扩散模型把一句话编码成条件向量，注入去噪过程；这里把一句话编码成**算子序列**，
注入计划空间。区别是：编译产物是可读的 JSON，能落盘、能校验、能 diff、能复现。

模型只做编译这一件事；运行时（ops.apply）不再调用模型，所以渲染仍然是逐位确定的：

    指令 --[模型, 编译期]--> ops.json --[ops.apply, 纯本地]--> plan' --[paint_core]--> 像素

CLI：
    python compile_ops.py summarize <plan.json>
    python compile_ops.py prompt    <plan.json> "<instruction>" [--mask JSON]
    python compile_ops.py validate  <plan.json> <ops.json>
    python compile_ops.py apply     <plan.json> <ops.json> <out.json> [--mask JSON] [--feather N]
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np

import masks
import ops
import plan_io
import paint_native

SCHEMA_NOTE = """输出必须是一个 JSON 数组（不要 markdown 包裹、不要解释），每个元素是一个算子：

  {"op": "set_layer_hue",       "layer": "<层名>", "hue": 0..360,  "guidance": 0..1}
  {"op": "set_layer_saturation","layer": "<层名>", "sat": 0..1,    "guidance": 0..1}
  {"op": "set_layer_brightness","layer": "<层名>", "factor": >0,   "guidance": 0..1}
  {"op": "scale_stroke_size",   "layer": "<层名>", "k": >0,        "guidance": 0..1}
  {"op": "swap_brush",          "layer": "<层名>", "preset": "<笔刷名>", "guidance": 0..1}
  {"op": "densify",             "layer": "<层名>", "k": >0,        "guidance": 0..1}
  {"op": "erase",                                                "guidance": 0..1}
  {"op": "redraw",              "fraction": 0..1, "jitter": 0..0.5}
  {"op": "recolor_palette",     "palette": [[r,g,b], ...],        "guidance": 0..1}
  {"op": "draw_path",           "layer": "<层名>", "d": "<SVG path>", "color": "#rrggbb", "size": >0, "opacity": 0..1, "preset": "<笔刷名>", "guidance": 0..1}
  {"op": "tone_map",            "gain": >0, "lift": 0..1, "gamma": >0, "layer": "<层名，可省>", "guidance": 0..1}

规则：
  * guidance=1 精确命中目标；0.5 = 折中；0 = 不动。拿不准就用 0.6~0.8。
  * 只改「花瓣」这类局部时，改画区域由外部 mask 提供，算子本身不要写坐标。
  * draw_path 是「从零加笔」：d 用 canvas 坐标写 SVG path（M/L/H/V/C/Q/T/Z），不需要 mask，
    只需要层名；想加在别处就把坐标写到那里。它和别的算子一样受 guidance 控制。
  * tone_map 是整体明暗的通用修法（专门对付 shadow/texture 两层 multiply 压暗）：
    gain 乘亮度（1.5~2.0 通常明显提亮），lift 加白（0.03~0.08 抬黑场），gamma<1 提中间调，
    layer 不给 = 所有层；不动颜色关系，只改明暗。
  * 只允许上面 11 个算子，不要发明新字段；层名必须是下面列出的层。
"""


def _hex(rgb):
    return "#%02x%02x%02x" % tuple(int(round(float(v))) for v in rgb[:3])


def load_image_data_url(path, max_side=720):
    """把本地图片压到 max_side 内，返回 data URL（多模态编译的视觉 grounding 输入）。"""
    import base64
    import io
    from PIL import Image
    im = Image.open(str(path)).convert("RGB")
    im.thumbnail((int(max_side), int(max_side)), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def build_messages(prompt, image_data_url=None):
    """纯文本 -> content 字符串；带图 -> OpenAI 兼容的 text+image_url 块。"""
    if not image_data_url:
        return [{"role": "user", "content": prompt}]
    return [{"role": "user", "content": [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": image_data_url}},
    ]}]


def render_thumb(plan_path, out_path=None, max_side=720, gpu=True):
    """把计划渲染成一张小预览（GPU 可用时 ~2s），供编译模型看图。"""
    import numpy as np
    from PIL import Image
    import plan_io
    import ops_incremental_demo as D
    import tile_inc
    doc, w, h = plan_io.load_plan(str(plan_path))
    p, cat, w, h = D.new_painter(doc, gpu=gpu)
    plan, spec = D.doc_to_plan(doc, cat)
    region = np.zeros((h, w), np.int32)
    tile_inc.full(p, plan, spec, lambda nm: region, gpu=bool(getattr(p, "gpu_used", gpu)), nthreads=8)
    arr = p.composite()
    p.close()
    im = Image.fromarray(arr).convert("RGB")
    im.thumbnail((int(max_side), int(max_side)), Image.LANCZOS)
    if out_path:
        im.save(str(out_path))
    return im


def _chat_body(model, prompt, image_data_url=None):
    return {"model": model, "temperature": 0, "messages": build_messages(prompt, image_data_url)}


def summarize(doc, palette_top=8):
    strokes = doc["strokes"]
    by_layer = {}
    for s in strokes:
        by_layer.setdefault(s["layer"], []).append(s)
    layers = []
    for spec in doc.get("layers") or []:
        name = spec.get("name")
        items = by_layer.get(name, [])
        if not items:
            layers.append({"name": name, "blend": spec.get("blend", "normal"), "strokes": 0})
            continue
        cols = np.asarray([np.asarray(s["color"], np.float64)[:3] for s in items])
        sizes = np.asarray([float(s["size"]) for s in items])
        layers.append({
            "name": name, "blend": spec.get("blend", "normal"), "strokes": len(items),
            "mean_color": _hex(cols.mean(axis=0)), "size_median": round(float(np.median(sizes)), 1),
            "size_range": [round(float(sizes.min()), 1), round(float(sizes.max()), 1)],
        })
    q = np.round(np.asarray([np.asarray(s["color"], np.float64)[:3] for s in strokes]) / 32.0) * 32.0
    uniq, counts = np.unique(q.astype(int), axis=0, return_counts=True)
    order = np.argsort(-counts)[:palette_top]
    palette = [{"rgb": [int(v) for v in uniq[k]], "hex": _hex(uniq[k]), "strokes": int(counts[k])} for k in order]
    presets = {}
    for s in strokes:
        presets[s["preset"]] = presets.get(s["preset"], 0) + 1
    return {
        "canvas": doc.get("canvas"),
        "layers": layers,
        "palette_top": palette,
        "presets": dict(sorted(presets.items(), key=lambda kv: -kv[1])[:10]),
        "total_strokes": len(strokes),
    }


def build_prompt(summary, instruction, mask=None, has_image=False):
    parts = [SCHEMA_NOTE, "## 当前计划摘要",
             json.dumps(summary, ensure_ascii=False, separators=(",", ":"))]
    if mask:
        parts.append("## 改画区域（mask，坐标系同 canvas）\n" +
                     json.dumps(mask, ensure_ascii=False, separators=(",", ":")))
    if has_image:
        parts.append("## 当前画面的渲染预览\n（随消息附上的图片就是下面这份计划现在的样子；"
                     "判断颜色/明暗/高光请以图片为准）")
    parts.append("## 用户指令\n" + instruction)
    parts.append("## 现在只输出 JSON 数组，不要任何其它文字。")
    return "\n\n".join(parts)


def _layer_names(doc):
    return [l.get("name") for l in (doc.get("layers") or [])]


def _known_presets():
    return set(paint_native.PRESETS)


def validate_ops(doc, specs, allow_layers=None):
    """严格校验：算子名 / 必填键 / 取值范围 / 层名存在 / preset 存在。"""
    if not isinstance(specs, list):
        raise ValueError("ops must be a JSON array, got %s" % type(specs).__name__)
    names = set(allow_layers or _layer_names(doc))
    presets = _known_presets()
    for i, spec in enumerate(specs):
        try:
            ops.validate(spec)
            if "layer" in spec and spec["layer"] not in names:
                raise ValueError("unknown layer %r; known: %s" % (spec["layer"], ", ".join(sorted(names))))
            if spec["op"] == "swap_brush" and spec["preset"] not in presets:
                raise ValueError("unknown preset %r; known: %s" % (spec["preset"], ", ".join(sorted(presets))))
            if spec["op"] == "draw_path":
                if not isinstance(spec.get("d"), str) or not spec["d"].strip():
                    raise ValueError("draw_path needs a non-empty SVG path string 'd'")
                if float(spec.get("size", 6.0)) <= 0:
                    raise ValueError("draw_path size must be > 0")
                if "preset" in spec and spec["preset"] not in presets:
                    raise ValueError("unknown preset %r; known: %s" % (spec["preset"], ", ".join(sorted(presets))))
                if "color" in spec:
                    rgb = ops._to_rgb(spec["color"])
                    if rgb.shape[0] != 3 or rgb.min() < 0 or rgb.max() > 255:
                        raise ValueError("draw_path color must be #rrggbb or [r,g,b] in 0..255")
            if spec["op"] == "tone_map":
                if not (isinstance(spec.get("gain", 1.0), (int, float)) and float(spec.get("gain", 1.0)) > 0):
                    raise ValueError("tone_map gain must be > 0")
                if not (isinstance(spec.get("gamma", 1.0), (int, float)) and float(spec.get("gamma", 1.0)) > 0):
                    raise ValueError("tone_map gamma must be > 0")
                lf = spec.get("lift", 0.0)
                if not (isinstance(lf, (int, float)) and 0.0 <= float(lf) <= 1.0):
                    raise ValueError("tone_map lift must be within [0,1]")
            if spec["op"] == "redraw" and "order" in spec:
                if spec["order"] not in ("random", "coverage", "layer", "zorder"):
                    raise ValueError("redraw order must be random/coverage/layer/zorder, got %r" % (spec["order"],))
            if spec["op"] == "recolor_palette":
                pal = np.asarray(spec["palette"], np.float64)
                if pal.ndim != 2 or pal.shape[1] != 3:
                    raise ValueError("palette must be [[r,g,b], ...]")
                if pal.min() < 0 or pal.max() > 255:
                    raise ValueError("palette values must be 0..255")
        except ValueError as e:
            raise ValueError("ops[%d] (%s): %s" % (i, spec.get("op"), e))
    return specs


PROVIDERS = {
    "deepseek": ("https://api.deepseek.com/v1", "deepseek-chat", "DEEPSEEK_API_KEY"),
    "ark": ("https://ark.cn-beijing.volces.com/api/v3", "doubao-seed-1-6-250615", "ARK_API_KEY"),
    "openai": ("https://api.openai.com/v1", "gpt-4o-mini", "OPENAI_API_KEY"),
}


def _strip_fences(text):
    t = text.strip()
    if t.startswith("```"):
        t = t.split(chr(10), 1)[1] if chr(10) in t else t
        t = t.rsplit("```", 1)[0]
    return t.strip()

def compile_with_model(prompt, provider="deepseek", model=None, base_url=None, image=None,
                       timeout=120, retries=2, verbose=False):
    """调 OpenAI 兼容接口拿算子 JSON；失败抛 RuntimeError（调用方回退到 prompt 模式）。"""
    import os
    import time
    import urllib.request
    import urllib.error

    base, default_model, env = PROVIDERS.get(provider, (None, None, None))
    base = base_url or base
    model = model or default_model
    key = os.environ.get(env or "", "")
    if not (base and model and key):
        raise RuntimeError("provider %r not configured (need %s)" % (provider, env))
    url = base.rstrip("/") + "/chat/completions"
    if image and not str(image).startswith("data:"):
        image = load_image_data_url(image)
    body = json.dumps(_chat_body(model, prompt, image)).encode("utf-8")
    last = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, data=body, headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + key,
            })
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            text = data["choices"][0]["message"]["content"]
            ops = json.loads(_strip_fences(text))
            if not isinstance(ops, list):
                raise ValueError("model did not return a JSON array")
            return ops
        except Exception as ex:          # 网络/解析失败都重试
            last = ex
            if image is not None and ("image" in str(ex).lower() or "400" in str(ex)):
                # 该模型/端点不支持图片输入：退回纯文本，别让编译整体失败
                image = None
                body = json.dumps(_chat_body(model, prompt, None)).encode("utf-8")
                sys.stderr.write("compile: image input rejected, falling back to text-only\n")
            if verbose:
                sys.stderr.write("compile attempt %d failed: %s\n" % (attempt + 1, ex))
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError("model call failed after %d attempts: %s" % (retries + 1, last))


def _parse_mask(text):
    return masks.from_json(json.loads(text))


def main():
    if len(sys.argv) < 2:
        print(__doc__.strip().split("\n\n")[-1])
        return
    cmd = sys.argv[1]
    if cmd == "summarize":
        doc, w, h = plan_io.load_plan(sys.argv[2])
        print(json.dumps(summarize(doc), ensure_ascii=False, indent=1))
    elif cmd == "prompt":
        doc, w, h = plan_io.load_plan(sys.argv[2])
        mask = _parse_mask(sys.argv[sys.argv.index("--mask") + 1]) if "--mask" in sys.argv else None
        print(build_prompt(summarize(doc), sys.argv[3], mask))
    elif cmd == "validate":
        doc, w, h = plan_io.load_plan(sys.argv[2])
        ops_arg = sys.argv[sys.argv.index("--ops") + 1] if "--ops" in sys.argv else sys.argv[3]
        specs = json.loads(Path(ops_arg).read_text(encoding="utf-8"))
        validate_ops(doc, specs)
        print(json.dumps({"ok": True, "ops": len(specs)}))
    elif cmd == "compile":
        doc, w, h = plan_io.load_plan(sys.argv[2])
        instruction = sys.argv[3]
        mask = None
        if "--mask-file" in sys.argv:
            mask = _parse_mask(Path(sys.argv[sys.argv.index("--mask-file") + 1]).read_text(encoding="utf-8"))
        image = None
        if "--image" in sys.argv:
            image = sys.argv[sys.argv.index("--image") + 1]
        elif "--thumb" in sys.argv:
            import tempfile
            tp = Path(tempfile.gettempdir()) / "compile_thumb.png"
            render_thumb(sys.argv[2], tp, gpu="--no-gpu" not in sys.argv)
            image = str(tp)
        prompt = build_prompt(summarize(doc), instruction, mask, has_image=bool(image))
        provider = sys.argv[sys.argv.index("--provider") + 1] if "--provider" in sys.argv else "deepseek"
        model = sys.argv[sys.argv.index("--model") + 1] if "--model" in sys.argv else None
        out = sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else None
        try:
            specs = compile_with_model(prompt, provider=provider, model=model, image=image,
                                       verbose="--verbose" in sys.argv)
            validate_ops(doc, specs)
        except Exception as ex:
            sys.stderr.write("compile failed: %s\n\n--- prompt (use it manually) ---\n%s\n" % (ex, prompt))
            raise SystemExit(2)
        if out:
            Path(out).write_text(json.dumps(specs, ensure_ascii=False, indent=1), encoding="utf-8")
        print(json.dumps({"ok": True, "ops": specs, "provider": provider, "model": model,
                          "image_grounded": bool(image), "out": out}, ensure_ascii=False))
    elif cmd == "apply":
        doc, w, h = plan_io.load_plan(sys.argv[2])
        specs = validate_ops(doc, json.loads(Path(sys.argv[3]).read_text(encoding="utf-8")))
        sel = wt = None
        if "--mask" in sys.argv:
            mask = _parse_mask(sys.argv[sys.argv.index("--mask") + 1])
            feather = float(sys.argv[sys.argv.index("--feather") + 1]) if "--feather" in sys.argv else 0.0
            sel, wt = masks.weights(doc, mask, feather)
        out = ops.apply(doc, specs, selection=sel, weights=wt)
        Path(sys.argv[4]).write_text(json.dumps(out, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
        box = masks.bbox(doc, sel) if sel else None
        print(json.dumps({"strokes_in": len(doc["strokes"]), "strokes_out": len(out["strokes"]),
                          "selected": None if sel is None else len(sel),
                          "dirty_rect": None if box is None else [round(v, 1) for v in box],
                          "fingerprint": ops.fingerprint(out)}, ensure_ascii=False))
    else:
        print("unknown command")


if __name__ == "__main__":
    main()
