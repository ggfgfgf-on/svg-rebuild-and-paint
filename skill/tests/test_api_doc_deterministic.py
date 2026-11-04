"""docs/API.md 的生成必须与 cwd/环境无关：工程实测不能漏进生成物。"""
import hashlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API = ROOT / "docs" / "API.md"


def _gen(env_extra=None, cwd=None):
    env = dict(os.environ)
    env.update(env_extra or {})
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "api_doc.py")],
                       capture_output=True, text=True, encoding="utf-8", cwd=cwd or str(ROOT), env=env)
    assert r.returncode == 0, r.stderr
    return hashlib.sha256(API.read_bytes()).hexdigest()


def main():
    base = _gen()
    with tempfile.TemporaryDirectory() as td:
        proj = Path(td) / "sources_measured.json"
        proj.write_bytes(b'{"cells": {"iou_by_tile": {"8": 0.968, "55": 0.813}}}')
        via_env = _gen({"SVP_MEASURED": str(proj)})
        in_dir = _gen(cwd=td)
    assert base == via_env, "设了 SVP_MEASURED 后生成的 API.md 变了：工程实测漏进了文档"
    assert base == in_dir, "换 cwd 生成的 API.md 变了：生成物依赖工作目录"
    print("PASS API.md 与 cwd/SVP_MEASURED 无关（三种方式同一 hash）")
    print("RESULT OK")
    return True


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
