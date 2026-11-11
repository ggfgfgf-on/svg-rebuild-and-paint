# Driver Contract

> Generated from docs/driver.contract.json by scripts/driver_doc.py; do not edit by hand.

## Driver

| Field | Contract |
|---|---|
| Input | plan: canvas / layers / strokes |
| Output | PNG via render_plan.py |
| Required | plan_io, render_plan |
| Optional | reference_image.img2img |
| Implicit model load | no |
| Validation | plan_io check; rerender maxdiff = 0 |

## Models

| Model | Role | Inputs | Output | Reference image | Default called | Writes plan | Verified |
|---|---|---|---|---|---|---|---|
| imf-b2 | optional ImageNet class prior | class_id | 256px class image | no | no | no | class 277 (red fox) |
| sdxs-512 | optional advisory text-to-image / image-to-image | prompt, reference_image | advisory PNG | yes | no | no | text 512x512 1 step; img2img 512x512 strength 0.45 4 steps |

## Rule

Models are optional advisory helpers. They do not write plans or geometry, and the driver does not load them implicitly.
