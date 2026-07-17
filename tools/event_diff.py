"""Identify which CAN frame carries a signal, by correlating a KNOWN physical event.

The workhorse reverse-engineering loop: perform a discrete action at a known
wall-clock time (window down/up, turn signal, mirror fold), then diff a quiet
baseline window against the event window. Frames that are STATIC in the baseline
but CHANGE during the event are the carriers - no DBC required.

Needs `start=<ISO8601>` in the capture metadata to map wall-clock -> t_ms.

Run as a module (the repo root must be on sys.path):
    python -m tools.event_diff <capture.csv> <base_from> <base_to> <evt_from> <evt_to>

Times are HH:MM or HH:MM:SS in the capture's local offset. Pick a baseline in the
SAME vehicle state as the event (e.g. both parked+charging) -- otherwise drive-vs-park
differences swamp the signal. Example:
    python -m tools.event_diff captures/drive.csv 18:54 18:59 19:00 19:03
"""
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta


def _ms(start_dt, hhmm):
    parts = [int(x) for x in hhmm.split(":")]
    while len(parts) < 3:
        parts.append(0)
    t = start_dt.replace(hour=parts[0], minute=parts[1], second=parts[2], microsecond=0)
    if t < start_dt:                       # rolled past midnight
        t += timedelta(days=1)
    return int((t - start_dt).total_seconds() * 1000)


def event_diff(frames, base_ms, evt_ms):
    """Per (can_id, byte): which values appear during the event that NEVER appear
    in the baseline.

    This is deliberately byte-level rather than "static vs changing": most frames
    carry rolling counters/checksums that change constantly, so nothing is ever
    static. A counter cycles through the same value range in BOTH windows, so it
    contributes no new values and drops out automatically. A real state change
    (a window switch, a position sweep) introduces values the baseline never saw.

    The baseline must be the SAME vehicle state as the event (e.g. both parked +
    charging), or drive-vs-park differences will swamp the signal.
    """
    trans = {"base": defaultdict(lambda: defaultdict(int)),
             "evt": defaultdict(lambda: defaultdict(int))}
    last = {}
    for t, cid, d in frames:
        if base_ms[0] <= t <= base_ms[1]:
            w = "base"
        elif evt_ms[0] <= t <= evt_ms[1]:
            w = "evt"
        else:
            last[cid] = d
            continue
        prev = last.get(cid)
        if prev is not None:
            for i in range(min(len(prev), len(d))):
                if prev[i] != d[i]:
                    trans[w][cid][i] += 1
        last[cid] = d

    base_dur = max((base_ms[1] - base_ms[0]) / 1000.0, 1e-6)
    evt_dur = max((evt_ms[1] - evt_ms[0]) / 1000.0, 1e-6)
    out = []
    for cid, byte_counts in trans["evt"].items():
        for i, n_evt in byte_counts.items():
            n_base = trans["base"][cid].get(i, 0)
            r_evt, r_base = n_evt / evt_dur, n_base / base_dur
            # the signature we want: frozen while idle, moving while you act
            if r_evt >= 0.05 and r_base <= 0.02:
                out.append((round(r_evt, 3), cid, i, round(r_base, 4), n_evt, n_base))
    out.sort(reverse=True)
    return out


def _main(argv):
    from tscan.capture import parse_capture_file
    from tscan.core import Decoder
    cap, bf, bt, ef, et = argv[0], argv[1], argv[2], argv[3], argv[4]
    meta, frames = parse_capture_file(cap)
    if "start" not in meta:
        print("capture has no start= metadata; cannot map wall-clock times")
        return 2
    start_dt = datetime.fromisoformat(meta["start"])
    base_ms = (_ms(start_dt, bf), _ms(start_dt, bt))
    evt_ms = (_ms(start_dt, ef), _ms(start_dt, et))
    print(f"capture start : {start_dt}")
    print(f"baseline      : {bf}-{bt}  -> t_ms {base_ms[0]:,}..{base_ms[1]:,}")
    print(f"event         : {ef}-{et}  -> t_ms {evt_ms[0]:,}..{evt_ms[1]:,}\n")

    dec = Decoder("data/tesla_models.dbc")
    hits = event_diff(frames, base_ms, evt_ms)
    if not hits:
        print("No byte was frozen during the baseline yet active during the event.")
        return 0
    by_frame = defaultdict(list)
    for r_evt, cid, i, r_base, n_evt, n_base in hits:
        by_frame[cid].append((r_evt, i, r_base, n_evt, n_base))
    ranked = sorted(by_frame.items(), key=lambda kv: -max(x[0] for x in kv[1]))
    print(f"{len(hits)} (frame,byte) pair(s) FROZEN in baseline but ACTIVE during "
          f"the event, across {len(by_frame)} frame(s):\n")
    for cid, entries in ranked[:12]:
        name = dec.message_name(cid) or "?"
        print(f"  0x{cid:03X} {name}")
        for r_evt, i, r_base, n_evt, n_base in sorted(entries, reverse=True)[:6]:
            print(f"      byte{i}: {r_evt:6.2f} changes/s during event "
                  f"({n_evt} total)   vs {r_base:.3f}/s baseline ({n_base})")
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
