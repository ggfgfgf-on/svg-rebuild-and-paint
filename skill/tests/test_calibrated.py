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
    want = {'model_grow_px', 'min_ratio', 'max_grow_px', 'neg_px', 'attach_px',
            'edge_min_contrast', 'accept_max_area_change'}
    assert want <= set(d), '缺标定项：%s' % sorted(want - set(d))
    for name in sorted(want):
        e = d[name]
        assert 'value' in e, '%s 缺 value' % name
        assert e.get('chosen_by') in ('model', 'rule'), '%s chosen_by=%r' % (name, e.get('chosen_by'))
        assert len(str(e.get('reason', '')).strip()) >= 8, '%s 理由太短' % name
        NUMERIC = {'model_grow_px', 'min_ratio', 'max_grow_px', 'neg_px', 'attach_px'}
        if e['chosen_by'] == 'model':
            for k in ('sweep', 'contact_sheet'):
                assert k in e, '%s 缺 %s' % (name, k)
            sheet = str(e.get('contact_sheet') or '')
            has_sheet = bool(sheet) and (ROOT / sheet).is_file()
            has_interval = bool(e.get('intersection'))
            # 证据要么是接触表（单样本扫档），要么是多图交集（per-sample 区间）
            assert has_sheet or has_interval, '%s 既没有接触表也没有多图交集，等于没证据' % name
            if name in NUMERIC:
                keys = [float(k) for k in e['sweep']]
                assert float(e['value']) in keys, '%s 的值 %r 不在 sweep 里 (%s)' % (name, e['value'], keys)
            else:
                # 判据型（阈值/门限）：sweep 是样本表或过程表，不要求 value 是 key，但要落在合理区间
                assert 0.0 < float(e['value']) <= 1.0, '%s 的阈值 %r 不在 (0,1]' % (name, e['value'])
                assert e['sweep'], '%s 的 sweep 为空' % name
        else:
            assert 'rule' in e or e.get('reason'), '%s 规则型要写 rule' % name
    e = d['model_grow_px']
    sheet = ROOT / e['contact_sheet']
    # 默认值必须来自标定文件，而不是代码里的常量
    v = PI.calibrated('model_grow_px', None)
    assert v == float(e['value']), (v, e['value'])
    # 默认值接线：这几个函数的默认必须真的从 calibrated.json 来
    assert PI.calibrated('min_ratio', None) == float(d['min_ratio']['value'])
    assert PI.calibrated('max_grow_px', None) == float(d['max_grow_px']['value'])
    assert PI.calibrated('neg_px', None) == float(d['neg_px']['value'])
    assert PI.calibrated('edge_min_contrast', None) == float(d['edge_min_contrast']['value'])
    assert PI.calibrated('不存在的参数', 7) == 7, '缺项要回落到默认'
    print('PASS calibrated.json：%s=%s（%s）；接触表 %s KB' %
          ('model_grow_px', e['value'], e['chosen_by'], sheet.stat().st_size // 1024))
    print('RESULT OK')
    return True


if __name__ == '__main__':
    sys.exit(0 if main() else 1)
