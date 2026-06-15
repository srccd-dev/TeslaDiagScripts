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


def _write_fixture(tmp_path, name, data):
    p = tmp_path / name
    p.write_text("# tesla_scan capture v1\n# bus=CAN3\nt_ms,can_id,data_hex\n"
                 f"0,219,{data.hex().upper()}\n", encoding="utf-8")
    return str(p)


def test_ingest_creates_rows(decoder, tmp_path):
    from tscan.trend import TrendStore
    store = TrendStore(str(tmp_path / "t.sqlite"))
    cid = store.ingest(decoder, _write_fixture(tmp_path, "c.csv", REAL_0219))
    cur = store.conn.execute("SELECT COUNT(*) FROM captures")
    assert cur.fetchone()[0] == 1
    cur = store.conn.execute(
        "SELECT v_last FROM signal_samples WHERE signal='BMS_isolationResistance' AND capture_id=?",
        (cid,))
    assert cur.fetchone()[0] == 0
    cur = store.conn.execute(
        "SELECT COUNT(*) FROM faults WHERE capture_id=? AND active=1", (cid,))
    assert cur.fetchone()[0] >= 1   # BMS_state=FAULT
    store.close()


def test_baseline_set_and_get(decoder, tmp_path):
    from tscan.trend import TrendStore
    store = TrendStore(str(tmp_path / "t.sqlite"))
    c1 = store.ingest(decoder, _write_fixture(tmp_path, "a.csv", REAL_0219))
    c2 = store.ingest(decoder, _write_fixture(tmp_path, "b.csv", REAL_0219))
    store.set_baseline(c1)
    assert store.baseline_id() == c1
    store.set_baseline(c2)        # moves baseline; only one at a time
    assert store.baseline_id() == c2
    store.close()
