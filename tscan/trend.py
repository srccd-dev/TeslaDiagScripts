"""Phase 3: SQLite trend store + capture diffing."""
from tscan.core import module_for


def aggregate_signals(decoder, frames):
    """Decode every frame and roll each signal up across the capture.
    Returns {signal: {module, unit, v_min, v_max, v_last, named_state, n}}."""
    units = {s.name: (s.unit or "")
             for m in decoder.db.messages for s in m.signals}
    agg = {}
    for _t, can_id, data in frames:
        dec = decoder.decode(can_id, data)
        if not dec:
            continue
        for sig, val in dec.items():
            if hasattr(val, "name") and hasattr(val, "value"):   # enum
                num, named = float(val.value), str(val)
            elif isinstance(val, (int, float)):
                num, named = float(val), None
            else:
                continue   # strings/bytes: not aggregated numerically
            a = agg.get(sig)
            if a is None:
                agg[sig] = {"module": module_for(sig), "unit": units.get(sig, ""),
                            "v_min": num, "v_max": num, "v_last": num,
                            "named_state": named, "n": 1}
            else:
                a["v_min"] = min(a["v_min"], num)
                a["v_max"] = max(a["v_max"], num)
                a["v_last"] = num
                a["named_state"] = named
                a["n"] += 1
    return agg
