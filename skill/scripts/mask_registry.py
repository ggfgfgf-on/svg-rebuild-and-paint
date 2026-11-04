"""掩膜注册表：给掩膜一个 id 和来源，别再靠 mask_rose_g14.json 这种猜文件名的做法。

用法:
    python scripts/mask_registry.py add --root lab/masks --mask m.json --source "render v2" \
        --note "seeds=(620,150) box=420,60,735,470 hue_tol=16" [--id rose]
    python scripts/mask_registry.py list --root lab/masks
    python scripts/mask_registry.py get --root lab/masks --id rose
"""
import argparse
import json
import time
import uuid
from pathlib import Path


def _index(root):
    return Path(root) / "index.jsonl"


def add(root, mask_path, source=None, note=None, mid=None):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    mid = mid or ("m" + uuid.uuid4().hex[:8])
    dst = root / ("%s.json" % mid)
    payload = json.loads(Path(mask_path).read_text(encoding="utf-8")) if mask_path else None
    dst.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    entry = {"id": mid, "path": str(dst), "source": source, "note": note,
             "time": time.strftime("%Y-%m-%d %H:%M:%S"),
             "type": (payload or {}).get("type"),
             "pixels": (payload or {}).get("w", 0) * (payload or {}).get("h", 0) if payload and payload.get("type") == "raster" else None}
    with _index(root).open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def entries(root):
    p = _index(root)
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def get(root, mid):
    for e in entries(root):
        if e["id"] == mid:
            return e
    raise SystemExit("找不到掩膜 %r" % mid)


def main():
    ap = argparse.ArgumentParser(description="掩膜注册表（带来源）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a1 = sub.add_parser("add"); a1.add_argument("--root", required=True); a1.add_argument("--mask", required=True)
    a1.add_argument("--source"); a1.add_argument("--note"); a1.add_argument("--id")
    a2 = sub.add_parser("list"); a2.add_argument("--root", required=True)
    a3 = sub.add_parser("get"); a3.add_argument("--root", required=True); a3.add_argument("--id", required=True)
    a = ap.parse_args()
    if a.cmd == "add":
        print(json.dumps(add(a.root, a.mask, a.source, a.note, a.id), ensure_ascii=False))
    elif a.cmd == "list":
        print("\n".join("%s  %s  %s" % (e["id"], e.get("source"), e.get("note")) for e in entries(a.root)) or "(empty)")
    else:
        print(json.dumps(get(a.root, a.id), ensure_ascii=False))


if __name__ == "__main__":
    main()
