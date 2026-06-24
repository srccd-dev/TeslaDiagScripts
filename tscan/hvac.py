"""Derived A/C-health assessment, folded into `dump`.

Reads the cabin-HVAC + ambient signals (including the RCCM vent-duct temps the
overlay unlocked) and reports cooling performance with a heuristic verdict. The
physical numbers (ambient, vent temps, delta, evaporator) are exact; the verdict
thresholds are heuristic — there is no fleet baseline yet, so the verdict is a
guide, not a diagnosis.
"""

_AMBIENT = "THC_ambientTempFiltered"
_COMP_STATE = "THC_compressorState"
_COMP_ACTIVE = "THC_compressorActive"
_COMP_POWER = "THC_compressorPower"
_COOL_PCT = "THC_cabinACCoolingPct"
_EVAP = "THC_auxEvapTemp_DegC"
_VENT_L = "RCCM_LeftVentDuctSnsRaw_DegC"
_VENT_R = "RCCM_RightVentDuctSnsRaw_DegC"

_NEEDED = (_AMBIENT, _COMP_STATE, _COMP_ACTIVE, _COMP_POWER, _COOL_PCT, _EVAP,
           _VENT_L, _VENT_R)

# heuristic ambient->vent cooling-delta thresholds (deg C)
_HEALTHY_DELTA = 18.0
_MARGINAL_DELTA = 8.0
# below this much cooling, with no compressor signal, treat A/C as not engaged
_ENGAGED_DELTA = 5.0


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def ac_health(decoder, frames):
    """Return derived A/C-health metrics, or None if the required HVAC signals
    (ambient + at least one vent-duct temp) aren't present in the capture.

    `decoder` is any object with `.decode(can_id, data) -> dict | None`."""
    latest_data = {}
    for _t, can_id, data in frames:
        latest_data[can_id] = data

    latest, vent_ids = {}, set()
    for can_id, data in latest_data.items():
        dec = decoder.decode(can_id, data)
        if not dec:
            continue
        for k in _NEEDED:
            if k in dec:
                latest[k] = dec[k]
        if _VENT_L in dec or _VENT_R in dec:
            vent_ids.add(can_id)

    ambient = _num(latest.get(_AMBIENT))
    if ambient is None or not vent_ids:
        return None

    # coldest vent achieved over the whole capture (demonstrated cooling capacity)
    coldest = None
    for _t, can_id, data in frames:
        if can_id not in vent_ids:
            continue
        dec = decoder.decode(can_id, data)
        if not dec:
            continue
        for k in (_VENT_L, _VENT_R):
            v = _num(dec.get(k))
            if v is not None and (coldest is None or v < coldest):
                coldest = v
    if coldest is None:
        return None

    vent_l, vent_r = _num(latest.get(_VENT_L)), _num(latest.get(_VENT_R))
    vent_now = min(v for v in (vent_l, vent_r) if v is not None)

    have_comp = _COMP_STATE in latest or _COMP_ACTIVE in latest
    running = (str(latest.get(_COMP_STATE)).upper() == "RUN"
               or latest.get(_COMP_ACTIVE) in (1, True))
    engaged = running or (not have_comp and (ambient - coldest) >= _ENGAGED_DELTA)

    if engaged:
        delta = round(ambient - coldest, 1)
        if delta >= _HEALTHY_DELTA:
            verdict, note = "HEALTHY", "strong cooling"
        elif delta >= _MARGINAL_DELTA:
            verdict, note = "MARGINAL", "reduced cooling - worth watching"
        else:
            verdict, note = "WEAK", ("possible low refrigerant / failing compressor "
                                     "/ blocked condenser")
    else:
        delta = None
        verdict, note = "A/C OFF", "compressor not running - not assessed"

    return {
        "ambient": ambient,
        "compressor_running": bool(running),
        "compressor_state": latest.get(_COMP_STATE),
        "compressor_power": _num(latest.get(_COMP_POWER)),
        "cooling_pct": _num(latest.get(_COOL_PCT)),
        "vent_l": vent_l, "vent_r": vent_r, "vent_now": vent_now,
        "coldest_vent": coldest, "evaporator": _num(latest.get(_EVAP)),
        "delta": delta, "verdict": verdict, "verdict_note": note,
    }


def _t(x):
    """Format a temperature to 1 decimal, or '-' if missing."""
    return f"{x:.1f}" if isinstance(x, (int, float)) and not isinstance(x, bool) else "-"


def format_ac_health(m):
    """Render the metrics dict (from ac_health) as a text block for `dump`."""
    lines = ["=== A/C Health (derived) ==="]
    lines.append(f"  Ambient (THC_ambientTempFiltered) : {_t(m['ambient'])} C")
    comp = m["compressor_state"] if m["compressor_state"] is not None else (
        "running" if m["compressor_running"] else "off")
    extra = []
    if m["compressor_power"] is not None:
        extra.append(f"{m['compressor_power']:.1f} kW")
    if m["cooling_pct"] is not None:
        extra.append(f"cabin cooling {m['cooling_pct']:.0f}%")
    suffix = f"  ({', '.join(extra)})" if extra else ""
    lines.append(f"  Compressor                        : {comp}{suffix}")
    lines.append(f"  Vent output L/R                   : {_t(m['vent_l'])} / {_t(m['vent_r'])} C"
                 f"   (coldest this capture: {_t(m['coldest_vent'])} C)")
    if m["evaporator"] is not None:
        lines.append(f"  Evaporator                        : {_t(m['evaporator'])} C")
    if m["delta"] is not None:
        lines.append(f"  Cooling delta (ambient - vent)    : {_t(m['delta'])} C")
    lines.append(f"  Verdict                           : {m['verdict']} - {m['verdict_note']}")
    lines.append("  (verdict thresholds are heuristic; delta + evaporator are the "
                 "hard truth)")
    return "\n".join(lines)
