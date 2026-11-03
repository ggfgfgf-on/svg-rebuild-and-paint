"""视频链路验收：小计划 + 一次编辑 -> 30 帧 @30fps，末帧必须等于整份重画。"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE / "scripts"))
import plan_io

W, H = 160, 120


def main():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        rng = np.random.default_rng(11)
        strokes = []
        for layer, color in (("under", [70, 80, 110]), ("top", [200, 60, 70])):
            for _ in range(18):
                x, y = float(rng.uniform(10, W - 10)), float(rng.uniform(10, H - 10))
                strokes.append({"layer": layer, "preset": "round_hard", "size": float(rng.uniform(5, 11)),
                                "opacity": 0.9, "color": color, "hardness": 0.7,
                                "points": [[x, y, 0.7], [x + 4, y + 3, 0.9]]})
        plan_io.dump_plan(tmp / "p.json", W, H, strokes, {"under": "normal", "top": "normal"})
        (tmp / "ops.json").write_text(json.dumps([
            {"op": "set_layer_hue", "layer": "top", "hue": 210, "guidance": 0.9}
        ]), encoding="utf-8")
        (tmp / "mask.json").write_text(json.dumps({"type": "rect", "x": 10, "y": 10, "w": 80, "h": 60}),
                                       encoding="utf-8")

        cmd = [sys.executable, str(HERE / "scripts" / "paint_video.py"), str(tmp / "p.json"),
               "--ops", str(tmp / "ops.json"), "--mask-file", str(tmp / "mask.json"),
               "--frames", "30", "--fps", "30", "--groups", "6",
               "--out", str(tmp / "out.mp4")]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            raise AssertionError("paint_video failed:\n" + (res.stderr or "")[-800:])
        stats = json.loads(res.stdout.strip().splitlines()[-1])
        assert stats["frames"] == 30 and stats["fps"] == 30, stats
        assert abs(stats["duration_s"] - 1.0) < 1e-6, stats
        assert stats["last_frame_vs_full_render_diff_px"] == 0, stats
        out = tmp / "out.mp4"
        assert out.exists() and out.stat().st_size > 1000, "mp4 missing/too small"
        print("PASS video: %d frames @%d fps = %.2fs, %.2f MB, last frame == full render (0 px)"
              % (stats["frames"], stats["fps"], stats["duration_s"], stats["mb"]))
    print("RESULT OK")


if __name__ == "__main__":
    main()
