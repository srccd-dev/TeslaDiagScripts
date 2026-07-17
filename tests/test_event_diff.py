"""event_diff: identify which frame carries a signal by correlating a known event.

Synthetic frames only - no capture file, no hardware.
"""
from datetime import datetime

from tools.event_diff import event_diff, _ms


BASE = (0, 10_000)          # ms
EVT = (20_000, 30_000)      # ms


def _frames(evt_byte1):
    """0x100: byte0 is a free-running counter, byte1 is the signal under test.
    byte1 is frozen at 0x05 during the baseline; `evt_byte1(i)` drives it in the event.
    """
    out = []
    for i, t in enumerate(range(BASE[0], BASE[1], 100)):
        out.append((t, 0x100, bytes([i % 256, 0x05])))
    for i, t in enumerate(range(EVT[0], EVT[1], 100)):
        out.append((t, 0x100, bytes([(i + 500) % 256, evt_byte1(i)])))
    return out


def test_finds_byte_active_only_during_event():
    hits = event_diff(_frames(lambda i: 0x05 + (i % 2)), BASE, EVT)
    found = {(cid, i) for _r, cid, i, _rb, _ne, _nb in hits}
    assert (0x100, 1) in found          # toggling byte -> the signal we want


def test_ignores_free_running_counter():
    # byte0 changes constantly in BOTH windows, so it must not be flagged
    hits = event_diff(_frames(lambda i: 0x05 + (i % 2)), BASE, EVT)
    found = {(cid, i) for _r, cid, i, _rb, _ne, _nb in hits}
    assert (0x100, 0) not in found


def test_quiet_event_yields_nothing():
    # byte1 stays frozen through the event too -> no candidates
    hits = event_diff(_frames(lambda i: 0x05), BASE, EVT)
    assert {(cid, i) for _r, cid, i, _rb, _ne, _nb in hits} == set()


def test_ms_maps_wall_clock_to_capture_offset():
    start = datetime.fromisoformat("2026-07-16T18:21:09-05:00")
    assert _ms(start, "18:21:09") == 0
    assert _ms(start, "19:00") == (38 * 60 + 51) * 1000      # 38m51s later
    # a time earlier in the day than the start rolls to the next day
    assert _ms(start, "00:30") > 0
