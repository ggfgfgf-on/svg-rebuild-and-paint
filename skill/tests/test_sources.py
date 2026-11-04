"""选择源注册表：语义即约束（粗来源不许直接上色），且注册表与 _SEL_KEYS 必须一致。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))
import ops


def _spec(extra=None):
    s = {'cells': {'image': 'x.png', 'windows': [{'roi': [0, 0, 10, 10], 'cols': 2, 'rows': 2, 'pos': ['A1']}]}}
    if extra:
        s.update(extra)
    return s


def main():
    assert set(ops.SOURCES) == set(ops._SEL_KEYS), (sorted(set(ops._SEL_KEYS) - set(ops.SOURCES)),
                                                     sorted(set(ops.SOURCES) - set(ops._SEL_KEYS)))
    # 粗来源 + 改色算子 -> 记一条 notice 提醒模型（不拒绝执行）
    n = ops.check_source(_spec(), 'set_layer_hue')
    assert n and n['level'] == 'warn', n
    assert 'refine' in n['text'] and 'allow_coarse' in n['text'], n['text']
    assert n['measured'], 'notice 里要带实测值'
    assert set(n['fix']) and len(n['fix']) == 3, n['fix']
    # 三种放行方式都不该再提醒
    assert ops.check_source(_spec({'cells': dict(_spec()['cells'], refine=True)}), 'set_layer_hue') is None
    assert ops.check_source(_spec({'allow_coarse': True}), 'set_layer_hue') is None
    assert ops.check_source(_spec(), 'densify') is None
    assert ops.check_source({'mask': {}}, 'set_layer_hue') is None
    head, rows = ops.describe_sources()
    assert len(rows) == len(ops._SEL_KEYS) and head[0] == '来源'
    cells_row = [r for r in rows if r[0] == 'cells'][0]
    assert cells_row[1] == '是' and '0.813' in cells_row[3] and '从格子' in cells_row[3], cells_row
    print('PASS 注册表 %d 个来源；cells 闸门 / refine / allow_coarse / 文档行' % len(rows))
    print('RESULT OK')
    return True


if __name__ == '__main__':
    sys.exit(0 if main() else 1)
