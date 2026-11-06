"""Contract tests for structure_metric (no model, deterministic)."""
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE / "scripts"))
import plan_io
import shape_ops
import structure_metric as SM


def make_plan(path, shift=0):
    doc = {"canvas": [200, 200], "layers": [], "strokes": []}
    poly = [[30 + shift, 30], [170 + shift, 30], [170 + shift, 170], [30 + shift, 170]]
    shape_ops.fill_polygon(doc, "stag", poly, color="#333333", size=8,
                           spacing=6, jitter=0.0, edge=False, pressure=1.0)
    # one tagged landmark at the geometric center
    doc["strokes"].append({
        "layer": "stag", "preset": "round_hard", "size": 2, "opacity": 1,
        "color": [0, 0, 0], "hardness": 1, "tag": "landmark:nose",
        "points": [[100 + shift, 100, 1.0]],
    })
    plan_io.dump_plan(str(path), 200, 200, doc["strokes"], layer_blends={"stag": "normal"})
    return doc


def main():
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        a = td / "a.plan.json"
        b = td / "b.plan.json"
        make_plan(a)
        make_plan(b, shift=20)

        ma, w, h, layers = SM.load_mask(str(a), "stag")
        assert (w, h) == (200, 200)
        assert layers == ["stag"]
        mm = SM.mask_metrics(ma)
        assert 25 <= mm["bbox"][0] <= 35 and 25 <= mm["bbox"][1] <= 35, mm
        assert 160 <= mm["bbox"][2] <= 180 and 160 <= mm["bbox"][3] <= 180, mm
        assert mm["area"] > 10000

        same = SM.compare_masks(ma, ma)
        assert abs(same["shape_iou"] - 1.0) < 1e-6, same
        moved = SM.compare_masks(ma, SM.load_mask(str(b), "stag")[0])
        assert abs(moved["shape_iou"] - 1.0) < 1e-6, moved  # pure translation: shape is unchanged
        assert moved["placement"]["centroid_dx"] == 20.0, moved

        spec = {"landmarks_bbox": {"nose": [0.5, 0.5]},
                "tolerances": {"landmark": 0.05}}
        pred = SM.load_landmarks(None, json.loads(a.read_text(encoding="utf-8")))
        lm = SM.check_landmarks(pred, spec, mm["bbox"])
        assert lm["available"] and lm["pass"], lm
        bad = {"nose": [150, 150]}
        lm2 = SM.check_landmarks(bad, spec, mm["bbox"])
        assert lm2["available"] and not lm2["pass"], lm2

        spec2 = {"landmarks_bbox": {"nose": [0.5, 0.5], "poll": [0.6, 0.4]},
                 "segments": {"head": ["poll", "nose"]},
                 "tolerances": {"landmark": 0.06, "segment": 0.06}}
        lm3 = SM.check_landmarks({"nose": [50, 50], "poll": [60, 40]}, spec2, [0, 0, 100, 100])
        assert lm3["available"] and lm3["pass"] and lm3["segment_pass"], lm3

        shape_spec = {"bbox_aspect": 1.0, "fill_ratio": 0.5,
                      "tolerances": {"bbox_aspect": 0.05, "fill_ratio": 0.05}}
        sh = SM.check_shape_metrics({"bbox_aspect": 1.02, "fill_ratio": 0.52}, shape_spec)
        assert sh["available"] and sh["pass"], sh
        sh2 = SM.check_shape_metrics({"bbox_aspect": 1.30, "fill_ratio": 0.80}, shape_spec)
        assert sh2["available"] and not sh2["pass"], sh2

        print("PASS structure_metric bbox/metrics, aligned shape compare, tags, landmarks, segments, shape gates")


if __name__ == "__main__":
    main()
