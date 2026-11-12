#!/usr/bin/env python3
"""Structural gate for delivery manifest: spec hash, clause mapping, verifier split."""
import argparse, hashlib, json, sys
from pathlib import Path

def canon(obj):
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("manifest")
    a = ap.parse_args()
    doc = json.loads(Path(a.manifest).read_text(encoding="utf-8"))
    d = doc.get("delivery", doc)
    errs = []
    spec = d.get("spec")
    if not isinstance(spec, dict) or not spec:
        errs.append("missing spec")
        spec = {}
    for k in ("user_prompt", "style", "subject", "constraints"):
        if k not in spec:
            errs.append("spec missing " + k)
    h = d.get("spec_hash")
    if not h:
        errs.append("missing spec_hash")
    elif h != hashlib.sha256(canon(spec).encode("utf-8")).hexdigest():
        errs.append("spec_hash mismatch")
    clauses = set(k for k in spec.keys() if k != "constraints")
    if isinstance(spec.get("constraints"), list):
        clauses.add("constraints")
    target = d.get("target")
    if not isinstance(target, list) or not target:
        errs.append("target must be non-empty list")
        target = []
    target_clauses = set()
    for t in target:
        if not isinstance(t, dict) or not t.get("clause"):
            errs.append("target item missing clause")
            continue
        target_clauses.add(t["clause"])
        if t["clause"] not in clauses:
            errs.append("target clause not in spec: " + str(t["clause"]))
    checks = d.get("checks") or []
    for c in checks:
        sc = c.get("spec_clause") if isinstance(c, dict) else None
        if not sc:
            errs.append("check missing spec_clause: " + str(c.get("id") if isinstance(c, dict) else c))
        elif sc not in target_clauses:
            errs.append("check spec_clause not in target: " + str(sc))
    builder = d.get("builder") or doc.get("builder")
    verifier = d.get("verifier") or doc.get("verifier")
    if not builder:
        errs.append("missing builder")
    if not verifier:
        errs.append("missing verifier")
    elif builder and builder == verifier:
        errs.append("builder == verifier")
    print(json.dumps({"ok": not errs, "errors": errs}, ensure_ascii=False))
    return 0 if not errs else 1

if __name__ == "__main__":
    sys.exit(main())
