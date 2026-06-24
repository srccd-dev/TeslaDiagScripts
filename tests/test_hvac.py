"""A/C-health derived metric (folded into dump). Uses a fake decoder so no real
DBC/vehicle data is needed."""
from tscan.hvac import ac_health, format_ac_health


class _FakeDec:
    """data(bytes) -> decoded dict, mimicking Decoder/DecodeEngine.decode()."""
    def __init__(self, mapping):
        self.m = mapping

    def decode(self, can_id, data):
        return self.m.get(data)


def _thc(**kw):
    return kw


def test_ac_health_none_when_signals_absent():
    dec = _FakeDec({b"\x01": {"SomethingElse": 1}})
    assert ac_health(dec, [(0, 0x100, b"\x01")]) is None


def test_ac_health_healthy_strong_cooling():
    dec = _FakeDec({
        b"\x01": _thc(THC_ambientTempFiltered=32.0, THC_compressorState="RUN",
                      THC_compressorPower=1.3, THC_cabinACCoolingPct=20,
                      THC_auxEvapTemp_DegC=-5.0),
        b"\x02": {"RCCM_LeftVentDuctSnsRaw_DegC": 6.0, "RCCM_RightVentDuctSnsRaw_DegC": 6.5},
        b"\x03": {"RCCM_LeftVentDuctSnsRaw_DegC": 4.0, "RCCM_RightVentDuctSnsRaw_DegC": 4.5},
    })
    frames = [(0, 0x100, b"\x01"), (1, 0x3F8, b"\x02"), (2, 0x3F8, b"\x03")]
    m = ac_health(dec, frames)
    assert m["ambient"] == 32.0
    assert m["coldest_vent"] == 4.0          # min across both vent frames
    assert m["compressor_running"] is True
    assert m["delta"] == 28.0                # 32 - 4
    assert m["verdict"] == "HEALTHY"


def test_ac_health_weak_when_little_cooling():
    dec = _FakeDec({
        b"\x01": _thc(THC_ambientTempFiltered=32.0, THC_compressorState="RUN"),
        b"\x02": {"RCCM_LeftVentDuctSnsRaw_DegC": 27.0, "RCCM_RightVentDuctSnsRaw_DegC": 28.0},
    })
    m = ac_health(dec, [(0, 0x100, b"\x01"), (1, 0x3F8, b"\x02")])
    assert m["delta"] == 5.0                 # 32 - 27
    assert m["verdict"] == "WEAK"


def test_ac_health_marginal_band():
    dec = _FakeDec({
        b"\x01": _thc(THC_ambientTempFiltered=30.0, THC_compressorActive=1),
        b"\x02": {"RCCM_LeftVentDuctSnsRaw_DegC": 18.0, "RCCM_RightVentDuctSnsRaw_DegC": 19.0},
    })
    m = ac_health(dec, [(0, 0x100, b"\x01"), (1, 0x3F8, b"\x02")])
    assert m["delta"] == 12.0                # 30 - 18, in [8,18)
    assert m["verdict"] == "MARGINAL"


def test_ac_health_off_not_assessed():
    dec = _FakeDec({
        b"\x01": _thc(THC_ambientTempFiltered=32.0, THC_compressorState="OFF"),
        b"\x02": {"RCCM_LeftVentDuctSnsRaw_DegC": 31.0, "RCCM_RightVentDuctSnsRaw_DegC": 31.5},
    })
    m = ac_health(dec, [(0, 0x100, b"\x01"), (1, 0x3F8, b"\x02")])
    assert m["compressor_running"] is False
    assert m["verdict"] == "A/C OFF"
    assert m["delta"] is None                # not assessed when off


def test_format_ac_health_renders_block():
    m = {"ambient": 31.5, "compressor_running": True, "compressor_state": "RUN",
         "compressor_power": 1.3, "cooling_pct": 20, "vent_l": 5.0, "vent_r": 5.7,
         "vent_now": 5.0, "coldest_vent": 4.0, "evaporator": -5.0, "delta": 27.5,
         "verdict": "HEALTHY", "verdict_note": "strong cooling"}
    s = format_ac_health(m)
    assert "A/C Health" in s
    assert "HEALTHY" in s
    assert "31.5" in s and "27.5" in s
    assert "heuristic" in s.lower()          # honesty footnote present
