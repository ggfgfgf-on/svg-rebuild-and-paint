"""Optional real-model smoke tests; skipped unless explicitly enabled.

    DSH_MODEL_SMOKE=1 DSH_SDXS_PATH=... pytest tests/test_model_smoke.py
"""
import os
import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import model_smoke


@pytest.mark.skipif(os.environ.get("DSH_MODEL_SMOKE") != "1",
                    reason="set DSH_MODEL_SMOKE=1 to run real model smoke")
@pytest.mark.skipif(not os.environ.get("DSH_SDXS_PATH"),
                    reason="set DSH_SDXS_PATH for SDXS smoke")
def test_sdxs_smoke(tmp_path):
    out = model_smoke.sdxs(tmp_path)
    assert Path(out["text"]["image"]).is_file()
    assert Path(out["img2img"]["image"]).is_file()


@pytest.mark.skipif(os.environ.get("DSH_MODEL_SMOKE") != "1",
                    reason="set DSH_MODEL_SMOKE=1 to run real model smoke")
@pytest.mark.skipif(not (os.environ.get("DSH_IMF_REPO") and os.environ.get("DSH_IMF_CKPT")),
                    reason="set DSH_IMF_REPO and DSH_IMF_CKPT for iMF smoke")
def test_imf_smoke(tmp_path):
    out = model_smoke.imf(tmp_path)
    assert Path(out["image"]).is_file()
