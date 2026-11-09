"""SVG -> plan.json for bridge-generated/editable SVG."""
import argparse, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import plan_svg

def main():
    ap = argparse.ArgumentParser(description="SVG -> plan.json")
    ap.add_argument("svg")
    ap.add_argument("out")
    ap.add_argument("--pure", action="store_true", help="强制纯 Python 路径")
    a = ap.parse_args()
    if not a.pure:
        try:
            import plan_svg_native as N
            if N.available():
                import time
                from pathlib import Path
                t0 = time.perf_counter()
                N.svg_file_to_plan(a.svg, a.out)
                print(json.dumps({"svg": a.svg, "plan": a.out, "plan_bytes": Path(a.out).stat().st_size,
                                  "parse_s": round(time.perf_counter() - t0, 4)}, ensure_ascii=False))
                return
        except Exception:
            pass
    print(json.dumps(plan_svg.load_svg_plan(a.svg, a.out), ensure_ascii=False))

if __name__ == "__main__":
    main()
