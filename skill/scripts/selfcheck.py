"""自检：三条主线各跑一次真实命令，打印 OK/FAIL。给没有上下文的调用方一个 30 秒答案。

    python scripts/selfcheck.py            # 跑完删临时目录
    python scripts/selfcheck.py --keep     # 保留产物，打印路径
    python scripts/selfcheck.py --frames 90 --json

检查项（全部在临时目录里跑，不写技能目录）：
  1) 位图 -> SVG：合成一张测试图，跑 auto.py，校验 mean/uncovered 与 out.html
  2) 从零作画：iterate.py --blank + draw 补丁，校验 svg 与 path 数
  3) 绘画/视频：paint_cli.py video examples/demo.plan.json，校验 mp4 与帧数
"""
import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
EXAMPLES = ROOT / "examples"


def run(cmd, cwd):
    t0 = time.perf_counter()
    p = subprocess.run([sys.executable] + [str(c) for c in cmd], cwd=str(cwd),
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return p, time.perf_counter() - t0


def last_json(text):
    for line in reversed(text.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except ValueError:
                continue
    return None


def make_ref(path):
    import numpy as np
    import cv2
    img = np.full((240, 240, 3), 245, np.uint8)
    cv2.circle(img, (128, 118), 66, (58, 62, 196), -1)
    cv2.rectangle(img, (26, 34), (74, 198), (196, 118, 46), -1)
    cv2.imwrite(str(path), img)


def _uncov(info):
    if not info:
        return "?"
    if "uncovered" in info:
        return info["uncovered"]
    for c in info.get("candidates") or []:
        if c.get("preset") == info.get("picked"):
            return c.get("uncovered")
    return "?"


def check_svg(work, res, args):
    ref = work / "ref.png"
    make_ref(ref)
    out = work / "out.html"
    p, dt = run([SCRIPTS / "auto.py", ref.name, out.name], work)
    info = last_json(p.stdout or "")
    ok = p.returncode == 0 and out.exists() and out.stat().st_size > 1024 and info and "mean" in info
    res.append({"name": "bitmap -> SVG (auto.py)", "ok": bool(ok), "sec": round(dt, 2),
                "detail": "mean=%s uncovered=%s %.0f KB" % (info.get("mean") if info else "?",
                                                            _uncov(info),
                                                            out.stat().st_size / 1024.0 if out.exists() else 0)})


def check_blank(work, res, args):
    patch = work / "draw.json"
    patch.write_text(json.dumps({"ops": [
        {"op": "draw", "d": "M0 250 L420 250 L420 300 L0 300 Z", "fill": "#2b6cb0"},
        {"op": "draw", "d": "M300 60 C338 60 360 84 360 108 C360 138 336 158 308 158 C278 158 252 138 252 108 C252 80 272 60 300 60 Z", "fill": "#ffd166"},
    ]}), encoding="utf-8")
    out = work / "out.svg"
    p, dt = run([SCRIPTS / "iterate.py", "-", out.name, "--base", "2 6 1.0 0 0 1 0",
                 "--blank", "420x300", "--patch", patch.name], work)
    info = last_json(p.stdout or "")
    ok = p.returncode == 0 and out.exists() and info and int(info.get("paths") or 0) >= 1
    res.append({"name": "blank canvas (iterate.py --blank)", "ok": bool(ok), "sec": round(dt, 2),
                "detail": "paths=%s %.1f KB" % (info.get("paths") if info else "?",
                                                out.stat().st_size / 1024.0 if out.exists() else 0)})


def check_paint(work, res, args):
    plan = EXAMPLES / "demo.plan.json"
    ops = EXAMPLES / "demo.ops.json"
    if not (plan.exists() and ops.exists()):
        res.append({"name": "paint -> video (paint_cli.py)", "ok": False, "sec": 0.0,
                    "detail": "缺少 examples/demo.plan.json 或 demo.ops.json"})
        return
    out = work / "out.mp4"
    p, dt = run([SCRIPTS / "paint_cli.py", "video", plan, "--ops", ops, "--out", out.name,
                 "--frames", args.frames, "--fps", args.fps], work)
    ok = p.returncode == 0 and out.exists() and out.stat().st_size > 2048
    detail = "%.0f KB" % (out.stat().st_size / 1024.0 if out.exists() else 0)
    if ok:
        try:
            import imageio_ffmpeg
            g = imageio_ffmpeg.read_frames(str(out), pix_fmt="rgb24")
            meta = g.__next__()
            n = 0
            try:
                while True:
                    g.__next__()
                    n += 1
            except StopIteration:
                pass
            ok = n == int(args.frames) and abs(float(meta.get("fps", 0)) - float(args.fps)) < 0.01
            detail = "%d 帧 @%sfps %.1fs %s" % (n, meta.get("fps"), float(meta.get("duration") or 0), detail)
        except Exception as e:
            detail += " (未校验帧数：%s)" % str(e)[:40]
    if not ok and p.stderr:
        detail += " | " + (p.stderr.strip().splitlines() or [""])[-1][:120]
    res.append({"name": "paint -> video (paint_cli.py)", "ok": bool(ok), "sec": round(dt, 2), "detail": detail})


CHECKS = [("svg", check_svg), ("blank", check_blank), ("paint", check_paint)]


def main():
    ap = argparse.ArgumentParser(description="三条主线自检")
    ap.add_argument("--frames", default="60", help="视频帧数（默认 60 = 2s@30fps）")
    ap.add_argument("--fps", default="30")
    ap.add_argument("--keep", action="store_true", help="保留临时产物目录")
    ap.add_argument("--json", action="store_true", help="只输出 JSON")
    args = ap.parse_args()

    work = Path(tempfile.mkdtemp(prefix="svp_selfcheck_"))
    res = []
    try:
        for _, fn in CHECKS:
            try:
                fn(work, res, args)
            except Exception as e:
                res.append({"name": fn.__name__, "ok": False, "sec": 0.0,
                            "detail": "%s: %s" % (type(e).__name__, str(e)[:120])})
        ok = all(r["ok"] for r in res)
        if args.json:
            print(json.dumps({"ok": ok, "root": str(ROOT), "checks": res}, ensure_ascii=False))
        else:
            for r in res:
                print("[%s] %-38s %5.1fs  %s" % ("OK  " if r["ok"] else "FAIL", r["name"], r["sec"], r["detail"]))
            print("RESULT " + ("OK" if ok else "FAIL") + "  (skill: %s)" % ROOT)
            if args.keep:
                print("产物保留在 %s" % work)
        return 0 if ok else 1
    finally:
        if not args.keep:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
