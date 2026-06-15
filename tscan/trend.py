"""Phase 3: SQLite trend store + capture diffing."""
import sqlite3
from tscan.core import module_for
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

    def set_baseline(self, capture_id):
        self.conn.execute("UPDATE captures SET is_baseline=0")
        self.conn.execute("UPDATE captures SET is_baseline=1 WHERE id=?", (capture_id,))
        self.conn.commit()

    def baseline_id(self):
        cur = self.conn.execute("SELECT id FROM captures WHERE is_baseline=1 LIMIT 1")
        row = cur.fetchone()
        return row[0] if row else None

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

    def close(self):
        self.conn.close()
