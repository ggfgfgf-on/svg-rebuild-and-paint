"""vectorize.py 的命名参数必须和位置参数等价。

背景：host 工具 art_vectorize 传的是 `--k` / `--ss`，而 vectorize.py 历史上只认
位置参数（ref out K turd ...）。缺了命名解析时，模型一调就被
`ValueError: invalid literal for int() with base 10: '--k'` 打回——这是「工具加好了
但参数对不上」的典型故障，加一条回归锁。
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "photo-320x180.png"


def run(args):
    res = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "vectorize.py")] + args,
        capture_output=True, text=True, cwd=str(ROOT / "scripts"),
    )
    assert res.returncode == 0, "vectorize 退出码 %d\n%s" % (res.returncode, (res.stderr or res.stdout)[-800:])
    lines = [ln for ln in (res.stdout or "").strip().splitlines() if ln.startswith("{")]
    assert lines, "没有 JSON 输出：%s" % (res.stdout or "")[-400:]
    return json.loads(lines[-1])


def main():
    assert FIXTURE.exists(), "缺少 %s" % FIXTURE
    with tempfile.TemporaryDirectory(prefix="svp_vecflag_") as tmp:
        named_out = Path(tmp) / "named.svg"
        pos_out = Path(tmp) / "pos.svg"
        named = run([str(FIXTURE), str(named_out), "--k", "96"])
        pos = run([str(FIXTURE), str(pos_out), "96"])
        assert named["K"] == 96 and pos["K"] == 96, (named.get("K"), pos.get("K"))
        assert named["layers"] == pos["layers"], (named["layers"], pos["layers"])
        assert named_out.stat().st_size > 1000 and pos_out.stat().st_size > 1000
        ss = run([str(FIXTURE), str(Path(tmp) / "ss.svg"), "--k", "96", "--ss", "2"])
        assert ss["K"] == 96
    print("PASS vectorize --k/--ss 与位置参数等价（K=96，%d 层）" % named["layers"])
    print("RESULT OK")
    return True


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
