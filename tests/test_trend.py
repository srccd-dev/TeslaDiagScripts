from tscan.trend import aggregate_signals

REAL_0219 = bytes([0x00, 0x80, 0x7F, 0x00, 0x82, 0x02, 0x00, 0x04])  # FAULT, iso=0


def test_aggregate_numeric_and_enum(decoder):
    frames = [(0, 0x219, REAL_0219), (30, 0x219, REAL_0219)]
    agg = aggregate_signals(decoder, frames)
    assert agg["BMS_isolationResistance"]["v_last"] == 0
    assert agg["BMS_isolationResistance"]["n"] == 2
    assert agg["BMS_isolationResistance"]["named_state"] is None
    assert agg["BMS_state"]["named_state"] == "FAULT"
    assert agg["BMS_state"]["module"] == "Battery Management"
