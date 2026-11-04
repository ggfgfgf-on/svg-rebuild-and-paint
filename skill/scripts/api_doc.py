"""从代码生成接口文档（docs/API.md），保证文档不会和实现漂移。

    python scripts/api_doc.py            # 重新生成 docs/API.md
    python scripts/api_doc.py --check    # 只比较，不一致就非零退出（测试用）
"""
import inspect
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
OUT = HERE.parent / "docs" / "API.md"
BT = chr(96)
UNI = ("selection", "weights", "guidance", "seed", "mask_obj", "sel_mode")


def c(s):
    return BT + str(s) + BT


def _first_para(doc):
    if not doc:
        return ""
    txt = inspect.cleandoc(doc)
    parts = [p for p in re.split(r"\n\s*\n", txt) if p.strip()]
    return " ".join(parts[0].split()) if parts else ""


def _sig(fn):
    try:
        return re.sub(r"\s+", " ", str(inspect.signature(fn)))
    except (TypeError, ValueError):
        return "(...)"


def build():
    import ops
    import pick_instance as PI
    import masks
    L = []
    A = L.append
    A("# 接口清单（自动生成，勿手改）")
    A("")
    A("> 由 " + c("scripts/api_doc.py") + " 从代码生成；改了接口就重跑，否则 " + c("tests/test_api_doc.py")
      + " 会失败。语义（每个源该在什么场景用、代价多少）在 SKILL.md 的「选择源」一节，这里只保证签名不漂移。")
    A("")
    A("## 选择源语义（由 ops.SOURCES 生成，与闸门同源）")
    A("")
    head, rows = ops.describe_sources()
    A("| " + " | ".join(head) + " |")
    A("|" + "|".join("---" for _ in head) + "|")
    for r in rows:
        A("| " + " | ".join(str(x) for x in r) + " |")
    A("")
    A("**闸门**：coarse=是 的来源直接给改色类算子会抛错（整笔改色会越界）；放行方式是 refine: true 或 allow_coarse: true。约束在 ops.check_source，本表由 ops.describe_sources() 渲染，所以不可能只改行为不改表。")
    A("")
    A("## 选择源（selection sources）")
    A("")
    A(c("resolve_mask") + " 认识这些键；" + c("ops.apply") + " 只解析 " + c("ops._SEL_KEYS")
      + " 里登记过的来源——**新增来源要同时改两处**，否则算子静默作用到全图。")
    A("")
    A(BT * 3)
    A(", ".join(ops._SEL_KEYS))
    A(BT * 3)
    A("")
    A("## 通用键（所有算子都接受）")
    A("")
    A("结构类：" + ", ".join(c(k) for k in sorted(ops.INTERNAL)))
    A("")
    A("执行类：" + ", ".join(c(k) for k in UNI))
    A("")
    A(c("sel_mode") + "：any（默认，笔触任一点碰到选区即整笔生效）、center（中心点在选区内）、"
      "all（整笔在选区内，最不溢出）。改色类算子建议 center 或 all。")
    A("")
    for name in sorted(ops.OPS):
        fn = ops.OPS[name]
        req = list(ops.REQUIRED.get(name, []))
        A("### " + c(name) + _sig(fn))
        A("")
        desc = _first_para(fn.__doc__)
        if desc:
            A(desc)
            A("")
        A("- 必填：" + (", ".join(c(k) for k in req) or "（无）"))
        optional = sorted(k for k in ops.ALLOWED.get(name, []) if k not in req)  # ALLOWED 若为 set，必须排序，否则每进程顺序不同
        A("- 可选：" + (", ".join(c(k) for k in optional) or "（无）"))
        A("")
    for mod_name, mod in (("pick_instance", PI), ("masks", masks)):
        A("## " + c(mod_name) + "（辅助函数）")
        A("")
        for nm, obj in sorted(vars(mod).items()):
            if nm.startswith("_") or not inspect.isfunction(obj):
                continue
            if getattr(obj, "__module__", None) != mod.__name__:
                continue
            A("### " + c(nm) + _sig(obj))
            A("")
            desc = _first_para(obj.__doc__)
            if desc:
                A(desc)
                A("")
    return "\n".join(L).rstrip() + "\n"


def main():
    text = build()
    if "--check" in sys.argv:
        cur = OUT.read_bytes().decode("utf-8") if OUT.exists() else ""
        if cur != text:
            print("docs/API.md 与代码不一致：跑 python scripts/api_doc.py 重新生成", file=sys.stderr)
            return 1
        print("API 文档与代码一致")
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_bytes(text.encode("utf-8"))
    print("wrote %s (%d chars)" % (OUT, len(text)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
