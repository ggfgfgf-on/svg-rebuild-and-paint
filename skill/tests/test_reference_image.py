"""Optional local reference adapters: interface tests only (no model download)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import reference_image as R

def test_status():
    s = R.status()
    assert "sdxs" in s and "imagenet" in s
    assert s["imagenet"]["available"] is False
    print("PASS reference_image status")

def test_validation():
    for fn in (lambda: R.text_reference("", "x.png"), lambda: R.text_reference("fox", "x.png", size=(100, 100)), lambda: R.text_reference("fox", "x.png", steps=0), lambda: R.class_reference(1000, "x")):
        try:
            fn()
        except Exception:
            pass
        else:
            raise AssertionError("invalid input was accepted")
    print("PASS reference_image validation")

def test_image_reference_validation(tmp_path):
    p = tmp_path / "ref.png"
    p.write_bytes(b"not-a-real-png")
    bad = [
        lambda: R.image_reference("", str(p), "x.png"),
        lambda: R.image_reference("fox", str(p), "x.png", backend="other"),
        lambda: R.image_reference("fox", str(p), "x.png", strength=0.0),
        lambda: R.image_reference("fox", str(p), "x.png", steps=0),
        lambda: R.image_reference("fox", str(p), "x.png", strength=0.2, steps=2),
        lambda: R.image_reference("fox", str(tmp_path / "missing.png"), "x.png"),
    ]
    for fn in bad:
        try:
            fn()
        except Exception:
            pass
        else:
            raise AssertionError("invalid image_reference input was accepted")


def test_class_runner():
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as td:
        def runner(class_id, out_png, seed):
            Path(out_png).write_bytes(b"png")
            return out_png
        out = R.class_reference(277, td, n=2, seed=10, runner=runner)
        assert out["class_id"] == 277 and len(out["images"]) == 2
        assert all(Path(p).exists() for p in out["images"])
    print("PASS class_reference runner contract")


if __name__ == "__main__":
    test_status()
    test_validation()
    test_class_runner()
    print("all reference_image tests passed")
