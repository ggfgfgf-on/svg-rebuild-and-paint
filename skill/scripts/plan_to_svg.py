"""plan.json -> SVG (one <path> per stroke, layer groups)."""
import argparse, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import plan_svg

def main():
    ap = argparse.ArgumentParser(description="plan.json -> SVG")
    ap.add_argument("plan")
    ap.add_argument("out")
    ap.add_argument("--no-filters", action="store_true", help="不写柔边 filter（更快、更小）")
    a = ap.parse_args()
    print(json.dumps(plan_svg.dump_plan_svg(a.plan, a.out, filters=not a.no_filters), ensure_ascii=False))

if __name__ == "__main__":
    main()
