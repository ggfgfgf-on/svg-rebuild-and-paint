"""cli_util 契约：目标亮度二选一、通用开关默认值、JSON 读写往返。"""
import argparse
import contextlib
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import cli_util as CU


def parser(with_target=True):
    ap = argparse.ArgumentParser()
    if with_target:
        CU.add_target_args(ap)
    CU.add_common_args(ap)
    return ap


def test_defaults():
    a = parser(with_target=False).parse_args([])
    assert a.gpu is False and a.threads == 8 and a.quiet is False
    a = parser(with_target=False).parse_args(["--gpu", "--threads", "3", "--quiet"])
    assert a.gpu is True and a.threads == 3 and a.quiet is True
    print("PASS common args: defaults gpu=0 threads=8 quiet=0")


def test_target_resolution():
    ap = parser()
    a = ap.parse_args(["--target-luma", "22.5"])
    assert CU.resolve_target(a, lambda p: 0.0) == 22.5
    hit = {}
    a = ap.parse_args(["--target-image", "ref.png"])
    got = CU.resolve_target(a, lambda p: (hit.setdefault("p", p), 33.0)[1])
    assert got == 33.0 and hit["p"] == "ref.png", (got, hit)
    print("PASS target: --target-luma / --target-image both resolve")


def test_target_errors():
    ap = argparse.ArgumentParser()
    CU.add_target_args(ap, required=True)
    with contextlib.redirect_stderr(io.StringIO()):     # argparse 的 usage 别污染测试输出
        try:
            ap.parse_args([])
            raise AssertionError("required group must fail with neither")
        except SystemExit:
            pass
        try:
            ap.parse_args(["--target-image", "a.png", "--target-luma", "1"])
            raise AssertionError("both must fail (mutually exclusive)")
        except SystemExit:
            pass
    loose = argparse.ArgumentParser()
    CU.add_target_args(loose, required=False)
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            CU.resolve_target(loose.parse_args([]), lambda p: 0.0)
        raise AssertionError("resolve_target must reject neither")
    except SystemExit:
        pass
    print("PASS target errors: required-group and resolve_target both reject none/both")


def test_json(tmp):
    p = Path(tmp) / "o.json"
    CU.write_json(p, [{"op": "tone_map", "gain": 0.9174, "note": "亮度"}])
    back = CU.load_json(p)
    assert back[0]["gain"] == 0.9174 and back[0]["note"] == "亮度"
    CU.log_unless_quiet(argparse.Namespace(quiet=True), "should not print")
    print("PASS json roundtrip: unicode kept, gain exact")


def main():
    import tempfile
    test_defaults()
    test_target_resolution()
    test_target_errors()
    with tempfile.TemporaryDirectory() as td:
        test_json(td)
    print("RESULT OK")


if __name__ == "__main__":
    main()
