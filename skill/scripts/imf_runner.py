#!/usr/bin/env python3
"""Optional PyTorch iMF/ImageNet class-conditional runner.

This is a thin adapter around the official PyTorch inference branch
(https://github.com/Lyy-iiis/imeanflow/tree/torch).  It does not bundle the
repo or weights.

Examples:
    python scripts/imf_runner.py --repo D:\\models\\imeanflow_torch \
        --ckpt D:\\models\\imf\\iMF-B-2.pth --class-id 277 --out out/fox.png
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def _sanitize_no_proxy():
    # httpx2 on this Windows box cannot parse the bracketed IPv6 entry.
    for key in ("NO_PROXY", "no_proxy"):
        raw = os.environ.get(key, "")
        if "[::1]" in raw:
            os.environ[key] = ",".join(x for x in raw.split(",") if x.strip() != "[::1]")


def generate(repo, ckpt, class_id, out_png, seed=0, device="cuda"):
    """Generate one 256px ImageNet-class image and save it.

    Returns metadata.  The caller must not trace/vectorise this image.
    """
    repo = Path(repo).resolve()
    ckpt = Path(ckpt).resolve()
    if not (repo / "imf.py").is_file():
        raise FileNotFoundError("imeanflow torch repo not found: %s" % repo)
    if not ckpt.is_file():
        raise FileNotFoundError("iMF checkpoint not found: %s" % ckpt)
    class_id = int(class_id)
    if class_id < 0 or class_id > 999:
        raise ValueError("ImageNet class_id must be 0..999")

    _sanitize_no_proxy()
    sys.path.insert(0, str(repo))
    import numpy as np
    import torch
    # Triton is not available on Windows; VAE decode must stay eager.
    torch.compile = lambda fn, *a, **kw: fn
    from PIL import Image
    from imf import iMeanFlow
    from utils.vae_util import VAEWrapper
    import utils.torch_util as tu

    model = iMeanFlow("imfDiT_B_2")
    state = torch.load(str(ckpt), map_location="cpu", weights_only=True)
    model.load_state_dict(state)
    model = tu.device_put(model).eval()
    vae = VAEWrapper(decode_batch_size=1)

    labels = torch.tensor([class_id], dtype=torch.int32).to(tu.dist.local_device())
    rng = tu.BatchGenerator(device=tu.dist.local_device(), seeds=[int(seed)])
    latents = model.generate(
        n_sample=1, rng=rng, num_steps=1,
        omega=8.0, t_min=0.4, t_max=0.65, labels=labels,
    )
    image = tu.device_get(vae.decode(latents))[0]
    image = (image + 1.0) / 2.0
    image = np.round(np.clip(image * 255, 0, 255)).astype(np.uint8)
    image = image.transpose(1, 2, 0)
    out = Path(out_png)
    out.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(image).save(out)
    return {
        "image": str(out),
        "backend": "imf-b2",
        "class_id": class_id,
        "seed": int(seed),
        "size": [256, 256],
        "advisory": True,
    }


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--ckpt", required=True)
    ap.add_argument("--class-id", type=int, required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args(argv)
    import json
    print(json.dumps(generate(a.repo, a.ckpt, a.class_id, a.out, seed=a.seed),
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
