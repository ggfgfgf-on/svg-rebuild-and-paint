"""图层管理算子（add_layer 插入位 / remove / rename / move）与视频路径结构编辑的验证。

跑法：python tests/test_layer_ops.py
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
SCRIPTS = HERE / "scripts"
sys.path.insert(0, str(SCRIPTS))

import numpy as np
import ops
import plan_io
import render_util
from PIL import Image


def names(doc):
    return [l["name"] for l in doc["layers"]]


def main():
    doc, w, h = plan_io.load_plan(str(HERE / "examples" / "demo.plan.json"))
    base_names = names(doc)
    assert len(base_names) == 4

    # 1) add_layer index=0 垫底；默认追加到最上
    bottom = ops.apply(doc, [{"op": "add_layer", "name": "under", "blend": "multiply", "index": 0}])
    assert names(bottom)[0] == "under" and names(bottom)[1:] == base_names
    top = ops.apply(doc, [{"op": "add_layer", "name": "over"}])
    assert names(top)[-1] == "over"
    print("add_layer index: ok")

    # 2) move_layer：把最后一层移到最底；幂等位置不变
    mv = ops.apply(doc, [{"op": "move_layer", "name": base_names[-1], "index": 0}])
    assert names(mv)[0] == base_names[-1] and names(mv)[1:] == base_names[:-1]
    mv2 = ops.apply(mv, [{"op": "move_layer", "name": base_names[-1], "index": 0}])
    assert names(mv2) == names(mv)
    print("move_layer: ok")

    # 3) rename_layer：层名与笔触一起改；对新名继续操作可被校验接受
    rn = ops.apply(doc, [{"op": "rename_layer", "name": "band", "to": "band_x"}])
    assert "band_x" in names(rn) and "band" not in names(rn)
    assert all(s["layer"] != "band" for s in rn["strokes"])
    assert any(s["layer"] == "band_x" for s in rn["strokes"])
    import compile_ops
    compile_ops.validate_ops(doc, [{"op": "rename_layer", "name": "band", "to": "band_x"},
                                   {"op": "set_layer_brightness", "layer": "band_x", "factor": 1.05}])
    try:
        ops.apply(doc, [{"op": "rename_layer", "name": "band", "to": "highlight"}])
    except ValueError as ex:
        assert "already exists" in str(ex)
    else:
        raise AssertionError("rename 到已存在的层应报错")
    print("rename_layer: ok")

    # 4) remove_layer：层与笔触一起消失；guidance=0 不动
    rm = ops.apply(doc, [{"op": "remove_layer", "name": "highlight"}])
    assert "highlight" not in names(rm)
    assert all(s["layer"] != "highlight" for s in rm["strokes"])
    assert len(rm["strokes"]) < len(doc["strokes"])
    rm0 = ops.apply(doc, [{"op": "remove_layer", "name": "highlight", "guidance": 0.0}])
    assert ops.canonical(rm0) == ops.canonical(doc)
    print("remove_layer: ok")

    # 5) 结构编辑后渲染确定
    a1 = render_util.render_arr(rm, gpu=False)
    a2 = render_util.render_arr(rm, gpu=False)
    assert int(np.abs(a1.astype(np.int32) - a2.astype(np.int32)).max()) == 0
    print("post-structure deterministic render: ok")

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        ops_path = td / "ops.json"
        ops_path.write_text(json.dumps([
            {"op": "add_layer", "name": "glow2", "blend": "screen"},
            {"op": "draw_path", "layer": "glow2", "d": "M 70 70 L 300 260",
             "color": "#ffd9a0", "size": 16, "opacity": 0.55},
            {"op": "move_layer", "name": "glow2", "index": 0},
            {"op": "rename_layer", "name": "ambient", "to": "ambient2"},
            {"op": "remove_layer", "name": "band"},
        ]), encoding="utf-8")
        vid = td / "v.mp4"
        r = subprocess.run([sys.executable, str(SCRIPTS / "paint_video.py"), str(HERE / "examples" / "demo.plan.json"),
                            "--ops", str(ops_path), "--frames", "8", "--fps", "8", "--out", str(vid)],
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stderr[-800:]
        stats = json.loads(r.stdout.strip().splitlines()[-1])
        assert stats["last_frame_vs_full_render_diff_px"] == 0, stats

        # 独立复核：末帧必须等于「真正应用了结构算子」的计划渲染
        applied = ops.apply(doc, json.loads(ops_path.read_text(encoding="utf-8")), selection=None, weights=None)
        want = render_util.render_arr(applied, gpu=False)[..., :3].astype(np.int32)
        last = np.asarray(Image.open(td / "v.frames" / "00007.png").convert("RGB")).astype(np.int32)
        diff = int(np.any(want != last, axis=2).sum())
        assert diff == 0, "video last frame != edited plan render (%d px)" % diff
        assert stats["groups"] > 0
        print("video structural edit: ok (diff 0, groups=%d)" % stats["groups"])

        # 无 ops 的视频仍正常
        r2 = subprocess.run([sys.executable, str(SCRIPTS / "paint_video.py"), str(HERE / "examples" / "demo.plan.json"),
                             "--frames", "6", "--fps", "6", "--out", str(td / "v2.mp4")],
                            capture_output=True, text=True)
        assert r2.returncode == 0, r2.stderr[-800:]
        s2 = json.loads(r2.stdout.strip().splitlines()[-1])
        assert s2["last_frame_vs_full_render_diff_px"] == 0, s2
        print("video no ops: ok")

    print("RESULT OK")


if __name__ == "__main__":
    main()
