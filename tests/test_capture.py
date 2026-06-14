import os
from tscan.capture import parse_capture_file
from tests.conftest import FIXTURES


def test_parse_capture_file_reads_meta_and_frames():
    meta, frames = parse_capture_file(os.path.join(FIXTURES, "sample_0219.csv"))
    assert meta["adapter"] == "STN1155 v5.6.19"
    assert meta["bus"] == "CAN3"
    assert len(frames) == 2
    t_ms, can_id, data = frames[0]
    assert t_ms == 0
    assert can_id == 0x219
    assert data == bytes([0x00, 0x80, 0x7F, 0x00, 0x82, 0x02, 0x00, 0x04])
