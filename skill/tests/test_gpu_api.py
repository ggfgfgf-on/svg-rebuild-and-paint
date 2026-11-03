import sys
sys.path.insert(0, r'D:\deepseek-harness-dsh-v0.2.1rc2\.dsh-home\skills\svg-rebuild-and-paint\scripts')
import numpy as np, paint_native, paint_gpu_native as gpn

W = H = 64
pts = np.array([[14, 32, 0.9], [50, 32, 0.6]], np.float32)

def render(gpu):
    p = (gpn.GpuPainter if gpu else paint_native.Painter)(W, H)
    if gpu:
        p.add_layer("normal")
    p.clear_layer(0, (12, 12, 12), 1.0)
    p.stroke(0, pts, 9.0, 0.9, (220, 40, 40), "round_soft", 0.6, 1.0)
    out = np.array(p.composite()).copy()
    lay = np.array(p.layer_rgba(0)).copy()
    p.close()
    return out, lay

a, la = render(False)
b, lb = render(True)
ok_api = (a == b).all() and (la == lb).all()
print("composite/layer_rgba diff:", int((a != b).any(axis=2).sum()), int((la != lb).any(axis=2).sum()))

# GPU 创建失败必须回落 CPU，且 tile_inc 不能按 GPU 的 offsets 切片
import ops_incremental_demo as D
orig = gpn.GpuPainter
gpn.GpuPainter = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no d3d11 device"))
try:
    q, _, _, _ = D.new_painter({"canvas": [16, 16]}, gpu=True)
    fell_back = getattr(q, "gpu_used", None) is False
finally:
    gpn.GpuPainter = orig
ok = ok_api and fell_back
print("fallback to CPU:", fell_back)
print("RESULT " + ("OK" if ok else "FAILED"))
sys.exit(0 if ok else 1)
