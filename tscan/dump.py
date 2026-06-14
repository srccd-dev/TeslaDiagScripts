"""Phase 2: decode every signal seen on the bus, grouped by module.

Unlike `faults`, this makes no fault/no-fault judgment — it shows decoded values
and lets the reader interpret. Latest frame per CAN ID wins.
"""
import re
from tscan.core import module_for


def dump_signals(decoder, frames, module=None, grep=None):
    """Return {module_label: [(signal_name, value), ...]} for all decoded signals.

    module: case-insensitive substring filter on the module label.
    grep:   case-insensitive regex filter on the signal name.
    """
    latest = {}
    for _t, can_id, data in frames:
        latest[can_id] = data

    pat = re.compile(grep, re.I) if grep else None
    grouped = {}
    for can_id, data in latest.items():
        dec = decoder.decode(can_id, data)
        if not dec:
            continue
        for sig, val in dec.items():
            if pat and not pat.search(sig):
                continue
            mod = module_for(sig)
            if module and module.lower() not in mod.lower():
                continue
            grouped.setdefault(mod, []).append((sig, val))

    for mod in grouped:
        grouped[mod].sort(key=lambda sv: sv[0])
    return grouped
