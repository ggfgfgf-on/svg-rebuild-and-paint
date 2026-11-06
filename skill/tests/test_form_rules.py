"""Contract test for form.py --rules override / validation."""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
FORM = HERE / "scripts" / "form.py"


def main():
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        svg = td / "t.svg"
        svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">'
                       '<rect width="100" height="100" fill="#ffffff"/>'
                       '<rect x="30" y="20" width="40" height="50" fill="#f7d8bc"/>'
                       '<rect x="30" y="20" width="40" height="15" fill="#2f1d19"/></svg>', encoding="utf-8")
        rules = td / "r.json"
        rules.write_text('[["head_w_over_h", 1.0, 0.1, "custom"]]', encoding="utf-8")
        out = td / "o.json"
        r = subprocess.run([sys.executable, str(FORM), str(svg), "--size", "100x100",
                            "--rules", str(rules), "--out", str(out)],
                           capture_output=True, text=True)
        assert r.returncode == 0, r.stderr[-800:]
        data = json.loads(out.read_text(encoding="utf-8"))
        assert set(data["ratios"]) == {"head_w_over_h"}, data["ratios"]
        assert data["verdicts"][0]["note"] == "custom", data["verdicts"]

        bad = td / "bad.json"
        bad.write_text('{"not": "a list"}', encoding="utf-8")
        r2 = subprocess.run([sys.executable, str(FORM), str(svg), "--size", "100x100",
                             "--rules", str(bad)], capture_output=True, text=True)
        assert r2.returncode != 0 and "bad --rules" in r2.stderr, r2.stderr[-500:]

        print("PASS form --rules custom + invalid rejection")


if __name__ == "__main__":
    main()
