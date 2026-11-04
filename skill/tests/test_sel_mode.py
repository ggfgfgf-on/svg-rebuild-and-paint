"""选择粒度：any ⊇ center ⊇ all，且 all 不溢出选区。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))
import numpy as np
import masks


def _doc():
    return {'canvas': [200, 200],
            'strokes': [
                {'layer': 'band', 'color': [10, 90, 10], 'size': 4, 'opacity': 1.0, 'points': [[50, 100]]},   # 完全在内
                {'layer': 'band', 'color': [10, 90, 10], 'size': 4, 'opacity': 1.0, 'points': [[95, 100], [150, 100]]},  # 跨边界
                {'layer': 'band', 'color': [10, 90, 10], 'size': 4, 'opacity': 1.0, 'points': [[180, 100]]},  # 完全在外
            ]}


def main():
    m = masks.rect(0, 0, 100, 200)
    d = _doc()
    any_ = masks.select(d, m, mode='any')
    cen = masks.select(d, m, mode='center')
    all_ = masks.select(d, m, mode='all')
    assert set(any_) >= set(cen) >= set(all_), (any_, cen, all_)
    assert 0 in all_ and 2 not in any_, (all_, any_)
    assert 1 in any_ and 1 not in all_, '跨边界笔：any 收、all 不收'
    assert 2 not in cen and 2 not in all_
    print('PASS sel_mode any=%s center=%s all=%s' % (any_, cen, all_))
    print('RESULT OK')
    return True


if __name__ == '__main__':
    sys.exit(0 if main() else 1)
