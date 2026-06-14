"""cantools-backed decode engine for Tesla CAN frames."""
import cantools

# message/signal name prefix -> human module label
MODULE_PREFIXES = {
    "BMS": "Battery Management", "DI": "Drive Inverter",
    "DIS": "Drive Inverter", "DIR": "Rear Drive Inverter",
    "DIF": "Front Drive Inverter", "GTW": "Gateway",
    "TAS": "Air Suspension", "CP": "Charge Port", "CHG": "Charger",
    "CHGS": "Charger", "UI": "User Interface (MCU)", "PCS": "Power Conversion",
    "VCFRONT": "Front Body Controller", "VCREAR": "Rear Body Controller",
    "EPAS": "Steering", "IBST": "iBooster Brake", "DAS": "Autopilot",
    "ESP": "Stability Control", "SCCM": "Steering Column",
    "PARK": "Park Assist", "BCFRONT": "Body Controller Front",
}


def module_for(name):
    """Map a signal/message name to a human module label via its prefix."""
    prefix = name.split("_", 1)[0]
    return MODULE_PREFIXES.get(prefix, f"Unknown ({prefix})")


class Decoder:
    def __init__(self, dbc_path):
        # strict=False: our DBC has at least one malformed message
        # (BCCEN_udsResponse) that fails strict validation but is irrelevant here.
        self.db = cantools.database.load_file(dbc_path, strict=False)
        self._ids = {m.frame_id for m in self.db.messages}

    def decode(self, can_id, data):
        """Return {signal_name: value_or_named} or None if the ID/decode is unknown."""
        if can_id not in self._ids:
            return None
        try:
            return self.db.decode_message(
                can_id, data, decode_choices=True, allow_truncated=True
            )
        except Exception:
            return None

    def message_name(self, can_id):
        try:
            return self.db.get_message_by_frame_id(can_id).name
        except KeyError:
            return None
