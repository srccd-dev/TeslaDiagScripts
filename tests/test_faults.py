from tscan.faults import active_faults


def test_state_watchlist_flags_bms_fault(decoder):
    # real captured 0x219 has BMS_state = FAULT
    frames = [(0, 0x219, bytes([0x00, 0x80, 0x7F, 0x00, 0x82, 0x02, 0x00, 0x04]))]
    faults = active_faults(decoder, frames)
    codes = {f.signal for f in faults}
    assert "BMS_state" in codes
    bms = next(f for f in faults if f.signal == "BMS_state")
    assert bms.module == "Battery Management"
    assert "FAULT" in bms.meaning


def test_coded_fault_bit_detected(decoder):
    # alertMatrix2 (0x021): BMS_f071_SW_SM_TransCon_Not_Met is bit 6 -> byte0 0x40
    data = bytes([0x40, 0, 0, 0, 0, 0, 0, 0])
    frames = [(0, 0x021, data)]
    faults = active_faults(decoder, frames)
    codes = {f.code for f in faults}
    assert "f071" in codes


def test_no_faults_when_clean(decoder):
    data = bytes([0x00, 0x00, 0x00, 0xFF, 0x00, 0x00, 0x00, 0x00])  # iso=255 SNA, state 0
    frames = [(0, 0x219, data)]
    faults = active_faults(decoder, frames)
    assert all(f.signal != "BMS_state" for f in faults)  # state 0 = STANDBY, not a fault
