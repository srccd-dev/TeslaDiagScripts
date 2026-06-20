from tscan.faults import active_faults, is_fault_value


def test_is_fault_value_conservative():
    assert is_fault_value("FAULT")
    assert is_fault_value("FAILED")
    assert is_fault_value("WELD")
    assert is_fault_value("SOPT_TEST_FAILED")
    # SNA and negations are NOT faults
    assert not is_fault_value("FAULT_SNA")
    assert not is_fault_value("NO_FAULT")
    assert not is_fault_value("NOT_TESTED_DTC")
    assert not is_fault_value("PASSED_DTC")
    assert not is_fault_value("STANDBY")


from tscan.faults import classify


class _Named(str):
    """str subclass with a .name attribute, mimicking cantools NamedSignalValue."""
    @property
    def name(self):
        return str(self)


def test_classify_coded_classes():
    assert classify("BMS_f071_x", 1).klass == "fault"
    assert classify("BMS_f071_x", 1).severity == "CRITICAL"
    assert classify("BMS_w158_x", 1).klass == "warning"
    assert classify("BMS_w158_x", 1).severity == "WARNING"
    assert classify("X_a094_y", 1).severity == "WARNING"      # alert -> WARNING
    assert classify("X_u008_y", 1).severity == "STATUS"
    assert classify("BMS_f071_x", 0) is None                  # not active


def test_classify_selftest_is_state_aware():
    # THE BUG FIX: PASSED is good, FAILED is the fault
    assert classify("BMS_d002_x", _Named("PASSED_DTC")) is None
    assert classify("BMS_d002_x", _Named("NOT_TESTED_DTC")) is None
    c = classify("BMS_d002_x", _Named("FAILED_DTC"))
    assert c.klass == "selftest" and c.state == "FAILED_DTC" and c.severity == "CRITICAL"


def test_classify_enum_fault_path():
    assert classify("BMS_state", _Named("FAULT")).severity == "CRITICAL"
    assert classify("BMS_state", _Named("FAULT")).klass == "state"
    assert classify("BMS_state", _Named("STANDBY")) is None
    assert classify("DI_x", _Named("FAULT_SNA")) is None      # SNA excluded


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
