# Tesla Deep-Scan Suite — Phase 3 `trend` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Store captures in SQLite and diff a capture against a baseline to flag new faults, enum state changes, and numeric signal drift over time.

**Architecture:** A `tscan/trend.py` module with a pure `aggregate_signals()` (per-signal min/max/last/state from a capture's frames) and a `TrendStore` class wrapping a SQLite DB (schema per spec §6). The `trend` CLI subcommand (ingest / baseline / diff / history) drives it. Reuses the existing decode core and `active_faults`. All unit-tested against fixtures — no car required.

**Tech Stack:** Python 3, `sqlite3` (stdlib), `cantools` (via existing `Decoder`), `pytest`.

**Spec:** `docs/superpowers/specs/2026-05-28-tesla-deep-scan-suite-design.md` (§4.5 trend, §6 schema).

---

## File Structure

- `tscan/trend.py` — `aggregate_signals()`, `is_drift()`, `TrendStore` (schema, ingest, baseline, diff, history, thresholds).
- `tesla_scan.py` — add the `trend` subcommand (ingest / baseline / diff / history).
- `tests/fixtures/baseline_0219.csv` — a "healthy-ish" `0x219` (state STANDBY, iso 100) to diff against the existing FAULT fixture.
- `tests/test_trend.py` — unit tests for aggregation, store, diff, history.

`*.sqlite` is already git-ignored (Task 0 of Phase 0/1).

---

## Task 1: Per-signal aggregation

`aggregate_signals` decodes every frame and rolls each signal up to (module, unit,
v_min, v_max, v_last, named_state, n). Pure function, no DB.

**Files:**
- Create: `tscan/trend.py`
- Test: `tests/test_trend.py`

- [ ] **Step 1: Write the failing test**

`tests/test_trend.py`:
```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /d/AI/Projects/TeslaDiagScripts && python -m pytest tests/test_trend.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tscan.trend'`.

- [ ] **Step 3: Write minimal implementation**

`tscan/trend.py`:
```python
"""Phase 3: SQLite trend store + capture diffing."""
from tscan.core import module_for


def aggregate_signals(decoder, frames):
    """Decode every frame and roll each signal up across the capture.
    Returns {signal: {module, unit, v_min, v_max, v_last, named_state, n}}."""
    units = {s.name: (s.unit or "")
             for m in decoder.db.messages for s in m.signals}
    agg = {}
    for _t, can_id, data in frames:
        dec = decoder.decode(can_id, data)
        if not dec:
            continue
        for sig, val in dec.items():
            if hasattr(val, "name") and hasattr(val, "value"):   # enum
                num, named = float(val.value), str(val)
            elif isinstance(val, (int, float)):
                num, named = float(val), None
            else:
                continue   # strings/bytes: not aggregated numerically
            a = agg.get(sig)
            if a is None:
                agg[sig] = {"module": module_for(sig), "unit": units.get(sig, ""),
                            "v_min": num, "v_max": num, "v_last": num,
                            "named_state": named, "n": 1}
            else:
                a["v_min"] = min(a["v_min"], num)
                a["v_max"] = max(a["v_max"], num)
                a["v_last"] = num
                a["named_state"] = named
                a["n"] += 1
    return agg
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /d/AI/Projects/TeslaDiagScripts && python -m pytest tests/test_trend.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tscan/trend.py tests/test_trend.py
git commit -m "feat: per-signal capture aggregation for trend"
```

---

## Task 2: TrendStore schema + ingest

**Files:**
- Modify: `tscan/trend.py`
- Test: `tests/test_trend.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_trend.py`:
```python
from tscan.trend import TrendStore


def test_ingest_creates_rows(decoder, tmp_path):
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


def _write_fixture(tmp_path, name, data):
    p = tmp_path / name
    p.write_text("# tesla_scan capture v1\n# bus=CAN3\nt_ms,can_id,data_hex\n"
                 f"0,219,{data.hex().upper()}\n", encoding="utf-8")
    return str(p)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /d/AI/Projects/TeslaDiagScripts && python -m pytest tests/test_trend.py::test_ingest_creates_rows -v`
Expected: FAIL — `ImportError: cannot import name 'TrendStore'`.

- [ ] **Step 3: Write minimal implementation**

Add to `tscan/trend.py` (top: add imports; bottom: add class):
```python
import sqlite3
from tscan.capture import parse_capture_file
from tscan.faults import active_faults

_SCHEMA = """
CREATE TABLE IF NOT EXISTS captures (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at TEXT, file TEXT, adapter TEXT, bus TEXT, notes TEXT,
  is_baseline INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS signal_samples (
  capture_id INTEGER REFERENCES captures(id),
  signal TEXT, module TEXT, unit TEXT,
  v_min REAL, v_max REAL, v_last REAL, named_state TEXT, n INTEGER
);
CREATE TABLE IF NOT EXISTS faults (
  capture_id INTEGER REFERENCES captures(id),
  code TEXT, module TEXT, signal TEXT, meaning TEXT, active INTEGER
);
CREATE TABLE IF NOT EXISTS drift_thresholds (
  signal TEXT PRIMARY KEY, abs_delta REAL, pct_delta REAL
);
CREATE INDEX IF NOT EXISTS idx_samples_signal ON signal_samples(signal);
"""


class TrendStore:
    def __init__(self, db_path):
        self.conn = sqlite3.connect(db_path)
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    def ingest(self, decoder, capture_path, overrides=None, notes=None):
        meta, frames = parse_capture_file(capture_path)
        agg = aggregate_signals(decoder, frames)
        cur = self.conn.execute(
            "INSERT INTO captures (started_at, file, adapter, bus, notes) "
            "VALUES (?,?,?,?,?)",
            (meta.get("start"), capture_path, meta.get("adapter"),
             meta.get("bus"), notes))
        cid = cur.lastrowid
        self.conn.executemany(
            "INSERT INTO signal_samples "
            "(capture_id, signal, module, unit, v_min, v_max, v_last, named_state, n) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            [(cid, s, a["module"], a["unit"], a["v_min"], a["v_max"],
              a["v_last"], a["named_state"], a["n"]) for s, a in agg.items()])
        faults = active_faults(decoder, frames, overrides=overrides)
        self.conn.executemany(
            "INSERT INTO faults (capture_id, code, module, signal, meaning, active) "
            "VALUES (?,?,?,?,?,1)",
            [(cid, f.code, f.module, f.signal, f.meaning) for f in faults])
        self.conn.commit()
        return cid

    def close(self):
        self.conn.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /d/AI/Projects/TeslaDiagScripts && python -m pytest tests/test_trend.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add tscan/trend.py tests/test_trend.py
git commit -m "feat: TrendStore SQLite schema + capture ingest"
```

---

## Task 3: Baseline get/set

**Files:**
- Modify: `tscan/trend.py`
- Test: `tests/test_trend.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_trend.py`:
```python
def test_baseline_set_and_get(decoder, tmp_path):
    store = TrendStore(str(tmp_path / "t.sqlite"))
    c1 = store.ingest(decoder, _write_fixture(tmp_path, "a.csv", REAL_0219))
    c2 = store.ingest(decoder, _write_fixture(tmp_path, "b.csv", REAL_0219))
    store.set_baseline(c1)
    assert store.baseline_id() == c1
    store.set_baseline(c2)        # moves baseline; only one at a time
    assert store.baseline_id() == c2
    store.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /d/AI/Projects/TeslaDiagScripts && python -m pytest tests/test_trend.py::test_baseline_set_and_get -v`
Expected: FAIL — `AttributeError: 'TrendStore' object has no attribute 'set_baseline'`.

- [ ] **Step 3: Write minimal implementation**

Add these methods to `TrendStore` (before `close`):
```python
    def set_baseline(self, capture_id):
        self.conn.execute("UPDATE captures SET is_baseline=0")
        self.conn.execute("UPDATE captures SET is_baseline=1 WHERE id=?", (capture_id,))
        self.conn.commit()

    def baseline_id(self):
        cur = self.conn.execute("SELECT id FROM captures WHERE is_baseline=1 LIMIT 1")
        row = cur.fetchone()
        return row[0] if row else None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /d/AI/Projects/TeslaDiagScripts && python -m pytest tests/test_trend.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add tscan/trend.py tests/test_trend.py
git commit -m "feat: trend baseline set/get (one baseline at a time)"
```

---

## Task 4: Diff (new faults, state changes, drift)

**Files:**
- Modify: `tscan/trend.py`
- Create: `tests/fixtures/baseline_0219.csv`
- Test: `tests/test_trend.py`

- [ ] **Step 1: Write the baseline fixture**

`tests/fixtures/baseline_0219.csv` (state STANDBY, contactor CLOSED, iso 100 kΩ —
byte2=0x02 → state 0/contactor 2; byte3=0x05 → 5×20=100):
```
# tesla_scan capture v1
# bus=CAN3
t_ms,can_id,data_hex
0,219,00800205820200​04
```
(Note: data is `0080020582020004` — write it with no spaces.)

- [ ] **Step 2: Write the failing test**

Add to `tests/test_trend.py`:
```python
import os
from tests.conftest import FIXTURES


def test_diff_detects_new_fault_state_change_and_drift(decoder, tmp_path):
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
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd /d/AI/Projects/TeslaDiagScripts && python -m pytest tests/test_trend.py::test_diff_detects_new_fault_state_change_and_drift -v`
Expected: FAIL — `AttributeError: 'TrendStore' object has no attribute 'diff'`.

- [ ] **Step 4: Write minimal implementation**

Add module-level defaults + helper near the top of `tscan/trend.py` (after imports):
```python
DEFAULT_PCT_DELTA = 20.0   # flag numeric drift > 20% by default
DEFAULT_ABS_DELTA = None


def is_drift(base_last, targ_last, abs_delta, pct_delta):
    d = abs(targ_last - base_last)
    if abs_delta is not None and d >= abs_delta:
        return True
    if pct_delta is not None and base_last != 0 and (d / abs(base_last)) * 100 >= pct_delta:
        return True
    # near-zero baseline: any change of >= 1 unit counts (avoids div-by-zero blind spot)
    if base_last == 0 and d >= 1:
        return True
    return False
```

Add the `diff` method to `TrendStore` (before `close`):
```python
    def _samples(self, capture_id):
        cur = self.conn.execute(
            "SELECT signal, v_last, named_state FROM signal_samples WHERE capture_id=?",
            (capture_id,))
        return {r[0]: {"v_last": r[1], "named_state": r[2]} for r in cur.fetchall()}

    def _active_fault_signals(self, capture_id):
        cur = self.conn.execute(
            "SELECT signal FROM faults WHERE capture_id=? AND active=1", (capture_id,))
        return {r[0] for r in cur.fetchall()}

    def _thresholds(self):
        cur = self.conn.execute(
            "SELECT signal, abs_delta, pct_delta FROM drift_thresholds")
        return {r[0]: (r[1], r[2]) for r in cur.fetchall()}

    def diff(self, capture_id, baseline_id=None):
        base_id = baseline_id if baseline_id is not None else self.baseline_id()
        if base_id is None:
            raise ValueError("no baseline set; call set_baseline() first")
        base, targ = self._samples(base_id), self._samples(capture_id)
        base_faults = self._active_fault_signals(base_id)
        thr = self._thresholds()

        new_faults = [{"signal": s} for s in (
            self._active_fault_signals(capture_id) - base_faults)]

        state_changes, drifts = [], []
        for sig, t in targ.items():
            b = base.get(sig)
            if b is None:
                continue
            if t["named_state"] is not None or b["named_state"] is not None:
                if t["named_state"] != b["named_state"]:
                    state_changes.append(
                        {"signal": sig, "from": b["named_state"], "to": t["named_state"]})
            else:
                a_d, p_d = thr.get(sig, (DEFAULT_ABS_DELTA, DEFAULT_PCT_DELTA))
                if is_drift(b["v_last"], t["v_last"], a_d, p_d):
                    drifts.append({"signal": sig, "from": b["v_last"], "to": t["v_last"]})
        return {"new_faults": new_faults, "state_changes": state_changes, "drifts": drifts}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd /d/AI/Projects/TeslaDiagScripts && python -m pytest tests/test_trend.py -v`
Expected: PASS (4 tests). If the baseline fixture's bytes decode unexpectedly, verify with:
`python -c "from tscan.core import Decoder; d=Decoder('data/tesla_models.dbc'); print(d.decode(0x219, bytes.fromhex('0080020582020004')))"`
and confirm `BMS_state=STANDBY`, `BMS_isolationResistance=100`.

- [ ] **Step 6: Commit**

```bash
git add tscan/trend.py tests/test_trend.py tests/fixtures/baseline_0219.csv
git commit -m "feat: trend diff - new faults, state changes, numeric drift"
```

---

## Task 5: History + drift thresholds

**Files:**
- Modify: `tscan/trend.py`
- Test: `tests/test_trend.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_trend.py`:
```python
def test_history_and_threshold(decoder, tmp_path):
    store = TrendStore(str(tmp_path / "t.sqlite"))
    store.ingest(decoder, _write_fixture(tmp_path, "h1.csv", REAL_0219))
    store.ingest(decoder, _write_fixture(tmp_path, "h2.csv", REAL_0219))
    hist = store.history("BMS_isolationResistance")
    assert len(hist) == 2
    assert hist[0]["v_last"] == 0
    store.set_threshold("BMS_isolationResistance", abs_delta=5.0, pct_delta=None)
    assert store._thresholds()["BMS_isolationResistance"] == (5.0, None)
    store.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /d/AI/Projects/TeslaDiagScripts && python -m pytest tests/test_trend.py::test_history_and_threshold -v`
Expected: FAIL — `AttributeError: 'TrendStore' object has no attribute 'history'`.

- [ ] **Step 3: Write minimal implementation**

Add to `TrendStore` (before `close`):
```python
    def history(self, signal):
        cur = self.conn.execute(
            "SELECT s.capture_id, c.started_at, s.v_last, s.named_state "
            "FROM signal_samples s JOIN captures c ON c.id=s.capture_id "
            "WHERE s.signal=? ORDER BY s.capture_id", (signal,))
        return [{"capture_id": r[0], "started_at": r[1],
                 "v_last": r[2], "named_state": r[3]} for r in cur.fetchall()]

    def set_threshold(self, signal, abs_delta=None, pct_delta=None):
        self.conn.execute(
            "INSERT INTO drift_thresholds (signal, abs_delta, pct_delta) VALUES (?,?,?) "
            "ON CONFLICT(signal) DO UPDATE SET abs_delta=excluded.abs_delta, "
            "pct_delta=excluded.pct_delta", (signal, abs_delta, pct_delta))
        self.conn.commit()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /d/AI/Projects/TeslaDiagScripts && python -m pytest tests/test_trend.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add tscan/trend.py tests/test_trend.py
git commit -m "feat: trend signal history + per-signal drift thresholds"
```

---

## Task 6: `trend` CLI subcommand

**Files:**
- Modify: `tesla_scan.py`
- Test: `tests/test_trend.py`

- [ ] **Step 1: Write the failing test (CLI smoke via main())**

Add to `tests/test_trend.py`:
```python
import os
from tests.conftest import FIXTURES


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /d/AI/Projects/TeslaDiagScripts && python -m pytest tests/test_trend.py::test_cli_trend_ingest_baseline_diff -v`
Expected: FAIL — argparse error / no `trend` subcommand.

- [ ] **Step 3: Add the `trend` handlers + subparser to `tesla_scan.py`**

Add import near the other `tscan` imports:
```python
from tscan.trend import TrendStore
```

Add the handler functions (after `cmd_dump`):
```python
def cmd_trend(args):
    store = TrendStore(args.db)
    overrides = _load_overrides(args.descriptions)
    if args.action == "ingest":
        cid = store.ingest(Decoder(args.dbc), args.capture, overrides=overrides)
        print(f"Ingested as capture id {cid}")
    elif args.action == "baseline":
        store.set_baseline(args.capture_id)
        print(f"Baseline set to capture id {args.capture_id}")
    elif args.action == "diff":
        d = store.diff(args.capture_id)
        print(f"NEW FAULTS ({len(d['new_faults'])}):")
        for f in d["new_faults"]:
            print(f"  + {f['signal']}")
        print(f"STATE CHANGES ({len(d['state_changes'])}):")
        for c in d["state_changes"]:
            print(f"  ~ {c['signal']}: {c['from']} -> {c['to']}")
        print(f"DRIFTS ({len(d['drifts'])}):")
        for dr in d["drifts"]:
            print(f"  Δ {dr['signal']}: {dr['from']} -> {dr['to']}")
    elif args.action == "history":
        for h in store.history(args.signal):
            print(f"  cap {h['capture_id']} ({h['started_at']}): "
                  f"{h['v_last']} {h['named_state'] or ''}")
    store.close()
```

Add the subparser in `main()` (after the `dump` subparser, before `args = ap.parse_args`):
```python
    tr = sub.add_parser("trend", help="SQLite trend store: ingest/baseline/diff/history")
    tr.add_argument("--db", default="tesla_trend.sqlite")
    tr.add_argument("--dbc", default=DEFAULT_DBC)
    tr.add_argument("--descriptions", default=DEFAULT_OVERRIDES)
    tr.set_defaults(func=cmd_trend)
    tract = tr.add_subparsers(dest="action", required=True)
    p_ing = tract.add_parser("ingest"); p_ing.add_argument("capture")
    p_base = tract.add_parser("baseline"); p_base.add_argument("capture_id", type=int)
    p_diff = tract.add_parser("diff"); p_diff.add_argument("capture_id", type=int)
    p_hist = tract.add_parser("history"); p_hist.add_argument("signal")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /d/AI/Projects/TeslaDiagScripts && python -m pytest tests/test_trend.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Run the full suite**

Run: `cd /d/AI/Projects/TeslaDiagScripts && python -m pytest -q`
Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add tesla_scan.py tests/test_trend.py
git commit -m "feat: trend CLI subcommand (ingest/baseline/diff/history)"
```

---

## Task 7: README + spec status update

**Files:**
- Modify: `README.md`, `docs/superpowers/specs/2026-05-28-tesla-deep-scan-suite-design.md`

- [ ] **Step 1: Move `trend` from "planned" to implemented in README Status**

In `README.md`, delete the `trend` bullet under "Planned / not yet built" and add under the implemented list:
```markdown
- **`trend`** — store captures in SQLite and diff a capture against a baseline to
  flag new faults, enum state changes, and numeric drift over time
  (`ingest` / `baseline` / `diff` / `history`).
```

- [ ] **Step 2: Add `trend` usage to README Usage block**

Append inside the existing ```bash usage block:
```bash
# trend: ingest captures, set a baseline, diff a later capture against it
python tesla_scan.py trend ingest captures/run1.csv
python tesla_scan.py trend baseline 1
python tesla_scan.py trend ingest captures/run2.csv
python tesla_scan.py trend diff 2
python tesla_scan.py trend history BMS_isolationResistance
```

- [ ] **Step 3: Mark §4.5 done in the spec**

In the spec, change the `trend` planned line under §2 "Planned / not yet built"
to note it is implemented, and leave §4.5/§6 as the reference.

- [ ] **Step 4: Run full suite + commit**

```bash
cd /d/AI/Projects/TeslaDiagScripts && python -m pytest -q
git add README.md docs/
git commit -m "docs: mark Phase 3 trend implemented (README + spec)"
```

---

## Self-Review

- **Spec coverage:** §6 schema → Task 2 (`_SCHEMA`); §4.5 ingest → Task 2; baseline → Task 3; diff (new faults / state changes / drift) → Task 4; history + thresholds → Task 5; CLI `ingest/baseline/diff/history` → Task 6; docs → Task 7. Full §4.5/§6 covered.
- **Placeholders:** none — every step has complete, runnable code.
- **Type consistency:** `aggregate_signals` returns `{signal: {module,unit,v_min,v_max,v_last,named_state,n}}`, consumed identically in `ingest`. `diff` returns `{new_faults:[{signal}], state_changes:[{signal,from,to}], drifts:[{signal,from,to}]}`, matched in the diff test and `cmd_trend`. `TrendStore` methods (`ingest/set_baseline/baseline_id/diff/history/set_threshold/_thresholds/close`) are named consistently across tasks. `Fault` fields (`code/module/signal/meaning`) reused from Phase 1 in `ingest`.
- **Risk flagged inline:** Task 4 Step 5 includes the procedure to verify the baseline fixture decodes as intended rather than assume.

---

## Notes for the implementer
- Run all commands from repo root `D:\AI\Projects\TeslaDiagScripts` (paths shown in Git-Bash form).
- The `0080020582020004` baseline fixture bytes must be written with NO spaces or separators.
- Read-only posture unchanged: `trend` only reads capture files and writes its own SQLite DB; it sends nothing to the vehicle.
