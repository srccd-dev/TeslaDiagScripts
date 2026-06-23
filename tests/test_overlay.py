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
