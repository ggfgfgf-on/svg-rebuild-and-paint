"""过程回放视频：按绘画批次生成关键帧，再复用铺帧（帧数不再影响渲染成本）。

用法: python render_video.py in.svg out.mp4 [frames=600] [fps=30]
环境变量: GROUPS=90 关键帧数量（越大越连续，成本线性增长）
"""

"""关键帧视频：每个阶段真实渲染一次（保留 blend mode 语义），帧只做复用。"""
import re, os, time, json, sys, subprocess
from concurrent.futures import ThreadPoolExecutor
import numpy as np
from PIL import Image
import imageio_ffmpeg

SRC = sys.argv[1]
TMP = os.path.join(os.path.dirname(os.path.abspath(sys.argv[2])), "_kf")
OUT = sys.argv[2]
W, H = 900, 1100   # 由 viewBox 覆盖（见下）
G = int(os.environ.get("GROUPS", "90"))
N = int(sys.argv[3]) if len(sys.argv) > 3 else 600
FPS = int(sys.argv[4]) if len(sys.argv) > 4 else 30
os.makedirs(TMP, exist_ok=True)
t0 = time.time()

svg = open(SRC, encoding="utf-8").read()
_m = re.search(r'viewBox="0 0 ([\d.]+) ([\d.]+)"', svg)
if _m:
    W, H = int(float(_m.group(1))), int(float(_m.group(2)))
ranges = []
for m in re.finditer(r'<g class="batch" data-seq="(\d+)" data-t="([\d.]+)"[^>]*>', svg):
    i, depth = m.end(), 1
    while i < len(svg) and depth:
        j = svg.find("<", i)
        if j < 0: break
        if svg.startswith("</g>", j):
            depth -= 1; i = j + 4
            if depth == 0:
                ranges.append((int(m.group(1)), float(m.group(2)), m.start(), i))
        elif svg.startswith("<g", j) and len(svg) > j + 2 and svg[j + 2] in " \t\r\n>":
            depth += 1; i = svg.find(">", j) + 1
        else:
            i = svg.find(">", j) + 1
ranges.sort(key=lambda r: r[0])
tmax = max(r[1] for r in ranges)
step = max(1, (len(ranges) + G - 1) // G)
cuts = [min((g + 1) * step, len(ranges)) - 1 for g in range((len(ranges) + step - 1) // step)]
print("batches %d -> keyframes %d" % (len(ranges), len(cuts)))

def prefix(k):
    """只保留前 k+1 个批次。"""
    parts, last = [], 0
    for idx, r in enumerate(ranges):
        if idx <= cuts[k]:
            continue
        parts.append(svg[last:r[2]]); last = r[3]
    parts.append(svg[last:])
    return "".join(parts)

render_py = r"D:\deepseek-harness-dsh-v0.2.1rc2\.dsh-home\skills\generic-pixel-svg-rebuild\scripts\render.py"

def build(k):
    sp = os.path.join(TMP, "k%03d.svg" % k)
    pp = os.path.join(TMP, "k%03d.png" % k)
    open(sp, "w", encoding="utf-8").write(prefix(k))
    subprocess.run([sys.executable, render_py, sp, pp, str(W), str(H)], capture_output=True)
    return pp

t1 = time.time()
with ThreadPoolExecutor(max_workers=8) as ex:
    files = list(ex.map(build, range(len(cuts))))
t2 = time.time()
print("rendered %d keyframes in %.1fs" % (len(files), t2 - t1))

times = [ranges[c][1] for c in cuts]
writer = imageio_ffmpeg.write_frames(OUT, (W, H), fps=FPS, codec="libx264", quality=8, pix_fmt_in="rgb24")
writer.send(None)
t3 = time.time()
kf = 0
imgs = []
cur = None
for i in range(N):
    t = tmax * ((i / (N - 1.0)) ** 1.30)
    want = 0
    while want + 1 < len(times) and times[want + 1] <= t + 1e-9:
        want += 1
    if want != kf or cur is None:
        kf = want
        cur = np.asarray(Image.open(files[kf]).convert("RGB")).tobytes()
    writer.send(cur)
writer.close()
t4 = time.time()
Image.open(files[-1]).convert("RGB").save(OUT + ".final.png")
print(json.dumps({"keyframes": len(files), "frames": N, "fps": FPS,
  "render_s": round(t2 - t1, 1), "encode_s": round(t4 - t3, 1),
  "total_s": round(t4 - t0, 1), "mb": round(os.path.getsize(OUT) / 1048576, 2)}))
