#!/usr/bin/env python3
"""Minimal driver: write a plan, optionally fetch one advisory image.

The advisory image is never converted into plan geometry. Set
DSH_REFERENCE_IMAGE to opt in; otherwise no model is imported or loaded.

    python examples/reference_driver.py
    python examples/reference_driver.py --ref in.png --prompt "same subject, oil painting"
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
sys.path.insert(0, str(SKILL / "scripts"))

import plan_io
import shape_ops

W, H = 640, 480
OUT = HERE / "out"
OUT.mkdir(parents=True, exist_ok=True)


def build_plan():
    doc = {"canvas": [W, H], "layers": [], "strokes": []}
    shape_ops.fill_polygon(
        doc, "base", [(0, 0), (W, 0), (W, H), (0, H)],
        color=(22, 24, 30), size=72, spacing=10, preset="flat", edge=False)
    shape_ops.fill_polygon(
        doc, "shape", [(90, 110), (420, 90), (520, 350), (140, 390)],
        color=(190, 130, 70), size=18, spacing=6, preset="bristle", edge=True)
    return doc


def maybe_advisory(ref_path, prompt):
    if not ref_path:
        return None
    from reference_image import image_reference
    return image_reference(
        prompt, ref_path, str(OUT / "advisory.png"),
        model_path=os.environ.get("DSH_SDXS_PATH"),
        strength=0.45, steps=4, seed=0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", default=os.environ.get("DSH_REFERENCE_IMAGE"))
    ap.add_argument("--prompt", default="same subject, oil painting")
    a = ap.parse_args()
    doc = build_plan()
    advisory = maybe_advisory(a.ref, a.prompt)
    plan_path = OUT / "reference.plan.json"
    plan_io.dump_plan(
        str(plan_path), W, H, doc["strokes"],
        layer_blends={"base": "normal", "shape": "normal"},
        layer_order=["base", "shape"])
    print("plan:", plan_path)
    if advisory:
        print("advisory:", advisory["image"], "(not a plan)")
    else:
        print("advisory: skipped (no --ref / DSH_REFERENCE_IMAGE)")


if __name__ == "__main__":
    main()
