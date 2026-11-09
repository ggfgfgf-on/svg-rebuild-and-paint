"""plan.json -> SVG (one <path> per stroke, layer groups)."""
import argparse, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import plan_svg

def main():
    ap = argparse.ArgumentParser(description="plan.json -> SVG")
    ap.add_argument("plan")
    ap.add_argument("out")
    ap.add_argument("--soft-filter", action="store_true", help="添加柔边 Gaussian filter（默认不加，避免整笔发糊）")
    ap.add_argument("--pure", action="store_true", help="强制纯 Python 路径")
    ap.add_argument("--stamps", action="store_true", help="stamp 级 SVG：每个 stamp 一个 circle，文件大")
    ap.add_argument("--compact", action="store_true", help="写完后做无损路径压缩")
    a = ap.parse_args()
    if a.stamps:
        import time
        from pathlib import Path
        import plan_io
        doc, _, _ = plan_io.load_plan(a.plan)
        t0 = time.perf_counter()
        try:
            import plan_svg_native as N
            svg = N.plan_to_svg_stamps(doc) if N.available() else plan_svg.plan_to_svg_stamps(doc)
        except Exception:
            svg = plan_svg.plan_to_svg_stamps(doc)
        Path(a.out).write_text(svg, encoding="utf-8")
        if a.compact:
            import svg_compact
            svg = svg_compact.compact_svg(svg)
            Path(a.out).write_text(svg, encoding="utf-8")
        print(json.dumps({"plan": a.plan, "svg": a.out, "svg_bytes": len(svg.encode("utf-8")),
                          "svg_s": round(time.perf_counter() - t0, 4), "mode": "stamps"}, ensure_ascii=False))
        return
    if not a.pure:
        try:
            import plan_svg_native as N
            if N.available():
                import time
                from pathlib import Path
                t0 = time.perf_counter()
                N.plan_file_to_svg(a.plan, a.out)
                print(json.dumps({"plan": a.plan, "svg": a.out, "svg_bytes": Path(a.out).stat().st_size,
                                  "svg_s": round(time.perf_counter() - t0, 4)}, ensure_ascii=False))
                return
        except Exception:
            pass
    print(json.dumps(plan_svg.dump_plan_svg(a.plan, a.out, filters=a.soft_filter), ensure_ascii=False))

if __name__ == "__main__":
    main()
