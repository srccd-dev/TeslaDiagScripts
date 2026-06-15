import os
from tscan.trend import aggregate_signals
from tests.conftest import FIXTURES

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


def test_diff_detects_new_fault_state_change_and_drift(decoder, tmp_path):
    from tscan.trend import TrendStore
    store = TrendStore(str(tmp_path / "t.sqlite"))
    base = store.ingest(decoder, os.path.join(FIXTURES, "baseline_0219.csv"))
    targ = store.ingest(decoder, os.path.join(FIXTURES, "sample_0219.csv"))
    store.set_baseline(base)
    d = store.diff(targ)
    # BMS_state went STANDBY -> FAULT: a new fault and a state change
    assert any(f["signal"] == "BMS_state" for f in d["new_faults"])
    assert any(c["signal"] == "BMS_state" and c["from"] == "STANDBY"
               and c["to"] == "FAULT" for c in d["state_changes"])
    # isolationResistance 100 -> 0: a drift
    assert any(dr["signal"] == "BMS_isolationResistance" for dr in d["drifts"])
    store.close()


def test_history_and_threshold(decoder, tmp_path):
    from tscan.trend import TrendStore
    store = TrendStore(str(tmp_path / "t.sqlite"))
    store.ingest(decoder, _write_fixture(tmp_path, "h1.csv", REAL_0219))
    store.ingest(decoder, _write_fixture(tmp_path, "h2.csv", REAL_0219))
    hist = store.history("BMS_isolationResistance")
    assert len(hist) == 2
    assert hist[0]["v_last"] == 0
    store.set_threshold("BMS_isolationResistance", abs_delta=5.0, pct_delta=None)
    assert store._thresholds()["BMS_isolationResistance"] == (5.0, None)
    store.close()


def test_cli_trend_ingest_baseline_diff(tmp_path, capsys):
    import tesla_scan
    db = str(tmp_path / "cli.sqlite")
    tesla_scan.main(["trend", "--db", db, "ingest",
                     os.path.join(FIXTURES, "baseline_0219.csv")])
    tesla_scan.main(["trend", "--db", db, "baseline", "1"])
    tesla_scan.main(["trend", "--db", db, "ingest",
                     os.path.join(FIXTURES, "sample_0219.csv")])
    tesla_scan.main(["trend", "--db", db, "diff", "2"])
    out = capsys.readouterr().out
    assert "BMS_state" in out
