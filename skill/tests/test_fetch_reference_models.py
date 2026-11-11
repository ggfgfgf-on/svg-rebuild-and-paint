"""Reference model download manifest: no network, no weights."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import fetch_reference_models as F

def test_manifest():
    m = F.manifest()
    assert "sdxs" in m and "imf-b2" in m
    assert "sana" not in m
    assert m["sdxs"]["redistribute"] is False
    assert m["imf-b2"]["redistribute"] is False
    print("PASS reference model manifest")

if __name__ == "__main__":
    test_manifest()
    print("all fetch_reference_models tests passed")
