import pytest


@pytest.fixture
def tmp(tmp_path):
    """Legacy alias: pytest tests use `tmp` as a Path."""
    return tmp_path
