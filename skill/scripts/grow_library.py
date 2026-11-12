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

    parent = c.get("parent")
    changes = c.get("changes")
    if parent:
        if not (PATTERNS / parent).exists():
            errs.append(f"parent pattern not found: patterns/{parent}")
        if not changes:
            errs.append("parent requires a non-empty changes list")
    elif changes:
        errs.append("changes requires parent")
    info["parent"] = parent

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
                if parent and changes:
                    c["copy_check"]["mode"] = "revision"
                    info["mode"] = "revision"
                else:
                    errs.append(f"driver looks copied from {who} (overlap {overlap:.2f}); declare parent+changes for a revision, or copy_allowed+reason for an intentional copy")
            elif parent and changes:
                c["copy_check"]["mode"] = "revision"
                info["mode"] = "revision"
            else:
                c["copy_check"]["mode"] = "ok"
                info["mode"] = "ok"
            dump(cj, c)

    if (PATTERNS / cid).exists() and (PATTERNS / cid) != cdir and cid != parent:
        errs.append(f"pattern already exists: patterns/{cid}")
    if verbose:
        if errs:
            print("FAIL")
            for e in errs:
                print(" -", e)
        else:
            print(f"OK  {cid}  overlap={info.get('overlap', 0):.2f}  mode={info.get('mode', 'ok')}")
    return errs, info


def promote(cdir: Path) -> int:
    errs, info = check(cdir)
    if errs:
        return 1
    cid = info["id"]
    c = load(cdir / "candidate.json")
    parent = c.get("parent")
    stamp = now().replace(":", "-").replace("T", "_")
    c["promoted"] = now()

    if parent:
        dst = PATTERNS / parent
        if not dst.exists():
            print(f"refuse: parent missing {dst}")
            return 1
        backup = EX / "_history" / parent / stamp
        backup.mkdir(parents=True, exist_ok=True)
        for f in dst.iterdir():
            if f.is_file():
                shutil.copy2(f, backup / f.name)
        assets = c.get("assets", {})
        for key, std in (("crop", "crop.png"), ("snippet", "snippet.py"), ("metrics", "metrics.json")):
            shutil.copy2(cdir / assets[key], dst / std)
        if (cdir / "pattern.md").exists():
            shutil.copy2(cdir / "pattern.md", dst / "pattern.md")
        with (dst / "pattern.md").open("a", encoding="utf-8") as f:
            f.write(f"\n## revision {now()}\n\n{c.get('review', '')}\n\n")
            for x in c.get("changes", []):
                f.write(f"- {x}\n")
        c["status"] = "curated_revision"
        c["parent"] = parent
        hist = EX / "_history" / cid / stamp
        hist.parent.mkdir(parents=True, exist_ok=True)
        dump(cdir / "candidate.json", c)
        shutil.move(str(cdir), str(hist))
        with CHANGELOG.open("a", encoding="utf-8") as f:
            f.write(f"- {now()} revise {parent} via {cid} from {c.get('source')}\n")
        print(f"revised -> {dst.relative_to(ROOT)} (backup {backup.relative_to(ROOT)})")
        return 0

    dst = PATTERNS / cid
    if dst.exists():
        print(f"refuse: {dst} already exists")
        return 1
    c["status"] = "curated"
    dump(cdir / "candidate.json", c)
    shutil.move(str(cdir), str(dst))
    idxp = PATTERNS / "index.json"
    idx = load(idxp) if idxp.exists() else {"version": 1, "patterns": []}
    idx.setdefault("patterns", [])
    if not any(x.get("id") == cid for x in idx["patterns"]):
        idx["patterns"].append({"id": cid, "path": cid, "source": c.get("source"), "tags": c.get("tags", [])})
    dump(idxp, idx)
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
         "review": "", "parent": args.parent, "changes": args.change, "copy_allowed": False, "copy_allowed_reason": ""}
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
    p.add_argument("--parent"); p.add_argument("--change", action="append", default=[])
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
