"""导出层契约：.kra 的 composite-op 与 .psd 的 blend key 必须来自计划的混合模式。"""
import struct
import sys
import zipfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import export_layers as E


def _img(v):
    a = np.zeros((16, 16, 4), np.uint8)
    a[..., 0] = v
    a[..., 3] = 255
    return a


def test_kra_blends(tmp):
    p = Path(tmp) / "a.kra"
    E.write_kra(p, [("bed", _img(10)), ("shadow", _img(20)), ("highlight", _img(30))],
                blends={"bed": "normal", "shadow": "multiply", "highlight": "screen"})
    xml = zipfile.ZipFile(p).read("maindoc.xml").decode("utf-8")
    ops = [seg.split('"')[0] for seg in xml.split('composite-op="')[1:]]
    assert ops == ["svg:src-over", "svg:multiply", "svg:screen"], ops
    print("PASS kra: composite-op =", ops)


def test_kra_default_unchanged(tmp):
    p = Path(tmp) / "b.kra"
    E.write_kra(p, [("bed", _img(10)), ("x", _img(20))])
    xml = zipfile.ZipFile(p).read("maindoc.xml").decode("utf-8")
    assert xml.count('composite-op="svg:src-over"') == 2
    print("PASS kra: no blends -> all normal (backwards compatible)")


def test_psd_blends(tmp):
    p = Path(tmp) / "a.psd"
    E.write_psd(p, [("bed", _img(10)), ("shadow", _img(20))],
                blends={"bed": "normal", "shadow": "multiply"})
    data = p.read_bytes()
    assert b"8BPS" in data[:4]
    assert b"8BIMmul " in data, "multiply key missing"
    print("PASS psd: blend keys norm/mul written")


def test_mapping():
    assert E._kra_blend("softlight") == "svg:soft-light"
    assert E._kra_blend("nonsense") == "svg:src-over"
    assert E._psd_blend("overlay") == b"over"
    print("PASS mapping: softlight/overlay/nonsense")


def main():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        test_kra_blends(td)
        test_kra_default_unchanged(td)
        test_psd_blends(td)
        test_mapping()
    print("RESULT OK")


if __name__ == "__main__":
    main()
