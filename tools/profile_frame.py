"""Characterize a CAN id's on-wire structure vs the DBC: length distribution,
distinct payloads, per-byte value variance, and DBC length/mux expectations.

Usage:
    python tools/profile_frame.py <capture.csv> <hexid> [<hexid> ...]
"""
import sys
from collections import Counter


def profile(frames, can_id, decoder):
    """frames: list[(t_ms, can_id, data)]. Returns a dict of structural stats."""
    payloads = [d for _t, cid, d in frames if cid == can_id]
    out = {"can_id": can_id, "count": len(payloads), "lengths": {},
           "distinct_payloads": 0, "byte_variance": [],
           "dbc_name": None, "dbc_length": None, "dbc_multiplexed": None}
    try:
        msg = decoder.db.get_message_by_frame_id(can_id)
        out["dbc_name"] = msg.name
        out["dbc_length"] = msg.length
        out["dbc_multiplexed"] = msg.is_multiplexed()
    except (KeyError, AttributeError):
        pass
    if not payloads:
        return out
    out["lengths"] = dict(Counter(len(p) for p in payloads))
    out["distinct_payloads"] = len(Counter(p.hex().upper() for p in payloads))
    maxlen = max(len(p) for p in payloads)
    out["byte_variance"] = [len(Counter(p[i] for p in payloads if len(p) > i))
                            for i in range(maxlen)]
    return out


def _main(argv):
    from tscan.core import Decoder
    from tscan.capture import parse_capture_file
    import os
    if len(argv) < 2:
        print(__doc__)
        return 2
    cap, ids = argv[0], [int(x, 16) for x in argv[1:]]
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    dec = Decoder(os.path.join(repo, "data", "tesla_models.dbc"))
    _meta, frames = parse_capture_file(cap)
    for cid in ids:
        r = profile(frames, cid, dec)
        print(f"\n0x{cid:03X}  dbc={r['dbc_name']} dbc_len={r['dbc_length']} "
              f"muxed={r['dbc_multiplexed']}")
        print(f"  count={r['count']} lengths={r['lengths']} "
              f"distinct={r['distinct_payloads']}")
        print(f"  byte-variance={r['byte_variance']}")
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
