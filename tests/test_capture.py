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


def test_writer_roundtrip(tmp_path):
    from tscan.capture import CaptureWriter, parse_capture_file
    p = tmp_path / "cap.csv"
    with CaptureWriter(str(p), meta={"adapter": "X", "bus": "CAN3"}) as w:
        w.write(0, 0x219, bytes([0x00, 0x80, 0x7F, 0x00, 0x82, 0x02, 0x00, 0x04]))
    meta, frames = parse_capture_file(str(p))
    assert meta["bus"] == "CAN3"
    assert frames[0][1] == 0x219


def test_writer_flushes_each_write(tmp_path):
    # data must be on disk immediately, BEFORE the context closes — so a stop/
    # crash/sleep (e.g. killing a drive capture) never loses the captured frames
    from tscan.capture import CaptureWriter, parse_capture_file
    p = tmp_path / "f.csv"
    with CaptureWriter(str(p), meta={"bus": "CAN3"}) as w:
        w.write(0, 0x219, bytes([1, 2, 3]))
        _m, frames = parse_capture_file(str(p))   # read mid-context, no close yet
        assert frames and frames[0][1] == 0x219
