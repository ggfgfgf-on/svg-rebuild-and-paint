#!/usr/bin/env python3
"""Download optional reference models from their official sources.

Weights are NOT redistributed by this repository.  This script only calls the
official Hugging Face repos; review each license before downloading/using.

    python scripts/fetch_reference_models.py --list
    python scripts/fetch_reference_models.py --download imf-b2 --dest models/imf
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

MODELS = {
    "sdxs": {
        "repo": "IDKiro/sdxs-512-dreamshaper",
        "kind": "snapshot",
        "size_gb": 2.1,
        "license": "OpenRAIL++",
        "redistribute": False,
        "note": "512px one-step; small local text/image prior.",
    },
    "imf-b2": {
        "repo": "Lyy0725/iMF",
        "filename": "iMF-B-2.pth",
        "kind": "file",
        "size_gb": 0.36,
        "license": "no license metadata in the model card; do not redistribute",
        "redistribute": False,
        "note": "ImageNet class-conditional iMF (PyTorch); not text-to-image.",
    },
}


def manifest():
    return {k: {kk: vv for kk, vv in v.items()} for k, v in MODELS.items()}


def _fix_no_proxy():
    # httpx2 mis-parses the bracketed IPv6 loopback entry on this Windows box.
    for key in ("NO_PROXY", "no_proxy"):
        raw = os.environ.get(key, "")
        if "[::1]" in raw:
            parts = [x for x in raw.split(",") if x.strip() != "[::1]"]
            os.environ[key] = ",".join(parts)


def download(key, dest):
    _fix_no_proxy()
    if key not in MODELS:
        raise KeyError("unknown model: %s" % key)
    try:
        from huggingface_hub import snapshot_download, hf_hub_download
    except Exception as exc:
        raise RuntimeError("huggingface_hub is required: pip install huggingface_hub") from exc
    spec = MODELS[key]
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    if spec["kind"] == "snapshot":
        path = snapshot_download(repo_id=spec["repo"], local_dir=str(dest))
    else:
        path = hf_hub_download(repo_id=spec["repo"], filename=spec["filename"],
                               local_dir=str(dest))
    return {"key": key, "repo": spec["repo"], "path": str(path),
            "license": spec["license"], "redistribute": spec["redistribute"]}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--download", choices=sorted(MODELS))
    ap.add_argument("--dest", default="models")
    a = ap.parse_args(argv)
    if a.download:
        print(json.dumps(download(a.download, a.dest), ensure_ascii=False, indent=2))
        return 0
    print(json.dumps(manifest(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
