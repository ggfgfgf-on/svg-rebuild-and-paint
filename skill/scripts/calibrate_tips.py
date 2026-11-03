"""给 native/tips 里的真实笔尖实测校准：压力 -> (半径比 r, 峰值覆盖 a, 沉积量 dep)。

公式预设的校准仍由 calibrate_brush.py 负责；这里只补位图笔尖的实测条目，
写进同一张 brush.json，键名 "tip:<名字>"，并标 kind="bitmap"。
"""
import json, sys, time
from pathlib import Path
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import paint_native

N_POINTS = 33
PRES = np.linspace(0.0, 1.0, N_POINTS)
SIZE = 60.0
CANVAS = 200


def measure(painter, pid):
    r_l, a_l, dep = [], [], []
    for pv in PRES:
        painter.clear_layer(0, (0, 0, 0), 0.0)
        painter.stroke(0, np.array([[100, 100, float(pv)]], np.float32), SIZE, 1.0,
                       (255, 255, 255), preset=pid, hardness=0.6)
        al = painter.layer_rgba(0)[:, :, 3].astype(np.float64)
        peak = float(al.max()) / 255.0
        area = float((al > 25).sum())
        r_l.append(round(float(np.sqrt(area / np.pi) / (SIZE / 2)), 4))
        a_l.append(round(peak, 4))
        dep.append(round(float(al.sum() / 255.0) / (SIZE * SIZE), 4))
    return {"r": r_l, "a": a_l, "dep": dep, "kind": "bitmap"}


def main(limit=None):
    p = paint_native.Painter(CANVAS, CANVAS)
    p.add_layer()
    cat = paint_native.load_catalog(p, limit=limit)
    out = {}
    t0 = time.time()
    for name, pid in cat.items():
        out["tip:" + name] = measure(p, pid)
    p.close()
    dst = Path(__file__).resolve().parent / "native" / "brush.json"
    table = json.loads(dst.read_text(encoding="utf-8")) if dst.exists() else {}
    table.update(out)
    dst.write_text(json.dumps(table, indent=1), encoding="utf-8")
    sample = list(out)[:2]
    print(json.dumps({"tips_measured": len(out), "seconds": round(time.time() - t0, 1),
                      "table_entries": len(table),
                      "sample": {k: {"r[0,16,32]": [out[k]["r"][0], out[k]["r"][16], out[k]["r"][32]],
                                     "a[0,16,32]": [out[k]["a"][0], out[k]["a"][16], out[k]["a"][32]]}
                                 for k in sample}}, ensure_ascii=False))


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else None)
