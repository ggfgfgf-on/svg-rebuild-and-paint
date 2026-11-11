#!/usr/bin/env python3
"""Optional local advisory-visual adapters.

No weights are bundled and nothing is downloaded automatically.  The helpers
produce an *advisory* image (a concept thumbnail or a class prior).  They never
write a plan and must not be traced/vectorised as a substitute for authoring
geometry.

Backends
--------
sana-sprint
    Text-to-image, one/few steps.  Requires a local Diffusers model directory
    (env DSH_SANA_SPRINT_PATH or --model-path).

imagenet
    Class-conditional MeanFlow/iMF checkpoints are JAX/Flax research artifacts;
    this repo does not bundle a loader.  Pass a callable runner if you have a
    local one: runner(class_id, out_png, seed) -> path.
"""
from __future__ import annotations

import importlib.util
import inspect
import json
import os
import sys
from pathlib import Path


def _has(name):
    return importlib.util.find_spec(name) is not None


def status():
    sana_path = os.environ.get("DSH_SANA_SPRINT_PATH", "").strip() or None
    sdxs_path = os.environ.get("DSH_SDXS_PATH", "").strip() or None
    return {
        "sana-sprint": {
            "installed": all(_has(m) for m in ("torch", "diffusers", "transformers")),
            "torch": _has("torch"),
            "diffusers": _has("diffusers"),
            "transformers": _has("transformers"),
            "model_path": sana_path,
            "available": bool(sana_path) and all(_has(m) for m in ("torch", "diffusers", "transformers")),
        },
        "sdxs": {
            "installed": all(_has(m) for m in ("torch", "diffusers", "transformers")),
            "model_path": sdxs_path,
            "available": bool(sdxs_path) and all(_has(m) for m in ("torch", "diffusers", "transformers")),
        },
        "imagenet": {
            "installed": False,
            "available": False,
            "reason": "requires the optional imf_runner and local repo/checkpoint",
        },
    }


def _torch():
    if not _has("torch"):
        raise RuntimeError("torch is not installed; install a CUDA build for local inference")
    import torch
    return torch


def _pick_device(torch, device):
    if device == "auto":
        return "cuda" if torch.cuda.is_available() else "cpu"
    return device


def text_reference(prompt, out_png, *, model_path=None, size=(768, 768),
                   steps=1, seed=0, device="auto", offload=True,
                   guidance_scale=0.0, backend="sana-sprint"):
    """Generate one advisory text-to-image thumbnail with Sana-Sprint.

    Returns a metadata dict.  The caller remains responsible for looking at the
    image and writing geometric plan data; this helper does not create a plan.
    """
    if backend == "sdxs":
        return sdxs_reference(prompt, out_png, model_path=model_path, size=size,
                              steps=steps, seed=seed, device=device,
                              offload=offload, guidance_scale=guidance_scale)
    if backend != "sana-sprint":
        raise ValueError("backend must be 'sana-sprint' or 'sdxs'")

    prompt = str(prompt or "").strip()
    if not prompt:
        raise ValueError("prompt must be non-empty")
    w, h = int(size[0]), int(size[1])
    if w <= 0 or h <= 0 or w % 32 or h % 32:
        raise ValueError("size must be positive multiples of 32")
    steps = int(steps)
    if steps < 1 or steps > 8:
        raise ValueError("steps must be 1..8")

    torch = _torch()
    if not _has("diffusers"):
        raise RuntimeError("diffusers is not installed; pip install diffusers transformers accelerate")
    if not _has("transformers"):
        raise RuntimeError("transformers is not installed; pip install diffusers transformers accelerate")
    if not model_path:
        model_path = os.environ.get("DSH_SANA_SPRINT_PATH", "").strip()
    if not model_path:
        raise ValueError("set DSH_SANA_SPRINT_PATH or pass model_path (nothing is downloaded automatically)")
    model_dir = Path(model_path)
    if not model_dir.is_dir():
        raise FileNotFoundError("Sana-Sprint model directory not found: %s" % model_dir)

    import diffusers

    cls = getattr(diffusers, "SanaSprintPipeline", None) or getattr(diffusers, "SanaPipeline", None)
    if cls is None:
        raise RuntimeError("this diffusers build has no SanaSprintPipeline/SanaPipeline; upgrade diffusers")

    dev = _pick_device(torch, device)
    dtype = torch.float16 if dev == "cuda" else torch.float32
    pipe = cls.from_pretrained(str(model_dir), torch_dtype=dtype, local_files_only=True)
    if offload and dev == "cuda" and hasattr(pipe, "enable_model_cpu_offload"):
        try:
            pipe.enable_model_cpu_offload()
        except Exception:
            pipe.to(dev)
    else:
        pipe.to(dev)

    gen = torch.Generator(device="cpu").manual_seed(int(seed))
    kwargs = {
        "prompt": prompt,
        "width": w,
        "height": h,
        "num_inference_steps": steps,
        "generator": gen,
        "guidance_scale": float(guidance_scale),
    }
    accepted = set(inspect.signature(pipe.__call__).parameters)
    kwargs = {k: v for k, v in kwargs.items() if k in accepted}
    result = pipe(**kwargs)
    image = result.images[0]

    out = Path(out_png)
    out.parent.mkdir(parents=True, exist_ok=True)
    image.save(out)
    return {
        "image": str(out),
        "backend": "sana-sprint",
        "model": str(model_dir),
        "seed": int(seed),
        "size": [w, h],
        "steps": steps,
        "advisory": True,
    }


def sdxs_reference(prompt, out_png, *, model_path=None, size=(512, 512),
                    steps=1, seed=0, device="auto", offload=True,
                    guidance_scale=0.0):
    """Generate one advisory 512px image with SDXS-512-DreamShaper."""
    prompt = str(prompt or "").strip()
    if not prompt:
        raise ValueError("prompt must be non-empty")
    w, h = int(size[0]), int(size[1])
    if w <= 0 or h <= 0 or w % 32 or h % 32:
        raise ValueError("size must be positive multiples of 32")
    steps = int(steps)
    if steps < 1 or steps > 8:
        raise ValueError("steps must be 1..8")
    torch = _torch()
    if not _has("diffusers") or not _has("transformers"):
        raise RuntimeError("diffusers/transformers are required; pip install diffusers transformers accelerate")
    if not model_path:
        model_path = os.environ.get("DSH_SDXS_PATH", "").strip()
    if not model_path:
        raise ValueError("set DSH_SDXS_PATH or pass model_path (nothing is downloaded automatically)")
    model_dir = Path(model_path)
    if not model_dir.is_dir():
        raise FileNotFoundError("SDXS model directory not found: %s" % model_dir)

    from diffusers import StableDiffusionPipeline
    dev = _pick_device(torch, device)
    dtype = torch.float16 if dev == "cuda" else torch.float32
    pipe = StableDiffusionPipeline.from_pretrained(
        str(model_dir), torch_dtype=dtype, local_files_only=True)
    if offload and dev == "cuda" and hasattr(pipe, "enable_model_cpu_offload"):
        try:
            pipe.enable_model_cpu_offload()
        except Exception:
            pipe.to(dev)
    else:
        pipe.to(dev)
    gen = torch.Generator(device="cpu").manual_seed(int(seed))
    kwargs = {
        "prompt": prompt,
        "width": w,
        "height": h,
        "num_inference_steps": steps,
        "generator": gen,
        "guidance_scale": float(guidance_scale),
    }
    accepted = set(inspect.signature(pipe.__call__).parameters)
    kwargs = {k: v for k, v in kwargs.items() if k in accepted}
    image = pipe(**kwargs).images[0]
    out = Path(out_png)
    out.parent.mkdir(parents=True, exist_ok=True)
    image.save(out)
    return {
        "image": str(out),
        "backend": "sdxs",
        "model": str(model_dir),
        "seed": int(seed),
        "size": [w, h],
        "steps": steps,
        "advisory": True,
    }


def image_reference(prompt, init_image, out_png, *, model_path=None, size=None,
                    strength=0.55, steps=4, seed=0, device="auto",
                    offload=True, guidance_scale=0.0, backend="sdxs"):
    """Generate an advisory image-to-image result from a reference image.

    This is still an advisory image: it never writes a plan, never creates
    geometry, and must not be vectorised as a substitute for authoring the
    painting.  strength controls how far the result may move from the
    reference (0.05 = close, 0.95 = loose).

    Supported backends:
      * sdxs: Stable Diffusion img2img, verified with the local 512px model.
      * sana-sprint: not supported for image input.  The Sana-Sprint pipeline
        has no image argument; use the sdxs backend or a dedicated
        image-conditioned Sana checkpoint.
    """
    prompt = str(prompt or "").strip()
    if not prompt:
        raise ValueError("prompt must be non-empty")
    if backend != "sdxs":
        raise ValueError("backend 'sana-sprint' has no image-to-image pipeline; use backend='sdxs'")
    strength = float(strength)
    if not 0.05 <= strength <= 0.95:
        raise ValueError("strength must be in [0.05, 0.95]")
    steps = int(steps)
    if steps < 1 or steps > 8:
        raise ValueError("steps must be 1..8")
    if int(steps * strength) < 1:
        raise ValueError("steps * strength must be >= 1; raise steps or strength")
    ref = Path(init_image)
    if not ref.is_file():
        raise FileNotFoundError("reference image not found: %s" % ref)

    from PIL import Image, ImageOps
    torch = _torch()
    if not _has("diffusers") or not _has("transformers"):
        raise RuntimeError("diffusers/transformers are required; pip install diffusers transformers accelerate")
    if not model_path:
        model_path = os.environ.get("DSH_SDXS_PATH", "").strip()
    if not model_path:
        raise ValueError("set DSH_SDXS_PATH or pass model_path (nothing is downloaded automatically)")
    model_dir = Path(model_path)
    if not model_dir.is_dir():
        raise FileNotFoundError("SDXS model directory not found: %s" % model_dir)

    src = Image.open(ref).convert("RGB")
    if size is None:
        w, h = src.size
        w = max(64, int(round(w / 32.0)) * 32)
        h = max(64, int(round(h / 32.0)) * 32)
    else:
        w, h = int(size[0]), int(size[1])
        if w <= 0 or h <= 0 or w % 32 or h % 32:
            raise ValueError("size must be positive multiples of 32")
    src = ImageOps.fit(src, (w, h), method=Image.Resampling.LANCZOS)

    from diffusers import StableDiffusionImg2ImgPipeline
    dev = _pick_device(torch, device)
    dtype = torch.float16 if dev == "cuda" else torch.float32
    pipe = StableDiffusionImg2ImgPipeline.from_pretrained(
        str(model_dir), torch_dtype=dtype, local_files_only=True,
        safety_checker=None, requires_safety_checker=False)
    if offload and dev == "cuda" and hasattr(pipe, "enable_model_cpu_offload"):
        try:
            pipe.enable_model_cpu_offload()
        except Exception:
            pipe.to(dev)
    else:
        pipe.to(dev)
    gen = torch.Generator(device="cpu").manual_seed(int(seed))
    kwargs = {
        "prompt": prompt,
        "image": src,
        "strength": strength,
        "num_inference_steps": steps,
        "generator": gen,
        "guidance_scale": float(guidance_scale),
    }
    accepted = set(inspect.signature(pipe.__call__).parameters)
    kwargs = {k: v for k, v in kwargs.items() if k in accepted}
    image = pipe(**kwargs).images[0]
    out = Path(out_png)
    out.parent.mkdir(parents=True, exist_ok=True)
    image.save(out)
    return {
        "image": str(out),
        "backend": "sdxs",
        "mode": "img2img",
        "model": str(model_dir),
        "reference": str(ref),
        "strength": strength,
        "seed": int(seed),
        "size": [w, h],
        "steps": steps,
        "advisory": True,
    }


def class_reference(class_id, out_dir, *, n=1, seed=0, runner=None):
    """Optional ImageNet/iMF class prior.

    No weights/loader are bundled.  If you have a local iMF runner, pass
    runner(class_id, out_png, seed) -> output_path; this function only handles
    validation, naming, and metadata.
    """
    class_id = int(class_id)
    if class_id < 0 or class_id > 999:
        raise ValueError("ImageNet class_id must be 0..999")
    if runner is None:
        raise RuntimeError(
            "no bundled iMF/JAX loader; pass runner=callable(class_id, out_png, seed)"
        )
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    images = []
    for i in range(max(1, int(n))):
        target = out / ("class_%d_%02d.png" % (class_id, i))
        got = runner(class_id, str(target), int(seed) + i)
        if isinstance(got, dict):
            got = got.get("image", target)
        images.append(str(got or target))
    return {"class_id": class_id, "images": images, "advisory": True}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] == "status":
        print(json.dumps(status(), ensure_ascii=False, indent=2))
        return 0
    if argv[0] == "img2img":
        import argparse
        ap = argparse.ArgumentParser()
        ap.add_argument("image")
        ap.add_argument("prompt")
        ap.add_argument("out")
        ap.add_argument("--model-path", default=None)
        ap.add_argument("--backend", choices=["sdxs", "sana-sprint"], default="sdxs")
        ap.add_argument("--size", type=int, nargs=2, default=None)
        ap.add_argument("--strength", type=float, default=0.55)
        ap.add_argument("--steps", type=int, default=4)
        ap.add_argument("--seed", type=int, default=0)
        ap.add_argument("--device", default="auto")
        a = ap.parse_args(argv[1:])
        print(json.dumps(image_reference(a.prompt, a.image, a.out, model_path=a.model_path,
                                         size=a.size, strength=a.strength, steps=a.steps,
                                         seed=a.seed, device=a.device, backend=a.backend),
                         ensure_ascii=False, indent=2))
        return 0
    if argv[0] == "sana":
        import argparse
        ap = argparse.ArgumentParser()
        ap.add_argument("prompt")
        ap.add_argument("out")
        ap.add_argument("--model-path", default=None)
        ap.add_argument("--size", type=int, nargs=2, default=(768, 768))
        ap.add_argument("--steps", type=int, default=1)
        ap.add_argument("--seed", type=int, default=0)
        ap.add_argument("--device", default="auto")
        ap.add_argument("--backend", choices=["sana-sprint", "sdxs"], default="sana-sprint")
        a = ap.parse_args(argv[1:])
        print(json.dumps(text_reference(a.prompt, a.out, model_path=a.model_path,
                                        size=a.size, steps=a.steps, seed=a.seed,
                                        device=a.device, backend=a.backend),
                         ensure_ascii=False, indent=2))
        return 0
    print("usage: reference_image.py [status | img2img IMAGE PROMPT OUT | sana PROMPT OUT ...]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
