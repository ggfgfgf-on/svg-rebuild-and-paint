"""多图标定：样本数 > 1 时，可接受区间取交集；单样本接口可用。"""
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
import numpy as np
import cv2 as cv
import calibrate as CA
import masks as M


def _make(dirp, radius, noise_seed):
    h = w = 160
    img = np.zeros((h, w, 3), np.uint8); img[:] = (28, 34, 26)
    cv.circle(img, (80, 80), radius, (120, 170, 100), -1)
    rng = np.random.default_rng(noise_seed)
    img = np.clip(img.astype(np.int16) + rng.integers(-6, 7, img.shape), 0, 255).astype(np.uint8)
    p = Path(dirp) / ('img%d.png' % radius)
    cv.imwrite(str(p), img)
    m = np.zeros((h, w), bool)
    yy, xx = np.mgrid[0:h, 0:w]
    m[(yy - 80) ** 2 + (xx - 80) ** 2 <= radius * radius] = True
    mp = Path(dirp) / ('mask%d.json' % radius)
    mp.write_bytes(json.dumps(M.to_json(M.from_bool(m))).encode('utf-8'))
    return {'image': str(p), 'trace': str(mp), 'base_mask': str(mp)}


def main():
    with tempfile.TemporaryDirectory() as td:
        s1 = _make(td, 40, 1)
        s2 = _make(td, 62, 2)
        sf = Path(td) / 'samples.json'
        sf.write_bytes(json.dumps({'samples': [s1, s2]}).encode('utf-8'))
        got = CA.load_samples(str(sf))
        assert len(got) == 2, got
        out = CA.sweep_multi('edge_min_contrast', got, [0.3, 0.4])
        assert out['samples'] == 2, out
        assert len(out['per_sample']) == 2, out['per_sample']
        for name, rows in out['per_sample'].items():
            assert rows['accurate']['should_pass'] and not rows['fat_3px']['should_pass'], (name, rows)
            lo, hi = rows['interval']
            assert lo < hi, (name, rows['interval'])
        assert out['intersection'], '两个样本的交集不该为空'
        lo, hi = out['intersection']
        for name, rows in out['per_sample'].items():
            plo, phi = rows['interval']
            assert lo >= plo and hi <= phi, (name, out['intersection'], rows['interval'])
        # 缺 trace 必须报错
        bad = Path(td) / 'bad.json'
        bad.write_bytes(json.dumps({'samples': [{'image': s1['image']}]}).encode('utf-8'))
        try:
            CA.load_samples(str(bad))
            raise AssertionError('缺 trace 应该报错')
        except ValueError:
            pass
        print('PASS 多图交集 %s（单样本区间 %s）' % (out['intersection'],
              [r['interval'] for r in out['per_sample'].values()]))
    print('RESULT OK')
    return True


if __name__ == '__main__':
    sys.exit(0 if main() else 1)
