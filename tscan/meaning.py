"""Human-readable meaning for a Tesla CAN signal/fault."""
import re
import urllib.parse

_CODE = re.compile(r"^[A-Z0-9]+_([wfu])(\d{3})_(.*)$")
_STEM = re.compile(r"^([A-Za-z0-9]+_[wfu]\d{3})")


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


def tessie_link(signal_name):
    """A polite link-out for authoritative descriptions: a web search scoped to
    Tessie's alert directory for the code's stable stem (e.g. BMS_f027). The stem
    resolves even when our DBC suffix differs from Tessie's
    (BMS_f027_Unused_27 vs BMS_f027_SW_Drive_Iso_Repeated). We link, never scrape."""
    m = _STEM.match(signal_name)
    stem = m.group(1) if m else signal_name
    q = urllib.parse.quote(f"site:stats.tessie.com {stem}")
    return f"https://www.google.com/search?q={q}"
