"""从零画到完成的 30fps 过程片（增量渲染，成本与帧数解耦）。

    python examples/incremental_video.py plan.json out.mp4 [frames] [fps]

为什么快：每笔只画一次（Painter.stroke_batch），每帧只做一次 composite。
逐帧用 render_arr 重画前 k 笔的话，总落笔量是 Σk，几百帧就要分分钟。
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import cv2
import numpy as np
import imageio_ffmpeg
import paint_native as PN

plan_path = Path(sys.argv[1])
out = Path(sys.argv[2] if len(sys.argv) > 2 else "process.mp4")
frames = int(sys.argv[3]) if len(sys.argv) > 3 else 600
fps = int(sys.argv[4]) if len(sys.argv) > 4 else 30
LEAD, TAIL = 12, 15

plan = json.loads(plan_path.read_text(encoding="utf-8"))
W, H = plan["canvas"]
strokes = plan["strokes"]
n = len(strokes)

p = PN.Painter(W, H)
p.clear_layer(0, (250, 247, 240), 1.0)                    # 白纸底
idx = {}
for i, spec in enumerate(plan["layers"]):
    p.add_layer(spec.get("blend", "normal"))
    idx[spec["name"]] = i + 1


def cut(i):
    if i < LEAD:
        return 0
    if i >= frames - TAIL:
        return n
    return max(1, int(round(n * (i - LEAD) / float(frames - LEAD - TAIL))))


writer = imageio_ffmpeg.write_frames(str(out), (W, H), fps=fps, codec="libx264",
                                     pix_fmt_in="rgb24", pix_fmt_out="yuv420p",
                                     macro_block_size=2, ffmpeg_log_level="error",
                                     output_params=["-crf", "20", "-preset", "veryfast"])
writer.send(None)
t0 = time.perf_counter()
drawn = 0
for i in range(frames):
    k = cut(i)
    j = drawn
    while j < k:                                          # 按层聚合成连续段，减少调用
        lay = strokes[j]["layer"]
        e = j
        while e < k and strokes[e]["layer"] == lay:
            e += 1
        p.stroke_batch(idx[lay], strokes[j:e], nthreads=8)
        j = e
    drawn = max(drawn, k)
    frame = np.ascontiguousarray(p.composite()[:, :, :3])
    cv2.rectangle(frame, (14, H - 40), (300, H - 14), (26, 22, 32), -1)
    cv2.putText(frame, "strokes %5d / %d" % (k, n), (24, H - 22),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (238, 230, 216), 1, cv2.LINE_AA)
    writer.send(frame.tobytes())
writer.close()
print(json.dumps({"frames": frames, "fps": fps, "duration_s": round(frames / fps, 2),
                  "size_mb": round(out.stat().st_size / 1048576.0, 2),
                  "strokes": n, "render_s": round(time.perf_counter() - t0, 2)},
                 ensure_ascii=False))
