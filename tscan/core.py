"""cantools-backed decode engine for Tesla CAN frames."""
import cantools

# message/signal name prefix -> human module label.
# Curated and partial: labels are added only where the prefix's signals make the
# module's function clear. Unmapped prefixes fall back to "Unknown (PREFIX)"
# rather than guess. Corrections welcome.
MODULE_PREFIXES = {
    # HV powertrain / energy
    "BMS": "Battery Management", "DI": "Drive Inverter",
    "DIS": "Drive Inverter", "DIR": "Rear Drive Inverter",
    "DIF": "Front Drive Inverter", "PCS": "Power Conversion",
    "DCDC": "DC-DC Converter", "PTC": "PTC Heater",
    "CHG": "Charger", "CHGS": "Charger", "CHGPH1": "Charger Phase 1",
    "CHGPH2": "Charger Phase 2", "CHGPH3": "Charger Phase 3",
    "CP": "Charge Port",
    # thermal
    "THC": "Thermal Controller",
    # chassis / dynamics
    "TAS": "Air Suspension", "EPAS": "Steering", "EPAS3P": "Steering (EPAS3)",
    "SCCM": "Steering Column", "IBST": "iBooster Brake", "EPBM": "Electronic Park Brake",
    "ESP": "Stability Control",
    # ADAS / autopilot
    "DAS": "Autopilot", "APP": "Autopilot (cameras)", "APS": "Driver Assist (ADAS)",
    "PARK": "Park Assist",
    # body / interior / infotainment
    "GTW": "Gateway", "UI": "User Interface (MCU)", "MCU": "Media Control Unit",
    "IC": "Instrument Cluster", "TUNER": "Radio Tuner",
    "RCM": "Restraint Control (airbags)", "DDM": "Driver Door Module",
    "VCFRONT": "Front Body Controller", "VCREAR": "Rear Body Controller",
    "BCFRONT": "Body Controller Front", "BCREAR": "Body Controller Rear",
    "BCCEN": "Body Controller Center",
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
