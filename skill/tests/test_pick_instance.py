"""模型自选实例接口的性质断言：验证器该拦的都拦，该放的放。"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import pick_instance as PI
import planner_region as PR

ok = True
def check(n, c, extra=""):
    global ok
    print(("PASS " if c else "FAIL ") + n + (" " + str(extra) if extra else ""))
    ok = ok and bool(c)

H = W = 200
bits = np.zeros((H, W), bool)
bits[60:140, 60:140] = True

v = PI.verify(bits)
check("标准方块通过", v["ok"], v)
check("面积占比正确", abs(v["area_frac"] - 80 * 80 / float(H * W)) < 1e-6)
check("连通块=1", v["components"] == 1)
check("空掩膜拦下", not PI.verify(np.zeros((H, W), bool))["ok"])
check("过小拦下", not PI.verify(np.zeros((H, W), bool) | np.pad(np.ones((3, 3), bool), ((10, H - 13), (10, W - 13))))["ok"])
big = np.ones((H, W), bool)
check("过大拦下", not PI.verify(big)["ok"])
check("贴边拦下", not PI.verify(big, max_area=1.0)["ok"])
check("允许贴边可放行", PI.verify(big, max_area=1.0, allow_border=True)["ok"])

two = np.zeros((H, W), bool)
two[20:60, 20:60] = True
two[120:160, 120:160] = True
check("多物体拦下", not PI.verify(two)["ok"])
check("允许 2 块可放行", PI.verify(two, max_components=2)["ok"])

full_box = np.zeros((H, W), bool)
full_box[40:160, 40:160] = True
check("掩膜≈矩形框被识别", not PI.verify(full_box, max_area=1.0, box=[40, 40, 160, 160],
                                            allow_border=True)["ok"])

# polygon 走同一条通道：模型画的掩膜也要被验证
import tempfile as _tf
from PIL import Image as _Im
arr = np.full((H, W, 3), 60, np.uint8)
arr[60:140, 60:140] = (220, 40, 40)
with _tf.TemporaryDirectory() as td:
    img = Path(td, "p.png"); _Im.fromarray(arr).save(img)
    rep = PI.pick(img, [{"name": "poly_ok", "polygon": [[60, 60], [140, 60], [140, 140], [60, 140]]}], Path(td, "o"))
    check("polygon 实例通过", rep["all_ok"], rep["instances"][0]["verify"])
    check("polygon 记录来源", rep["instances"][0]["source"] == "polygon")
    rep2 = PI.pick(img, [{"name": "poly_bad", "polygon": [[0, 0], [W, 0], [W, H], [0, H]]}], Path(td, "o2"))
    check("整图 polygon 被拦下", not rep2["all_ok"], rep2["instances"][0]["verify"]["reasons"])
    rep3 = PI.pick(img, [{"name": "poly_box", "polygon": [[50, 50], [150, 50], [150, 150], [50, 150]],
                          "box": [50, 50, 150, 150]}], Path(td, "o3"))
    check("polygon 占满框被拦下", not rep3["all_ok"], rep3["instances"][0]["verify"].get("box_fill"))

# survey 分块坐标自洽
res = PI.survey  # 只验证不落盘的辅助逻辑：tiles 覆盖整图
import io, tempfile
from PIL import Image
with tempfile.TemporaryDirectory() as td:
    img = Path(td, "t.png"); Image.fromarray(np.zeros((H, W, 3), np.uint8)).save(img)
    out = Path(td, "sheet.jpg")
    r = PI.survey(img, out, cols=2, rows=2)
    boxes = [t["box"] for t in r["tiles"]]
    check("survey 块数 = cols*rows", len(boxes) == 4)
    check("survey 覆盖到右下角", boxes[-1] == [100, 100, 200, 200], boxes[-1])
    check("survey 出图", out.exists())

# auto_ring 由笔触尺寸推出、不写死
doc = {"strokes": [{"layer": "band", "size": s} for s in (6, 8, 10, 40)]}
ring = PR.auto_ring(doc, ["band"])
check("auto_ring 有值且受限", 1 <= ring <= 24, ring)
check("auto_ring 随尺寸变", PR.auto_ring({"strokes": [{"layer": "band", "size": 4}]}, ["band"]) < ring)
check("无笔触时 ring=0", PR.auto_ring({"strokes": []}, ["band"]) == 0)

# candidates：算法提候选，模型挑（k-means 版；叶子这种同色物体分不出来，属于已知边界）
with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    im2 = np.full((300, 300, 3), 30, np.uint8)
    im2[120:180, 120:180] = (200, 60, 60)
    path2 = td / "c2.png"; Image.fromarray(im2).save(path2)
    res = PI.candidates(path2, roi=(60, 60, 240, 240), k=4, max_out=4)
    check("candidates 有输出", len(res["candidates"]) > 0, len(res["candidates"]))
    check("candidates 每个都能验证", all("verify" in c for c in res["candidates"]))
    sheet_info = PI.candidate_sheet(path2, res, td / "sheet.jpg")
    check("候选叠图落盘", sheet_info is not None and Path(sheet_info["sheet"]).exists())

def test_tile_points_multi():
    """多级 zoom：每个 window 自己的 roi/tile_px，正负编号各自换算到照片坐标。"""
    W, H = 200, 150
    img = np.zeros((H, W, 3), np.uint8)
    img[:, :] = (30, 60, 20)
    p = Path(tempfile.mkdtemp()) / "g.png"
    Image.fromarray(img).save(p)
    wins = [{"roi": [0, 0, 200, 150], "tile_px": 50, "pos": ["B2"], "neg": ["A1"]},
            {"roi": [100, 50, 140, 90], "tile_px": 10, "pos": ["C3"], "neg": ["A1"]}]
    pos, neg, used = PI.tile_points(str(p), wins)
    assert len(pos) == 2 and len(neg) == 2, (pos, neg)
    # 第一窗 B2 = 第一列第二行中心；第二窗 C3 = 细格中心，必须落在各自 roi 内
    assert 50 < pos[0][0] < 100 and 50 <= pos[0][1] <= 100, pos[0]
    assert 100 <= pos[1][0] <= 140 and 50 <= pos[1][1] <= 90, pos[1]
    assert [w["cols"] for w in used] == [4, 4], used
    print("PASS tile_points 多级 zoom 坐标换算 %s" % (pos,))


test_tile_points_multi()
def test_implausible_guard():
    """守卫本身要拦得住"整图掩膜"这种明显错误（不依赖判别器是否恰好坍缩）。"""
    full = np.ones((90, 120), bool)
    wins = [{"roi": [0, 0, 120, 90]}]
    msg = PI.mask_implausible(full, wins)
    assert msg and "整图" in msg, msg
    small = np.zeros((90, 120), bool)
    small[30:60, 40:80] = True
    assert PI.mask_implausible(small, wins) is None
    print("PASS 异常掩膜被判为不合理：%s" % msg)
    return True


def test_cell_geometry():
    """像素位置辅助函数：格子框/中心/逆映射/覆盖格子，必须自洽且不因 int 截断漂移。"""
    roi = [80.0, 500.0, 520.0, 790.0]
    cols, rows = 8, 6
    bad = 0
    for r in range(rows):
        for c in range(cols):
            lab = PI._tile_name(c, r)
            b = PI.cell_box(roi, cols, rows, lab)
            ctr = PI.cell_center(roi, cols, rows, lab)
            if not (b[0] <= ctr[0] <= b[2] and b[1] <= ctr[1] <= b[3]):
                bad += 1
            if PI.px_to_cell(roi, cols, rows, *ctr) != lab:
                bad += 1
    assert bad == 0, "格子几何不自洽 %d 处" % bad
    # 子窗口 roi 必须等于父格子的并集（浮点，不截断）
    g = PI.child_window(r"D:\DSHwork\flower\rose5-full.png",
                        {"id": "root", "roi": roi, "cols": cols, "rows": rows}, ["B3"], sub=5)
    b = PI.cell_box(roi, cols, rows, "B3")
    assert all(abs(a - c) < 1e-6 for a, c in zip(g["roi"], b)), (g["roi"], b)
    assert (g["cols"], g["rows"]) == (5, 5), (g["cols"], g["rows"])
    # 前缀引用：tip.A2 走子窗口
    pos, neg, used = PI.tile_points(r"D:\DSHwork\flower\rose5-full.png",
                                   [{"id": "root", "roi": roi, "cols": cols, "rows": rows, "pos": ["D3"], "neg": ["A1"]},
                                    {"id": "tip", "roi": g["roi"], "cols": 5, "rows": 5, "pos": ["A2"], "neg": ["A1"]}])
    assert len(pos) == 2 and len(neg) == 2, (pos, neg)
    print("PASS 像素位置辅助函数自洽（%dx%d 全格）与子窗口 roi/前缀引用" % (cols, rows))
    return True


ok = test_cell_geometry() and ok
ok = test_implausible_guard() and ok
print("RESULT", "OK" if ok else "FAILED")
sys.exit(0 if ok else 1)

def test_segment_band(tmpdir=None):
    """segment 的属性：同色块 + 淡色背景，自动分割应恢复 >0.9 IoU（不许手画点）。"""
    import tempfile, os
    from PIL import Image
    W, H = 240, 180
    img = np.zeros((H, W, 3), np.uint8)
    img[:, :] = (18, 30, 12)                      # 暗绿背景
    yy, xx = np.mgrid[0:H, 0:W]
    blob = ((xx - 120) / 70.0) ** 2 + ((yy - 90) / 50.0) ** 2 <= 1.0
    img[blob] = (40, 90, 20)                      # 亮一些的绿叶子
    d = tempfile.mkdtemp()
    p = os.path.join(d, "s.png"); Image.fromarray(img).save(p)
    m = PI.segment(p, roi=[20, 20, 220, 160], ring=6, passes=2)
    got = PI.rasterize(masks.from_bool(m) if hasattr(masks, "from_bool") else m, W, H)
    truth = cv2.resize(blob.astype(np.uint8), (W, H), interpolation=cv2.INTER_NEAREST).astype(bool)
    iou = float((got & truth).sum()) / max(1, int((got | truth).sum()))
    assert iou > 0.90, "segment IoU=%.3f" % iou
    print("PASS segment 自动分割 IoU %.3f" % iou)


