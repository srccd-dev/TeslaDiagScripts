# tests/test_profile_frame.py
import os
from tools.profile_frame import profile
from tscan.core import Decoder

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DBC = os.path.join(REPO, "data", "tesla_models.dbc")


def test_profile_reports_length_and_variance():
    frames = [
        (0, 0x3F8, bytes([0xFB, 0x02, 0xFA, 0x02, 0xF7, 0x02, 0xFD, 0x02])),
        (1, 0x3F8, bytes([0x00, 0x03, 0xFA, 0x02, 0xFA, 0x02, 0xFB, 0x02])),
        (2, 0x111, bytes([0x01])),                      # different id ignored
    ]
    r = profile(frames, 0x3F8, Decoder(DBC))
    assert r["count"] == 2
    assert r["lengths"] == {8: 2}
    assert r["distinct_payloads"] == 2
    assert len(r["byte_variance"]) == 8
    assert r["byte_variance"][0] == 2                   # byte0 varies (FB vs 00)
    assert r["dbc_name"] == "DCDC_alertMatrix1"


def test_profile_unknown_id_returns_empty():
    r = profile([(0, 0x111, b"\x01")], 0x7FF, Decoder(DBC))
    assert r["count"] == 0
