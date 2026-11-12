#!/usr/bin/env python3
"""Grow the svg-rebuild-and-paint reference library.

Lifecycle:
  examples/inbox/<id>/     candidate, written while working
  examples/patterns/<id>/  curated, visible to every cold start
  examples/drivers/        full integration references

A candidate becomes curated only through promote, which re-runs the checks.
"""
from __future__ import annotations
import argparse, json, re, shutil, sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EX = ROOT / "examples"
INBOX = EX / "inbox"
PATTERNS = EX / "patterns"
DRIVERS = EX / "drivers"
CHANGELOG = EX / "CHANGELOG.md"


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def dump(p: Path, obj) -> None:
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def norm(text: str) -> list[str]:
    out = []
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        s = re.sub(r"[A-Za-z]:\\\\[^\"']+", "<path>", s)
        s = re.sub(r"\s+", " ", s)
        out.append(s)
    return out


def similarity(a: list[str], b: list[str]) -> float:
    def shingles(xs, n=4):
        return {tuple(xs[i:i+n]) for i in range(max(0, len(xs)-n+1))}
    sa, sb = shingles(a), shingles(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def copied_overlap(driver: Path) -> tuple[float, str | None]:
    if not driver.exists() or driver.suffix.lower() != ".py":
        return 0.0, None
    src = norm(driver.read_text(encoding="utf-8", errors="replace"))
    best, who = 0.0, None
    for p in EX.rglob("*.py"):
        if INBOX in p.parents:
            continue
        try:
            s = similarity(src, norm(p.read_text(encoding="utf-8", errors="replace")))
        except OSError:
            continue
        if s > best:
            best, who = s, str(p.relative_to(ROOT))
    return best, who


def check(cdir: Path, verbose: bool = True) -> tuple[list[str], dict]:
    errs: list[str] = []
    info: dict = {}
    cj = cdir / "candidate.json"
    if not cj.exists():
        return [f"missing {cj}"], info
    try:
        c = load(cj)
    except Exception as e:
        return [f"candidate.json parse error: {e}"], info

    cid = c.get("id", "")
    info["id"] = cid
    if not cid or cid != cdir.name:
        errs.append("id must match the directory name")
    if not c.get("subject"):
        errs.append("missing subject")
    if not c.get("source"):
        errs.append("missing source")
    if c.get("copy_allowed") and not c.get("copy_allowed_reason"):
        errs.append("copy_allowed requires copy_allowed_reason")

    assets = c.get("assets", {})
    for key in ("crop", "snippet", "metrics"):
        name = assets.get(key)
        if not name:
            errs.append(f"assets.{key} is missing")
            continue
        p = cdir / name
        if not p.exists():
            errs.append(f"assets.{key} not found: {name}")
    if not c.get("borrow"):
        errs.append("borrow must be a non-empty list")
    if not c.get("avoid"):
        errs.append("avoid must be a non-empty list")
    if not c.get("known_issues"):
        errs.append("known_issues must be a non-empty list")
    if not c.get("review"):
        errs.append("review is required before promotion")

    driver_name = c.get("source_driver")
    if driver_name:
        driver = (cdir / driver_name).resolve()
        if not driver.exists():
            errs.append(f"source_driver not found: {driver_name}")
        else:
            overlap, who = copied_overlap(driver)
            c.setdefault("copy_check", {})
            c["copy_check"]["overlap"] = round(overlap, 4)
            c["copy_check"]["closest"] = who
            info["overlap"] = round(overlap, 4)
            if overlap >= 0.98 and not c.get("copy_allowed"):
                errs.append(f"driver looks copied from {who} (overlap {overlap:.2f}); set copy_allowed + reason only if intentional")
            dump(cj, c)

    if (PATTERNS / cid).exists() and (PATTERNS / cid) != cdir:
        errs.append(f"pattern already exists: patterns/{cid}")
    if verbose:
        if errs:
            print("FAIL")
            for e in errs:
                print(" -", e)
        else:
            print(f"OK  {cid}  overlap={info.get('overlap', 0):.2f}")
    return errs, info


def promote(cdir: Path) -> int:
    errs, info = check(cdir)
    if errs:
        return 1
    cid = info["id"]
    dst = PATTERNS / cid
    if dst.exists():
        print(f"refuse: {dst} already exists")
        return 1
    c = load(cdir / "candidate.json")
    c["status"] = "curated"
    c["promoted"] = now()
    dump(cdir / "candidate.json", c)
    shutil.move(str(cdir), str(dst))

    idxp = PATTERNS / "index.json"
    idx = load(idxp) if idxp.exists() else {"version": 1, "patterns": []}
    idx.setdefault("patterns", [])
    if not any(x.get("id") == cid for x in idx["patterns"]):
        idx["patterns"].append({"id": cid, "path": cid, "source": c.get("source"), "tags": c.get("tags", [])})
    dump(idxp, idx)
    CHANGELOG.parent.mkdir(parents=True, exist_ok=True)
    with CHANGELOG.open("a", encoding="utf-8") as f:
        f.write(f"- {now()} promote {cid} from {c.get('source')}\n")
    print(f"promoted -> {dst.relative_to(ROOT)}")
    return 0


def new_candidate(args) -> int:
    cdir = INBOX / args.id
    if cdir.exists():
        print(f"refuse: {cdir} exists")
        return 1
    cdir.mkdir(parents=True)
    c = {"id": args.id, "subject": args.subject, "source": args.source, "status": "candidate",
         "created": now(), "tags": [],
         "assets": {"crop": "crop.png", "snippet": "snippet.py", "metrics": "metrics.json"},
         "source_driver": args.driver or None, "borrow": [], "avoid": [], "known_issues": [],
         "review": "", "copy_allowed": False, "copy_allowed_reason": ""}
    dump(cdir / "candidate.json", c)
    print(f"created {cdir}; add crop.png / snippet.py / metrics.json, fill borrow/avoid/known_issues/review")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    p = sub.add_parser("check"); p.add_argument("path")
    p = sub.add_parser("promote"); p.add_argument("path")
    p = sub.add_parser("new")
    p.add_argument("--id", required=True); p.add_argument("--subject", required=True)
    p.add_argument("--source", required=True); p.add_argument("--driver")
    a = ap.parse_args()

    if a.cmd == "list":
        print("INBOX")
        for d in (sorted(INBOX.glob("*/")) if INBOX.exists() else []):
            print(" ", d.name)
        print("PATTERNS")
        for d in sorted(PATTERNS.glob("*/")):
            print(" ", d.name)
        return 0
    if a.cmd == "check":
        errs, _ = check(Path(a.path)); return 1 if errs else 0
    if a.cmd == "promote":
        return promote(Path(a.path))
    if a.cmd == "new":
        return new_candidate(a)
    return 2


if __name__ == "__main__":
    sys.exit(main())
