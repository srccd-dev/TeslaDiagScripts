"""Select active fault/alert codes from decoded frames."""
import re
from dataclasses import dataclass
from tscan.core import module_for
from tscan.meaning import describe

_CODE = re.compile(r"_([wfu])(\d{3})_")
_CLASS = {"w": "warning", "f": "fault", "u": "status"}

# state enums whose listed values count as an active fault
DEFAULT_STATE_WATCH = {
    "BMS_state": {"FAULT", "WELD"},
    "BMS_contactorState": {"WELD", "CLEANING"},
}

_FAULT_TOKENS = ("FAULT", "FAILED", "WELD", "ERROR")


def is_fault_value(named):
    """A decoded enum value indicates a fault when it contains a fault token but is
    not an SNA/negation. Conservative on purpose: FAULT_SNA and NO_FAULT are NOT
    faults, only e.g. FAULT / FAILED / WELD / SOPT_TEST_FAILED."""
    s = str(named).upper()
    if "SNA" in s or s.startswith("NO_") or "NOT_" in s:
        return False
    return any(tok in s for tok in _FAULT_TOKENS)


@dataclass
class Fault:
    code: str          # e.g. "f071" or "" for state-enum hits
    klass: str         # "fault" / "warning" / "status" / "state"
    module: str
    signal: str
    meaning: str
    can_id: int
    evidence: str      # hex of the frame that set it


def active_faults(decoder, frames, overrides=None, state_watch=None):
    """Return a de-duplicated list of Fault for every active coded signal or
    watched state-enum across the frames (latest frame per ID wins)."""
    state_watch = state_watch or DEFAULT_STATE_WATCH
    latest = {}
    for _t, can_id, data in frames:
        latest[can_id] = data

    out, seen = [], set()
    for can_id, data in latest.items():
        dec = decoder.decode(can_id, data)
        if not dec:
            continue
        evidence = data.hex().upper()
        for sig, val in dec.items():
            m = _CODE.search(sig)
            if m and _is_active(val):
                if sig in seen:
                    continue
                seen.add(sig)
                out.append(Fault(
                    code=f"{m.group(1)}{m.group(2)}", klass=_CLASS[m.group(1)],
                    module=module_for(sig), signal=sig,
                    meaning=describe(sig, overrides=overrides),
                    can_id=can_id, evidence=evidence,
                ))
            elif sig in state_watch and str(val) in state_watch[sig]:
                if sig in seen:
                    continue
                seen.add(sig)
                out.append(Fault(
                    code="", klass="state", module=module_for(sig), signal=sig,
                    meaning=describe(sig, named_value=val, overrides=overrides),
                    can_id=can_id, evidence=evidence,
                ))
    return out


def _is_active(val):
    """A coded fault/alert signal is active when its bit/value is non-zero."""
    try:
        return int(val) != 0
    except (TypeError, ValueError):
        return False
