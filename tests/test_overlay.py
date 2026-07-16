import json
from tscan.overlay import load_overlay


def test_load_overlay_parses_entries_and_builds_db(tmp_path):
    p = tmp_path / "overlay.json"
    p.write_text(json.dumps({
        "version": 1,
        "messages": {
            "3F8": {
                "name": "DCDC_status", "length": 8, "trust": "analog",
                "replace_signals": True,
                "signals": [
                    {"name": "DCDC_rawWord0", "start": 0, "length": 16,
                     "endian": "little", "scale": 1, "offset": 0, "unit": ""}
                ],
            },
            "212": {"length": 5, "trust": "unknown"},
        },
    }), encoding="utf-8")

    ov = load_overlay(str(p))
    assert ov.entry(0x3F8)["replace_signals"] is True
    assert ov.entry(0x212)["length"] == 5
    assert ov.trust(0x3F8) == "analog"
    assert ov.trust(0x212) == "unknown"
    assert ov.trust(0x999) == "faults"            # untagged default
    # message with signals is decodable via the overlay db
    dec = ov.db.decode_message(0x3F8, bytes([0xFB, 0x02, 0, 0, 0, 0, 0, 0]),
                               decode_choices=True, allow_truncated=True)
    assert dec["DCDC_rawWord0"] == 0x02FB         # 763, little-endian


def test_load_overlay_missing_file_is_empty(tmp_path):
    ov = load_overlay(str(tmp_path / "nope.json"))
    assert ov.entry(0x219) is None
    assert ov.trust(0x219) == "faults"


import os
from tscan.core import Decoder
from tscan.overlay import DecodeEngine

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DBC = os.path.join(REPO, "data", "tesla_models.dbc")


def _engine_with(tmp_path, messages):
    import json
    p = tmp_path / "overlay.json"
    p.write_text(json.dumps({"version": 1, "messages": messages}), encoding="utf-8")
    return DecodeEngine(Decoder(DBC), load_overlay(str(p)))


def test_engine_replace_signals_overrides_dbc(tmp_path):
    eng = _engine_with(tmp_path, {
        "3F8": {"length": 8, "trust": "analog", "replace_signals": True,
                "signals": [{"name": "DCDC_rawWord0", "start": 0, "length": 16,
                             "endian": "little"}]},
    })
    dec = eng.decode(0x3F8, bytes([0xFB, 0x02, 0, 0, 0, 0, 0, 0]))
    assert dec == {"DCDC_rawWord0": 0x02FB}        # DBC's fake fault bits are gone
    assert eng.trust(0x3F8) == "analog"


def test_engine_truncates_padding_to_true_length(tmp_path):
    # overlay says true length 5; a padded 8-byte frame must not read bytes 5-7
    eng = _engine_with(tmp_path, {"212": {"length": 5, "trust": "unknown"}})
    full = eng.decode(0x212, bytes([0xD8, 0x09, 0x12, 0x1E, 0x00, 0xFF, 0xFF, 0xFF]))
    short = eng.decode(0x212, bytes([0xD8, 0x09, 0x12, 0x1E, 0x00]))
    assert full == short                            # padding ignored
    assert eng.trust(0x212) == "unknown"


def test_engine_falls_through_to_dbc_for_untouched_ids(tmp_path):
    eng = _engine_with(tmp_path, {})
    real_0219 = bytes([0x00, 0x80, 0x7F, 0x00, 0x82, 0x02, 0x00, 0x04])
    dec = eng.decode(0x219, real_0219)
    assert "BMS_isolationResistance" in dec
    assert eng.trust(0x219) == "faults"


from tscan.faults import active_faults
from tscan.dump import dump_signals

# top payload observed for 0x3F8 in the 2026-06-21 drive capture
REAL_3F8 = bytes([0xFB, 0x02, 0xFA, 0x02, 0xF7, 0x02, 0xFD, 0x02])


def test_overlay_batch_kills_dcdc_false_faults(engine):
    faults = active_faults(engine, [(0, 0x3F8, REAL_3F8)])
    assert faults == []                              # was 8 bogus DCDC "faults"


def test_overlay_batch_3f8_decodes_hvac_duct_temps(engine):
    grouped = dump_signals(engine, [(0, 0x3F8, REAL_3F8)])
    flat = {n: v for vals in grouped.values() for n, v in vals}
    assert "RCCM_LeftVentDuctSnsRaw_DegC" in flat
    # raw word 0x02FB=763 -> 763*0.1 - 40 = 36.3 degC (plausible duct temp)
    assert 30 <= flat["RCCM_LeftVentDuctSnsRaw_DegC"] <= 45
    assert {"RCCM_RightVentDuctSnsRaw_DegC", "RCCM_LeftFloorDuctSnsRaw_DegC",
            "RCCM_RightFloorDuctSnsRaw_DegC"} <= set(flat)


def test_overlay_batch_suppresses_light_frames_from_faults(engine):
    frames = [(0, 0x212, bytes([0xD8, 0x09, 0x12, 0xFF, 0x00])),
              (0, 0x232, bytes([0x6A, 0x27, 0xE9, 0x9C]))]
    assert active_faults(engine, frames) == []       # was many bogus light "FAULTs"


# --- Batch 2: 12 frames the community DBC mislabels as fault/alert matrices,
# corrected from PT.dbc (Amond/SMT) to their real analog identities ---
BATCH2 = [0x305, 0x378, 0x267, 0x368, 0x5D8, 0x391, 0x5B8, 0x328, 0x202, 0x392,
          0x358, 0x145]


def test_batch2_odometer_decodes(engine):
    # 0x5D8 is the odometer (MCU_odometerStatus), not GTW_faultMatrix4.
    dec = engine.decode(0x5D8, bytes.fromhex("58B33F07"))   # u32 LE * 0.001 mi
    assert abs(dec["MCU_odometer"] - 121615.192) < 0.01      # 0x073FB358 * 0.001


def test_batch2_bms_drive_limits_decode(engine):
    # 0x202 is BMS_driveLimits (bus V + current limits), not BCFDM_status.
    # minBusVoltage = bytes[0:2] LE 0x0161 = 353 * 0.01 = 3.53 V
    dec = engine.decode(0x202, bytes.fromhex("61016E9D00000000"))
    assert abs(dec["BMS_minBusVoltage"] - 3.53) < 1e-9       # 0x0161 * 0.01
    assert "BMS_maxChargeCurrent" in dec and "BMS_maxDischargeCurrent" in dec


def test_batch2_frames_all_analog_and_suppressed(engine):
    # every batch-2 frame is trust:analog -> never classified as a fault
    for cid in BATCH2:
        assert engine.trust(cid) == "analog"
    frames = [(0, cid, bytes(range(8))) for cid in BATCH2]
    assert active_faults(engine, frames) == []


# --- Noise frames: community DBC mislabels them as faults, but Toolbox CAN
# Explorer confirmed the vehicle reports no such alerts/DTCs. trust:unknown ->
# suppressed from faults (no correct definition available; PT.dbc lacks them) ---
NOISE = [0x314, 0x257, 0x31F, 0x2C8, 0x286]


def test_noise_frames_unknown_and_suppressed(engine):
    for cid in NOISE:
        assert engine.trust(cid) == "unknown"
    frames = [(0, cid, bytes(range(8))) for cid in NOISE]
    assert active_faults(engine, frames) == []


# --- 0x219: the community DBC calls it BMS_status (5B). On this bus it is really
# CHGPH2_status (8B, charger phase 2) per PT.dbc. Proof: the frame is absent across
# 30 min of pure driving (0 frames) but present 10,237x while supercharging - a BMS
# frame broadcasts constantly, a charger frame only when charging. The bad decode
# produced a bogus CRITICAL "BMS_state=FAULT" on every charge. ---
REAL_219 = bytes.fromhex("00C07F006F020000")        # captured mid-supercharge
FAULTY_219 = bytes.fromhex("00007F0082020000")      # the frame that faked BMS_state=FAULT


def test_0x219_decodes_as_charger_not_bms(engine):
    dec = engine.decode(0x219, REAL_219)
    assert "CHGPH2_mainState" in dec                 # real charger telemetry
    assert "BMS_state" not in dec                    # the mislabel is gone
    assert "BMS_isolationResistance" not in dec      # was a meaningless 0
    assert engine.trust(0x219) == "analog"


def test_0x219_no_longer_fakes_a_bms_fault(engine):
    # this exact frame used to emit "[CRITICAL] BMS_state = FAULT" during charging
    assert active_faults(engine, [(0, 0x219, FAULTY_219)]) == []
