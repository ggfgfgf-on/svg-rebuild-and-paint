"""标定闭环：calibrated.json 必须由脚本生成、模型选定、依据可查；默认值必须来自它。"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
import pick_instance as PI


def main():
    p = ROOT / 'scripts' / 'calibrated.json'
    assert p.exists(), '缺 calibrated.json：先跑 scripts/calibrate.py'
    d = json.loads(p.read_text(encoding='utf-8'))
    assert 'model_grow_px' in d, sorted(d)
    e = d['model_grow_px']
    for k in ('value', 'chosen_by', 'reason', 'sweep', 'contact_sheet', 'spec'):
        assert k in e, 'calibrated.json 缺字段 %s' % k
    assert e['chosen_by'] == 'model', e['chosen_by']
    assert len(e['reason'].strip()) >= 10, 'reason 太短，等于没依据'
    assert str(e['value']) in e['sweep'], '选中的值不在 sweep 里：%r' % (e['value'],)
    sheet = ROOT / e['contact_sheet']
    assert sheet.exists(), '接触表不存在：%s' % sheet
    # 默认值必须来自标定文件，而不是代码里的常量
    v = PI.calibrated('model_grow_px', None)
    assert v == float(e['value']), (v, e['value'])
    assert PI.calibrated('不存在的参数', 7) == 7, '缺项要回落到默认'
    print('PASS calibrated.json：%s=%s（%s）；接触表 %s KB' %
          ('model_grow_px', e['value'], e['chosen_by'], sheet.stat().st_size // 1024))
    print('RESULT OK')
    return True


if __name__ == '__main__':
    sys.exit(0 if main() else 1)
