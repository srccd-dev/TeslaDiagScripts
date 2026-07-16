"""PCAN capture backend tests — use an injected fake bus, no hardware needed."""
from tscan.capture import capture_pcan, parse_capture_file


class _FakeMsg:
    def __init__(self, arbitration_id, data):
        self.arbitration_id = arbitration_id
        self.data = data


class _FakeBus:
    """Minimal python-can Bus stand-in: yields canned messages, then None."""
    def __init__(self, msgs):
        self._msgs = list(msgs)
        self.shut = False

    def recv(self, timeout=None):
        return self._msgs.pop(0) if self._msgs else None

    def shutdown(self):
        self.shut = True


REAL_0219 = bytes([0x00, 0x80, 0x7F, 0x00, 0x82, 0x02, 0x00, 0x04])


def test_capture_pcan_writes_capture_file(tmp_path):
    bus = _FakeBus([_FakeMsg(0x219, REAL_0219), _FakeMsg(0x102, bytes([1, 2, 3]))])
    out = str(tmp_path / "p.csv")
    capture_pcan(10, out_path=out, bus=bus, max_frames=2)
    meta, frames = parse_capture_file(out)
    assert meta["adapter"].startswith("PCAN")
    assert frames[0][1] == 0x219
    assert frames[0][2] == REAL_0219
    assert frames[1][1] == 0x102
    # injected bus is NOT shut down by us (caller owns it)
    assert bus.shut is False


def test_capture_pcan_records_start_timestamp(tmp_path):
    # An absolute start time is required to align a capture against Toolbox's
    # clock (our frames carry only relative t_ms).
    from datetime import datetime
    bus = _FakeBus([_FakeMsg(0x219, REAL_0219)])
    out = str(tmp_path / "p.csv")
    capture_pcan(10, out_path=out, bus=bus, max_frames=1)
    meta, _frames = parse_capture_file(out)
    assert "start" in meta
    ts = datetime.fromisoformat(meta["start"])        # ISO8601 w/ tz offset
    assert ts.tzinfo is not None                      # unambiguous, not naive
    assert abs((datetime.now().astimezone() - ts).total_seconds()) < 60


def test_capture_pcan_id_filter(tmp_path):
    bus = _FakeBus([_FakeMsg(0x219, REAL_0219), _FakeMsg(0x102, bytes([1, 2, 3]))])
    out = str(tmp_path / "p.csv")
    capture_pcan(10, ids=["219"], out_path=out, bus=bus, max_frames=2)
    _meta, frames = parse_capture_file(out)
    assert [f[1] for f in frames] == [0x219]


def test_assert_live():
    import pytest
    from tscan.capture import _assert_live, CaptureEmpty
    with pytest.raises(CaptureEmpty):
        _assert_live(0, 5)        # zero frames in the window -> dead link
    _assert_live(3, 5)            # frames flowing -> no raise


def test_capture_pcan_aborts_on_dead_link(tmp_path):
    import pytest
    from tscan.capture import CaptureEmpty
    bus = _FakeBus([])            # empty -> no frames ever
    with pytest.raises(CaptureEmpty):
        capture_pcan(10, out_path=str(tmp_path / "dead.csv"), bus=bus, liveness_secs=0)
