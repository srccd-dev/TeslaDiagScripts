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

    def close(self):
        self.conn.close()
