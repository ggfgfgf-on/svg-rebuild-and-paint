"""接口文档不漂移：docs/API.md 必须等于 api_doc.build() 的输出。"""
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / 'scripts' / 'api_doc.py'


def main():
    r = subprocess.run([sys.executable, str(SCRIPT), '--check'], capture_output=True, text=True,
                       encoding='utf-8', errors='replace')
    if r.returncode != 0:
        print('FAIL 接口文档过期：', (r.stdout + r.stderr).strip())
        return False
    print('PASS 接口文档与代码一致 (docs/API.md)')
    return True


if __name__ == '__main__':
    sys.exit(0 if main() else 1)
