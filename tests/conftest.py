import os
import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DBC = os.path.join(REPO, "data", "tesla_models.dbc")
FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


@pytest.fixture(scope="session")
def decoder():
    from tscan.core import Decoder  # lazy: keeps conftest importable before core exists
    return Decoder(DBC)


@pytest.fixture
def engine(decoder):
    from tscan.overlay import load_overlay, DecodeEngine
    overlay_path = os.path.join(REPO, "data", "overlay.json")
    return DecodeEngine(decoder, load_overlay(overlay_path))
