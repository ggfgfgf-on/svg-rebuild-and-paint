"""渲染与亮度相关的共用辅助函数（calibrate_tone / rebuild_shadow_layer / 其它脚本共用）。

这里只放"跑一次渲染、拿数组、算亮度"这类无状态的薄封装；算子的语义一律留在 ops.py，
不要在辅助函数里塞任何业务常数。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
from PIL import Image

import ops_incremental_demo as D
import paint_native
import tile_inc

LUMA = np.array([0.2126, 0.7152, 0.0722], np.float64)


def luma_of(arr):
    """sRGB 相对亮度（0-255）。arr 可以是 RGBA，取前 3 通道。"""
    return float(np.asarray(arr, dtype=np.float64)[..., :3].dot(LUMA).mean())


def target_luma_from_image(path):
    """参考图的平均亮度——标定的输入就是它，不是拍出来的常数。"""
    return luma_of(np.asarray(Image.open(path).convert("RGB"), dtype=np.float64))


def draw_full(p, cat, doc, w, h, gpu=False, nthreads=8):
    """把整份计划画进已创建好的 painter（之后还要做增量重建的调用方复用这条路径）。"""
    plan, spec = D.doc_to_plan(doc, cat)
    region = np.zeros((h, w), np.int32)
    tile_inc.full(p, plan, spec, lambda nm: region, gpu=gpu, nthreads=nthreads)
    return plan, spec, region


def _gpu_unsupported(doc):
    for st in doc.get("strokes") or []:
        if isinstance(st.get("paint"), dict) or isinstance(st.get("mask"), dict) or str(st.get("preset")) == "fill":
            return True
    for ly in doc.get("layers") or []:
        if isinstance(ly.get("mask"), dict):
            return True
    return False


def render_arr(doc, gpu=False, nthreads=8):
    """整份计划渲染一次，返回 RGBA 数组（composite 结果）。"""
    if gpu and _gpu_unsupported(doc):
        gpu = False
    p, cat, w, h = D.new_painter(doc, gpu=gpu)
    try:
        plan, spec = D.doc_to_plan(doc, cat)
        region = np.zeros((h, w), np.int32)
        tile_inc.full(p, plan, spec, lambda nm: region, gpu=gpu, nthreads=nthreads)
        return np.ascontiguousarray(p.composite()).copy()
    finally:
        p.close()


def render_luma(doc, gpu=False, nthreads=8):
    return luma_of(render_arr(doc, gpu=gpu, nthreads=nthreads))


def render_layer_rgba(doc, layer, gpu=False, nthreads=8):
    """只渲染某一层（其它层清空），alpha = 该层覆盖。"""
    d = {"canvas": doc["canvas"],
         "layers": [{"name": "bg", "blend": "normal"}, {"name": "top", "blend": "normal"}],
         "strokes": [dict(s, layer="top") for s in doc["strokes"] if s["layer"] == layer]}
    return render_arr(d, gpu=gpu, nthreads=nthreads)


def send_strokes(p, idx, strokes, nthreads=4, region=None):
    """把一组计划笔触一次性喂给 painter 的某一层（planner 与测试共用同一条发送路径）。"""
    parts, offs, params, rids = [], [], [], []
    cur = 0
    for st in strokes:
        a = np.ascontiguousarray(st["points"], dtype=np.float32).reshape(-1, 3)
        parts.append(a.ravel())
        offs.append(cur)
        cur += a.size
        c = st["color"]
        params.extend([st["size"], st["opacity"], c[0] / 255.0, c[1] / 255.0, c[2] / 255.0,
                       st.get("hardness", 0.7), 1.0, float(paint_native._pid(st["preset"]))])
        rids.append(int(st.get("region_id", 0)))
    pts = np.concatenate(parts) if parts else np.zeros(0, np.float32)
    kwargs = {"nthreads": nthreads}
    if region is not None:
        kwargs["region"] = region
    p.stroke_batch_raw(idx, pts, np.asarray(offs, np.int32), np.asarray(params, np.float32),
                       np.asarray(rids, np.int32), **kwargs)


def strokes_to_rgba(strokes, w, h, nthreads=4):
    """一组笔触单独渲染成 RGBA（alpha=覆盖）。"""
    p = paint_native.Painter(w, h)
    try:
        p.add_layer("normal")
        p.clear_layer(0, (255, 255, 255), 0.0)
        send_strokes(p, 1, strokes, nthreads=nthreads)
        return np.ascontiguousarray(p.layer_rgba(1)).copy()
    finally:
        p.close()
