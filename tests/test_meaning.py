from tscan.meaning import describe, humanize, tessie_link


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


def test_tessie_link_uses_code_stem():
    # DBC suffix differs from Tessie's; the stable stem (BMS_f027) is what we search
    url = tessie_link("BMS_f027_SW_Drive_Iso")
    assert "stats.tessie.com" in url
    assert "BMS_f027" in url


def test_tessie_link_handles_non_coded_signal():
    url = tessie_link("BMS_state")
    assert "stats.tessie.com" in url
    assert "BMS_state" in url
