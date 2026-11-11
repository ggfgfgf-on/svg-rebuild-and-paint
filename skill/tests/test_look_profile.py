if __name__ != "__main__":
    import pytest
    pytest.skip("standalone acceptance script", allow_module_level=True)

import json, sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import look_profile as LP

ok = True

def check(name, cond, extra=""):
    global ok
    print(("PASS " if cond else "FAIL ") + name + (" " + str(extra) if extra else ""))
    ok = ok and bool(cond)

with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    # 1) 文件不存在 -> 恒等
    check("missing profile -> None", LP.load_profile(td / "nope.json") is None)
    check("missing -> no ops", LP.ops_from(None) == [])

    # 2) 恒等 tone 不产生算子
    neutral = td / "neutral.json"
    neutral.write_text(json.dumps({"tone": {"gain": 1.0, "lift": 0.0, "gamma": 1.0}}), encoding="utf-8")
    check("identity tone -> no op", LP.ops_from(LP.load_profile(neutral)) == [])

    # 3) 非恒等 tone -> 一个 tone_map
    prof = td / "prof.json"
    prof.write_text(json.dumps({"tone": {"gain": 0.95, "gamma": 1.1}, "ops": [{"op": "erase", "layer": "highlight"}]}), encoding="utf-8")
    specs = LP.ops_from(LP.load_profile(prof))
    check("tone op first", specs[0]["op"] == "tone_map" and abs(specs[0]["gain"] - 0.95) < 1e-9, specs[0])
    check("passthrough ops kept", specs[1] == {"op": "erase", "layer": "highlight"})

    # 4) 命令行覆盖在后面
    merged = LP.resolve([{"op": "tone_map", "gain": 0.85}], prof)
    check("cli ops after profile", len(merged) == 3 and merged[-1]["gain"] == 0.85, merged)

    # 5) 默认 profile 文件存在且可解析
    d = LP.load_profile()
    check("repo default profile loads", d is not None and "tone" in d, d and d.get("_source"))

print("RESULT", "OK" if ok else "FAILED")
if __name__ == "__main__":
    sys.exit(0 if ok else 1)