import os
import pytest
from tscan.core import Decoder

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DBC = os.path.join(REPO, "data", "tesla_models.dbc")
FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


@pytest.fixture(scope="session")
def decoder():
    return Decoder(DBC)
