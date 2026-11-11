#!/usr/bin/env python3
"""Generate docs/DRIVER.md from docs/driver.contract.json.

    python scripts/driver_doc.py            # regenerate
    python scripts/driver_doc.py --check    # fail if generated doc is stale
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONTRACT = HERE.parent / "docs" / "driver.contract.json"
OUT = HERE.parent / "docs" / "DRIVER.md"


def build():
    data = json.loads(CONTRACT.read_text(encoding="utf-8"))
    d = data["driver"]
    lines = [
        "# Driver Contract",
        "",
        "> Generated from docs/driver.contract.json by scripts/driver_doc.py; do not edit by hand.",
        "",
        "## Driver",
        "",
        "| Field | Contract |",
        "|---|---|",
        "| Input | " + str(d["input"]) + " |",
        "| Output | " + str(d["output"]) + " |",
        "| Required | " + ", ".join(d["required"]) + " |",
        "| Optional | " + ", ".join(d["optional"]) + " |",
        "| Implicit model load | " + ("yes" if d["implicit_model_load"] else "no") + " |",
        "| Validation | " + "; ".join(d["validation"]) + " |",
        "| Smoke | " + str(d.get("smoke", "")) + " |",
        "",
        "## Models",
        "",
        "| Model | Role | Inputs | Output | Reference image | Default called | Writes plan | Verified |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for name in sorted(data["models"]):
        m = data["models"][name]
        lines.append("| " + " | ".join([
            name,
            str(m["role"]),
            ", ".join(m["inputs"]),
            str(m["output"]),
            "yes" if m["reference_image"] else "no",
            "yes" if m["default_called"] else "no",
            "yes" if m["writes_plan"] else "no",
            str(m["verified"]),
        ]) + " |")
    lines += [
        "",
        "## Rule",
        "",
        "Models are optional advisory helpers. They do not write plans or geometry, and the driver does not load them implicitly.",
        "",
    ]
    return "\n".join(lines)


def main():
    text = build()
    if "--check" in sys.argv:
        cur = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if cur != text:
            print("docs/DRIVER.md is stale: run python scripts/driver_doc.py", file=sys.stderr)
            return 1
        print("driver contract doc is current")
        return 0
    OUT.write_text(text, encoding="utf-8")
    print("wrote " + str(OUT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
