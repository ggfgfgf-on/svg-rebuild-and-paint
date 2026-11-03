"""MCP stdio server：把笔触渲染器暴露给 agent（12 个工具）。

协议：JSON-RPC 2.0，逐行 JSON（MCP stdio 传输）。
"""
from __future__ import annotations
import json, sys, base64, io
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import numpy as np
from PIL import Image
import paint_native
from export_layers import write_kra, write_psd

SERVER = {"name": "paint-core", "version": "1.0.0"}
PROTO = "2024-11-05"
STATE = {"p": None, "layers": []}

TOOLS = [
    ("paint_create", "创建画布（含 1 个底层）", {"width": "int", "height": "int", "background": "color? 默认不填充"}),
    ("paint_add_layer", "新增图层", {"blend": "normal|multiply|screen"}),
    ("paint_fill_layer", "整层填充颜色", {"layer": "int", "color": "color", "alpha": "0-1"}),
    ("paint_set_region", "设置区域裁剪图（int32 .npy）", {"path": "str"}),
    ("paint_stroke", "画一笔", {"layer": "int", "points": "[[x,y,pressure]...]", "size": "float",
                              "opacity": "0-1", "color": "color", "preset": "笔头名",
                              "hardness": "0-1", "spacing": "float", "region_id": "int?", "calibrated": "bool"}),
    ("paint_composite", "合成并保存 PNG", {"path": "str"}),
    ("paint_layer_png", "导出单层 PNG", {"layer": "int", "path": "str"}),
    ("paint_export_kra", "导出分层 Krita .kra", {"path": "str"}),
    ("paint_export_psd", "导出分层 Photoshop .psd", {"path": "str"}),
    ("paint_info", "画布与图层状态", {}),
    ("paint_presets", "笔头预设与校准曲线", {}),
    ("paint_close", "释放画布", {}),
]


def _color(c, default=(255, 255, 255)):
    if c is None:
        return default
    if isinstance(c, str):
        c = c.lstrip("#")
        return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))
    return tuple(c)


def _painter():
    p = STATE["p"]
    if p is None:
        raise RuntimeError("no canvas: call paint_create first")
    return p


def call(name, a):
    if name == "paint_create":
        p = paint_native.Painter(int(a["width"]), int(a["height"]))
        STATE["p"] = p
        STATE["layers"] = ["base"]
        if a.get("background"):
            p.clear_layer(0, _color(a["background"]), 1.0)
        return {"width": int(a["width"]), "height": int(a["height"]), "layers": 1}
    if name == "paint_add_layer":
        p = _painter()
        idx = p.add_layer(a.get("blend", "normal"))
        STATE["layers"].append(a.get("blend", "normal"))
        return {"layer": idx, "blend": a.get("blend", "normal")}
    if name == "paint_fill_layer":
        p = _painter()
        p.clear_layer(int(a["layer"]), _color(a.get("color")), float(a.get("alpha", 1.0)))
        return {"ok": True}
    if name == "paint_set_region":
        p = _painter()
        p.set_region(np.load(a["path"]))
        return {"ok": True, "shape": list(p._region.shape)}
    if name == "paint_stroke":
        p = _painter()
        pts = np.asarray(a["points"], np.float32)
        if pts.ndim == 1:
            pts = pts.reshape(-1, 3)
        lut = None
        if a.get("calibrated"):
            lut = paint_native.Painter.load_brush().get(a.get("preset", "round_soft"))
        p.stroke(int(a.get("layer", 0)), pts, float(a["size"]), float(a.get("opacity", 1.0)),
                 _color(a.get("color")), preset=a.get("preset", "round_soft"),
                 hardness=float(a.get("hardness", 0.6)), spacing=float(a.get("spacing", 1.0)),
                 region_id=int(a.get("region_id", 0)), lut=lut)
        return {"ok": True, "points": int(len(pts))}
    if name == "paint_composite":
        p = _painter()
        comp = p.composite()
        Image.fromarray(comp, "RGBA").save(a["path"])
        return {"path": a["path"], "size": [p.w, p.h]}
    if name == "paint_layer_png":
        p = _painter()
        Image.fromarray(p.layer_rgba(int(a["layer"])), "RGBA").save(a["path"])
        return {"path": a["path"]}
    if name in ("paint_export_kra", "paint_export_psd"):
        p = _painter()
        layers = [("layer%d" % i, p.layer_rgba(i)) for i in range(p.layer_count())]
        comp = p.composite()
        fn = write_kra if name.endswith("kra") else write_psd
        fn(a["path"], layers, merged=comp)
        return {"path": a["path"], "layers": len(layers)}
    if name == "paint_info":
        p = _painter()
        return {"width": p.w, "height": p.h, "layers": p.layer_count(), "region": p._region is not None}
    if name == "paint_presets":
        return {"presets": list(paint_native.PRESETS), "blends": list(paint_native.BLENDS),
                "calibration": paint_native.Painter.load_brush()}
    if name == "paint_close":
        if STATE["p"]:
            STATE["p"].close()
        STATE["p"] = None
        return {"ok": True}
    raise RuntimeError("unknown tool: " + name)


def schema(props):
    out = {}
    for k, desc in props.items():
        optional = desc.endswith("?") or "默认" in desc
        t = "string"
        if desc.startswith("int"):
            t = "integer"
        elif desc.startswith("float") or desc.startswith("0-1"):
            t = "number"
        elif desc.startswith("bool"):
            t = "boolean"
        elif desc.startswith("color") or desc.startswith("[[") or desc.startswith("normal|"):
            t = "string" if desc.startswith("normal|") else "object"
        elif desc.startswith("str") or desc.startswith("笔头"):
            t = "string"
        elif desc.startswith("[["):
            t = "array"
        out[k] = {"type": t, "description": desc}
    return {"type": "object", "properties": out, "required": [k for k, v in props.items()
            if not (v.endswith("?") or "默认" in v)]}


def handle(msg):
    method = msg.get("method")
    mid = msg.get("id")
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": PROTO, "capabilities": {"tools": {}}, "serverInfo": SERVER}}
    if method == "notifications/initialized":
        return None
    if method == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": [
            {"name": n, "description": d, "inputSchema": schema(props)} for n, d, props in TOOLS]}}
    if method == "tools/call":
        params = msg.get("params", {})
        try:
            out = call(params.get("name"), params.get("arguments") or {})
            return {"jsonrpc": "2.0", "id": mid, "result": {
                "content": [{"type": "text", "text": json.dumps(out, ensure_ascii=False)}],
                "isError": False}}
        except Exception as e:  # noqa: BLE001
            return {"jsonrpc": "2.0", "id": mid, "result": {
                "content": [{"type": "text", "text": "%s: %s" % (type(e).__name__, e)}],
                "isError": True}}
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "method not found"}}


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        resp = handle(msg)
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
