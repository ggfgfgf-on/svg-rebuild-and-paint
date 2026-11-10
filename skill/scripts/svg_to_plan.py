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
    print(json.dumps(plan_svg.load_svg_plan(a.svg, a.out), ensure_ascii=False))

if __name__ == "__main__":
    main()
