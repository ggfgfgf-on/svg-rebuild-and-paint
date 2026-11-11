"""Driver contract doc is generated from JSON and must stay in sync."""
import json
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import driver_doc


def test_contract_shape():
    data = json.loads((ROOT / "docs" / "driver.contract.json").read_text(encoding="utf-8"))
    assert data["driver"]["input"].startswith("plan:")
    assert data["driver"]["implicit_model_load"] is False
    assert "sdxs-512" in data["models"]
    assert "imf-b2" in data["models"]
    assert data["models"]["sdxs-512"]["reference_image"] is True
    assert data["models"]["sdxs-512"]["writes_plan"] is False
    assert data["models"]["imf-b2"]["reference_image"] is False


def test_generated_doc_is_current():
    assert (ROOT / "docs" / "DRIVER.md").read_text(encoding="utf-8") == driver_doc.build()

if __name__ == "__main__":
    test_contract_shape()
    test_generated_doc_is_current()
    print("all driver_doc tests passed")
