from tscan.meaning import describe, humanize


def test_humanize_strips_code_prefix():
    assert humanize("BMS_w142_SW_Isolation_Degradatio") == "SW Isolation Degradatio"


def test_describe_prefers_override():
    overrides = {"BMS_f027_SW_Drive_Iso": "Drive unit isolation fault"}
    out = describe("BMS_f027_SW_Drive_Iso", named_value=None,
                   comment=None, overrides=overrides)
    assert out == "Drive unit isolation fault"


def test_describe_falls_back_to_humanized_suffix():
    out = describe("BMS_w142_SW_Isolation_Degradatio", named_value=None,
                   comment=None, overrides={})
    assert "Isolation Degradatio" in out


def test_describe_uses_named_value_for_enum():
    out = describe("BMS_state", named_value="FAULT", comment=None, overrides={})
    assert "FAULT" in out
