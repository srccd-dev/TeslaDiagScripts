"""Human-readable meaning for a Tesla CAN signal/fault."""
import re

_CODE = re.compile(r"^[A-Z0-9]+_([wfu])(\d{3})_(.*)$")


def humanize(signal_name):
    """Strip the '<MODULE>_<class><nnn>_' prefix and de-underscore the rest."""
    m = _CODE.match(signal_name)
    suffix = m.group(3) if m else signal_name
    return suffix.replace("_", " ").strip()


def describe(signal_name, named_value=None, comment=None, overrides=None):
    """Priority: override file -> DBC comment -> enum named value -> humanized name."""
    overrides = overrides or {}
    if signal_name in overrides:
        return overrides[signal_name]
    if comment:
        return comment
    human = humanize(signal_name)
    if named_value is not None and not isinstance(named_value, (int, float)):
        return f"{human} = {named_value}"
    return human
