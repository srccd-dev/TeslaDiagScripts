from tscan.dump import dump_signals

REAL_0219 = bytes([0x00, 0x80, 0x7F, 0x00, 0x82, 0x02, 0x00, 0x04])


def test_dump_groups_by_module(decoder):
    grouped = dump_signals(decoder, [(0, 0x219, REAL_0219)])
    assert "Battery Management" in grouped
    sigs = {name for name, _v in grouped["Battery Management"]}
    assert "BMS_isolationResistance" in sigs


def test_dump_module_filter_excludes_nonmatch(decoder):
    grouped = dump_signals(decoder, [(0, 0x219, REAL_0219)], module="Drive")
    assert grouped == {}


def test_dump_grep_filter(decoder):
    grouped = dump_signals(decoder, [(0, 0x219, REAL_0219)], grep="isolation")
    allsigs = [n for v in grouped.values() for n, _ in v]
    assert allsigs and all("isolation" in s.lower() for s in allsigs)
