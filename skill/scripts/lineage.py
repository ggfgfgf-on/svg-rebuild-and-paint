"""编辑谱系：每次编辑一条记录（指令/ops/掩膜来源/度量/产物），可回溯到任意版本。

不是查表，也不改行为——它只是一个 append-only 的 JSONL 账本，让"第 5 步不满意回第 3 步"
以及"两条路线合并"有据可依（ops 是确定性的，重放可复现）。

用法:
    python scripts/lineage.py record --root lab/lineage --plan p.json --instruction "..." \
        --ops ops.json --metrics m.json --tag it1 [--parent <id>] [--snapshot p_after.json]
    python scripts/lineage.py tree --root lab/lineage
    python scripts/lineage.py checkout --root lab/lineage --id <id> --out restored.plan.json
"""
import argparse
import json
import shutil
import time
import uuid
from pathlib import Path


def _path(root):
    return Path(root) / "lineage.jsonl"


def record(root, plan, instruction=None, ops=None, mask=None, metrics=None, tag=None,
           parent=None, snapshot=None, extra=None):
    """追加一条编辑记录，返回条目（含 id）。"""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    entries = read(root)
    eid = "e" + uuid.uuid4().hex[:8]
    entry = {
        "id": eid,
        "parent": parent or (entries[-1]["id"] if entries else None),
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "tag": tag,
        "instruction": instruction,
        "plan": str(plan),
        "ops": ops,
        "mask": mask,
        "metrics": metrics,
        "snapshot": None,
    }
    if snapshot:
        dst = root / ("%s.plan.json" % eid)
        shutil.copyfile(snapshot, dst)
        entry["snapshot"] = str(dst)
    if extra:
        entry.update(extra)
    with _path(root).open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def read(root):
    p = _path(root)
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def tree(root):
    """按父子关系缩进打印（返回文本行）。"""
    entries = read(root)
    by_parent = {}
    for e in entries:
        by_parent.setdefault(e.get("parent"), []).append(e)
    lines = []

    def walk(pid, depth):
        for e in by_parent.get(pid, []):
            label = e.get("tag") or e["id"]
            ins = (e.get("instruction") or "")[:40]
            lines.append("%s%s %s  %s" % ("  " * depth, e["id"], label, ins))
            walk(e["id"], depth + 1)

    roots = by_parent.get(None, [])
    for e in roots:
        lines.append("%s %s  %s" % (e["id"], e.get("tag") or "", (e.get("instruction") or "")[:40]))
        walk(e["id"], 1)
    return lines


def checkout(root, eid, out):
    """恢复某个版本的快照计划；没有快照则报错（记录里只存路径时无法还原）。"""
    for e in read(root):
        if e["id"] == eid:
            if not e.get("snapshot"):
                raise SystemExit("版本 %s 没有快照（record 时未给 --snapshot）" % eid)
            shutil.copyfile(e["snapshot"], out)
            return {"id": eid, "out": str(out), "source": e["snapshot"]}
    raise SystemExit("找不到版本 %r" % eid)


def main():
    ap = argparse.ArgumentParser(description="编辑谱系账本")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("record")
    r.add_argument("--root", required=True)
    r.add_argument("--plan", required=True)
    r.add_argument("--instruction")
    r.add_argument("--ops")
    r.add_argument("--mask")
    r.add_argument("--metrics")
    r.add_argument("--tag")
    r.add_argument("--parent")
    r.add_argument("--snapshot")

    t = sub.add_parser("tree")
    t.add_argument("--root", required=True)

    c = sub.add_parser("checkout")
    c.add_argument("--root", required=True)
    c.add_argument("--id", required=True)
    c.add_argument("--out", required=True)

    a = ap.parse_args()
    if a.cmd == "record":
        load = lambda p: json.loads(Path(p).read_text(encoding="utf-8")) if p else None
        print(json.dumps(record(a.root, a.plan, a.instruction, load(a.ops), load(a.mask),
                                load(a.metrics), a.tag, a.parent, a.snapshot), ensure_ascii=False))
    elif a.cmd == "tree":
        print("\n".join(tree(a.root)) or "(empty)")
    else:
        print(json.dumps(checkout(a.root, a.id, a.out), ensure_ascii=False))


if __name__ == "__main__":
    main()
