if __name__ != "__main__":
    import pytest
    pytest.skip("standalone acceptance script", allow_module_level=True)

"""GPU 后端的动态参数必须是每实例状态。

历史：pg_set_dynamics / pg_set_color_dynamics 曾写文件级 static 全局，
第二个 painter 一构造/设置就把第一个的设置覆盖掉——多实例（批处理、并行评测）会静默画错。
本测试固定该行为：先设 scatter 的 painter，在另一个 painter 重置动态之后，输出必须不变。
"""
import sys
sys.path.insert(0, r'D:\deepseek-harness-dsh-v0.2.1rc2\.dsh-home\skills\svg-rebuild-and-paint\scripts')
import numpy as np, paint_native, paint_gpu_native as gpn

W = H = 96
pts = np.array([[20, 48, 1.0], [70, 48, 1.0]], np.float32)
offs = np.array([0], np.int32)
pid = float(paint_native._pid("round_soft"))
params = np.array([[10.0, 1.0, 0.9, 0.2, 0.2, 0.6, 1.0, pid]], np.float32)
rids = np.array([0], np.int32)
region = np.zeros((H, W), np.int32)

def render(scatter, other_resets=False):
    p = gpn.GpuPainter(W, H)
    p.add_layer("normal")
    p.set_dynamics(scatter=scatter)
    if other_resets:
        q = gpn.GpuPainter(W, H)
        q.add_layer("normal")
        q.set_dynamics(scatter=0.0)
    p.stroke_batch_raw(0, pts, offs, params, rids, nthreads=1, region=region)
    out = np.array(p.composite())
    p.close()
    return out

a0 = render(0.0)
a1 = render(0.8)
a2 = render(0.8, other_resets=True)
print("scatter0 vs scatter0.8 diff_px =", int((a0 != a1).any(axis=2).sum()))
print("scatter0.8 alone vs after-other-reset diff_px =", int((a1 != a2).any(axis=2).sum()))
ok = (a1 == a2).all() and (a0 != a1).any()
print("RESULT " + ("OK" if ok else "FAIL"))
if __name__ == "__main__":
    sys.exit(0 if ok else 1)