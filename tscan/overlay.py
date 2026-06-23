"""Hand-authored correction overlay (data/overlay.json) over the base DBC.

We author definitions in JSON; cantools still does the bit extraction. Each
message entry may carry: length (true wire length), trust (faults|analog|
unknown), name, replace_signals, and signals (compact defs). See the design
spec for the schema and rationale.
"""
import json

from cantools.database.conversion import BaseConversion
from cantools.database.can.signal import Signal
from cantools.database.can.message import Message
from cantools.database.can.database import Database


def _build_signal(d):
    byte_order = "big_endian" if str(d.get("endian", "little")).startswith("big") \
        else "little_endian"
    choices = {int(k): v for k, v in d.get("values", {}).items()} or None
    conv = BaseConversion.factory(scale=d.get("scale", 1), offset=d.get("offset", 0),
                                  choices=choices)
    return Signal(name=d["name"], start=d["start"], length=d["length"],
                  byte_order=byte_order, is_signed=bool(d.get("signed", False)),
                  conversion=conv, unit=d.get("unit", ""))


class Overlay:
    def __init__(self, entries, db):
        self.entries = entries        # {frame_id:int -> entry dict}
        self.db = db                  # cantools Database of overlay messages w/ signals

    def entry(self, can_id):
        return self.entries.get(can_id)

    def trust(self, can_id):
        e = self.entries.get(can_id)
        return (e or {}).get("trust", "faults")


def load_overlay(path):
    """Load overlay.json -> Overlay. Missing/invalid file yields an empty overlay
    (so the suite works with no overlay present)."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        return Overlay({}, Database(messages=[], strict=False))
    entries, msgs = {}, []
    for hid, e in raw.get("messages", {}).items():
        fid = int(hid, 16)
        entries[fid] = e
        if e.get("signals"):
            sigs = [_build_signal(s) for s in e["signals"]]
            msgs.append(Message(frame_id=fid, name=e.get("name", f"OVL_{hid}"),
                                length=e.get("length", 8), signals=sigs, strict=False))
    return Overlay(entries, Database(messages=msgs, strict=False))


class DecodeEngine:
    """Compose the overlay over the base DBC decoder.

    For each frame: truncate padding beyond the overlay's declared true length,
    decode via the overlay (when replace_signals) or the base DBC (optionally
    merging overlay signals on top), and expose the trust level for `faults`.
    Drop-in for `Decoder` (same `.decode(can_id, data)`), plus `.trust(can_id)`.
    """

    def __init__(self, base, overlay):
        self.base = base              # tscan.core.Decoder
        self.overlay = overlay        # Overlay

    def trust(self, can_id):
        return self.overlay.trust(can_id)

    def _overlay_decode(self, can_id, data):
        try:
            return self.overlay.db.decode_message(
                can_id, data, decode_choices=True, allow_truncated=True)
        except Exception:
            return None

    def decode(self, can_id, data):
        e = self.overlay.entry(can_id)
        length = (e or {}).get("length")
        if length is not None:
            data = data[:length]      # ignore padding beyond the true wire length
        if e and e.get("signals") and e.get("replace_signals"):
            return self._overlay_decode(can_id, data)
        # base Decoder decodes with allow_truncated=True, which drops any signal
        # extending past the received bytes — the upper half of the length-guard.
        dec = self.base.decode(can_id, data)
        if e and e.get("signals"):    # merge overlay signals onto the base result
            merged = self._overlay_decode(can_id, data)
            if merged:
                dec = {**(dec or {}), **merged}
        return dec
