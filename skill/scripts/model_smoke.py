#!/usr/bin/env python3
"""Run optional model smoke checks; no weights are bundled.

    python scripts/model_smoke.py --model sdxs
    python scripts/model_smoke.py --model imf --repo ... --ckpt ...
    python scripts/model_smoke.py --model all
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def _check_image(path):
    p = Path(path)
    if not p.is_file() or p.stat().st_size <= 0:
        raise RuntimeError("smoke output missing: %s" % p)
    return str(p)


def sdxs(out_dir, model_path=None, ref=None, device="auto"):
    import reference_image as R
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    text = R.text_reference("a red fox at dawn", str(out / "sdxs_text.png"),
                            model_path=model_path, size=(512, 512), steps=1,
                            seed=0, device=device, backend="sdxs")
    _check_image(text["image"])
    ref_path = ref or text["image"]
    image = R.image_reference("same subject, oil painting", ref_path,
                              str(out / "sdxs_img2img.png"),
                              model_path=model_path, strength=0.45, steps=4,
                              seed=1, device=device, backend="sdxs")
    _check_image(image["image"])
    return {"text": text, "img2img": image}


def imf(out_dir, repo=None, ckpt=None, class_id=277, device="cuda"):
    import imf_runner
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    repo = repo or os.environ.get("DSH_IMF_REPO")
    ckpt = ckpt or os.environ.get("DSH_IMF_CKPT")
    if not repo or not ckpt:
        raise ValueError("pass --repo/--ckpt or set DSH_IMF_REPO/DSH_IMF_CKPT")
    meta = imf_runner.generate(repo, ckpt, class_id, str(out / "imf_class.png"),
                               seed=0, device=device)
    _check_image(meta["image"])
    return meta


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", choices=["sdxs", "imf", "all"], default="sdxs")
    ap.add_argument("--out", default=None)
    ap.add_argument("--model-path", default=None)
    ap.add_argument("--ref", default=None)
    ap.add_argument("--repo", default=None)
    ap.add_argument("--ckpt", default=None)
    ap.add_argument("--class-id", type=int, default=277)
    ap.add_argument("--device", default="auto")
    a = ap.parse_args()
    out_dir = Path(a.out) if a.out else Path(tempfile.mkdtemp(prefix="model-smoke-"))
    result = {"out": str(out_dir)}
    if a.model in ("sdxs", "all"):
        result["sdxs"] = sdxs(out_dir, model_path=a.model_path, ref=a.ref, device=a.device)
    if a.model in ("imf", "all"):
        result["imf"] = imf(out_dir, repo=a.repo, ckpt=a.ckpt,
                            class_id=a.class_id, device=a.device)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print("SMOKE FAILED: %s" % exc, file=sys.stderr)
        raise SystemExit(1)
