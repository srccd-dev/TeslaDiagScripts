def test_decode_0219_real_frame(decoder):
    data = bytes([0x00, 0x80, 0x7F, 0x00, 0x82, 0x02, 0x00, 0x04])
    dec = decoder.decode(0x219, data)
    assert dec is not None
    assert str(dec["BMS_state"]) == "FAULT"
    assert dec["BMS_isolationResistance"] == 0


def test_decode_unknown_id_returns_none(decoder):
    assert decoder.decode(0x7FF, b"\x00") is None


def test_message_name(decoder):
    assert decoder.message_name(0x219) == "BMS_status"
