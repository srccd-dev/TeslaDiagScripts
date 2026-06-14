# Tesla Deep-Scan Suite — Phase 0 + 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the read-only decode foundation and the `faults` subcommand — capture raw Tesla CAN frames to file, decode every DBC-known signal via cantools, and print active fault/alert codes with plain-language meaning.

**Architecture:** A `tscan/` package with a cantools-backed decode core, a capture reader/writer, a meaning-derivation helper, and a fault-selector, all driven by a thin `tesla_scan.py` CLI. Capture and decode are decoupled: capture writes raw frames; decode reads a file or live stream. Everything except live serial I/O is unit-tested against fixtures, no car required.

**Tech Stack:** Python 3, `cantools` (DBC decode, loaded `strict=False`), `pyserial` (capture), `pytest` (tests), `sqlite3` (later phase, not here).

**Scope:** Phase 0 (scaffold, capture, core, module map, meaning) + Phase 1 (`faults`). `dump` (Phase 2), `trend` (Phase 3), and the actions module are separate follow-on plans. Spec: `docs/superpowers/specs/2026-05-28-tesla-deep-scan-suite-design.md`.

**Reference source:** The proven STN/ELM serial-monitor logic to port into `capture.py` lives in
`C:\Tools\Vehicle Tools\ECU_OBD2 Tools\2016 Tesla Model X P90D\SMT Files\tesla_iso_capture.py`
(functions `open_retry`, `Dev.cmd`, `Dev.monitor`, `setup_stn_filters`, `parse_line`).

---

## File Structure

- `tesla_scan.py` — CLI dispatcher (argparse subcommands: `capture`, `faults`).
- `tscan/__init__.py` — package marker.
- `tscan/capture.py` — raw-frame capture format: `write_frame`, `parse_capture_file` (read), `CaptureWriter`; live serial capture `capture_live`.
- `tscan/core.py` — `Decoder` (loads DBC `strict=False`, `decode`), `module_for`.
- `tscan/meaning.py` — `describe` (override → DBC comment → VAL_ name → humanized suffix).
- `tscan/faults.py` — `Fault` dataclass, `active_faults`.
- `data/tesla_models.dbc` — vendored DBC (version-pinned).
- `data/descriptions.json` — optional human-description overrides (starts `{}`).
- `requirements.txt` — runtime + dev deps.
- `tests/conftest.py` — shared fixtures (decoder, paths).
- `tests/fixtures/sample_0219.csv` — one real captured frame.
- `tests/test_capture.py`, `tests/test_core.py`, `tests/test_meaning.py`, `tests/test_faults.py`.

---

## Task 0: Project scaffold

**Files:**
- Create: `requirements.txt`, `tscan/__init__.py`, `data/descriptions.json`, `tests/conftest.py`, `.gitignore`
- Create (copy): `data/tesla_models.dbc`

- [ ] **Step 1: Create the package + data dirs and vendor the DBC**

```bash
cd /d/AI/Projects/TeslaDiagScripts
mkdir -p tscan data tests/fixtures
cp "/d/AI/.claude/Tesla ECU Analysis/CAN Capture/tesla_models.dbc" data/tesla_models.dbc
printf '' > tscan/__init__.py
printf '{}\n' > data/descriptions.json
```

- [ ] **Step 2: Write `requirements.txt`**

```
cantools>=39
pyserial>=3.5
pytest>=8
```

- [ ] **Step 3: Write `.gitignore`**

```
__pycache__/
*.pyc
.pytest_cache/
*.sqlite
captures/
```

- [ ] **Step 4: Write `tests/conftest.py`**

```python
import os
import pytest
from tscan.core import Decoder

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DBC = os.path.join(REPO, "data", "tesla_models.dbc")
FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


@pytest.fixture(scope="session")
def decoder():
    return Decoder(DBC)
```

- [ ] **Step 5: Install deps and commit**

```bash
cd /d/AI/Projects/TeslaDiagScripts
pip install -r requirements.txt
git add requirements.txt .gitignore tscan/__init__.py data/descriptions.json tests/conftest.py data/tesla_models.dbc
git commit -m "chore: scaffold tscan package, vendor DBC, dev deps"
```

---

## Task 1: Capture file format (read side)

The capture file is CSV with `#`-prefixed metadata, body header `t_ms,can_id,data_hex`. The reader is pure and testable without hardware.

**Files:**
- Create: `tscan/capture.py`
- Create: `tests/fixtures/sample_0219.csv`
- Test: `tests/test_capture.py`

- [ ] **Step 1: Write the fixture (one real captured 0x219 frame)**

`tests/fixtures/sample_0219.csv`:
```
# tesla_scan capture v1
# adapter=STN1155 v5.6.19 port=COM5 protocol=ISO15765-11/500 bus=CAN3 start=2026-06-14T14:22:21
t_ms,can_id,data_hex
0,219,00807F0082020004
30,219,00807F0082020004
```

- [ ] **Step 2: Write the failing test**

`tests/test_capture.py`:
```python
import os
from tscan.capture import parse_capture_file
from tests.conftest import FIXTURES


def test_parse_capture_file_reads_meta_and_frames():
    meta, frames = parse_capture_file(os.path.join(FIXTURES, "sample_0219.csv"))
    assert meta["adapter"] == "STN1155 v5.6.19"
    assert meta["bus"] == "CAN3"
    assert len(frames) == 2
    t_ms, can_id, data = frames[0]
    assert t_ms == 0
    assert can_id == 0x219
    assert data == bytes([0x00, 0x80, 0x7F, 0x00, 0x82, 0x02, 0x00, 0x04])
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd /d/AI/Projects/TeslaDiagScripts && python -m pytest tests/test_capture.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tscan.capture'` (or ImportError).

- [ ] **Step 4: Write minimal implementation**

`tscan/capture.py`:
```python
"""Raw CAN capture file format (read + write) and live serial capture.

File format (CSV):
    # tesla_scan capture v1
    # adapter=... port=... protocol=... bus=... start=...
    t_ms,can_id,data_hex
    0,219,00807F0082020004
"""


def parse_capture_file(path):
    """Return (meta: dict, frames: list[(t_ms:int, can_id:int, data:bytes)])."""
    meta, frames = {}, []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            if line.startswith("#"):
                for tok in line.lstrip("#").strip().split():
                    if "=" in tok:
                        k, v = tok.split("=", 1)
                        meta[k] = v
                # also capture space-containing values after first key
                _merge_spaced_meta(line, meta)
                continue
            if line.startswith("t_ms"):
                continue
            parts = line.split(",")
            if len(parts) != 3:
                continue
            t_ms = int(parts[0])
            can_id = int(parts[1], 16)
            data = bytes.fromhex(parts[2])
            frames.append((t_ms, can_id, data))
    return meta, frames


def _merge_spaced_meta(line, meta):
    """Handle values that contain spaces, e.g. 'adapter=STN1155 v5.6.19 port=COM5'.
    Re-parse the line splitting on '<key>=' boundaries so a value runs until the
    next 'key=' token."""
    body = line.lstrip("#").strip()
    import re
    keys = list(re.finditer(r"(\w+)=", body))
    for i, m in enumerate(keys):
        k = m.group(1)
        start = m.end()
        end = keys[i + 1].start() if i + 1 < len(keys) else len(body)
        meta[k] = body[start:end].strip()
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd /d/AI/Projects/TeslaDiagScripts && python -m pytest tests/test_capture.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add tscan/capture.py tests/test_capture.py tests/fixtures/sample_0219.csv
git commit -m "feat: capture file format reader with metadata parsing"
```

---

## Task 2: Decode core

Loads the DBC `strict=False` (our DBC has a malformed message `BCCEN_udsResponse` that breaks strict loading) and decodes a frame to named signals.

**Files:**
- Create: `tscan/core.py`
- Test: `tests/test_core.py`

- [ ] **Step 1: Write the failing test (uses the real captured frame)**

`tests/test_core.py`:
```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /d/AI/Projects/TeslaDiagScripts && python -m pytest tests/test_core.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tscan.core'`.

- [ ] **Step 3: Write minimal implementation**

`tscan/core.py`:
```python
"""cantools-backed decode engine for Tesla CAN frames."""
import cantools

# message/signal name prefix -> human module label
MODULE_PREFIXES = {
    "BMS": "Battery Management", "DI": "Drive Inverter",
    "DIS": "Drive Inverter", "DIR": "Rear Drive Inverter",
    "DIF": "Front Drive Inverter", "GTW": "Gateway",
    "TAS": "Air Suspension", "CP": "Charge Port", "CHG": "Charger",
    "CHGS": "Charger", "UI": "User Interface (MCU)", "PCS": "Power Conversion",
    "VCFRONT": "Front Body Controller", "VCREAR": "Rear Body Controller",
    "EPAS": "Steering", "IBST": "iBooster Brake", "DAS": "Autopilot",
    "ESP": "Stability Control", "SCCM": "Steering Column",
    "PARK": "Park Assist", "BCFRONT": "Body Controller Front",
}


def module_for(name):
    """Map a signal/message name to a human module label via its prefix."""
    prefix = name.split("_", 1)[0]
    return MODULE_PREFIXES.get(prefix, f"Unknown ({prefix})")


class Decoder:
    def __init__(self, dbc_path):
        # strict=False: our DBC has at least one malformed message
        # (BCCEN_udsResponse) that fails strict validation but is irrelevant here.
        self.db = cantools.database.load_file(dbc_path, strict=False)
        self._ids = {m.frame_id for m in self.db.messages}

    def decode(self, can_id, data):
        """Return {signal_name: value_or_named} or None if the ID/decode is unknown."""
        if can_id not in self._ids:
            return None
        try:
            return self.db.decode_message(
                can_id, data, decode_choices=True, allow_truncated=True
            )
        except Exception:
            return None

    def message_name(self, can_id):
        try:
            return self.db.get_message_by_frame_id(can_id).name
        except KeyError:
            return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /d/AI/Projects/TeslaDiagScripts && python -m pytest tests/test_core.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add tscan/core.py tests/test_core.py
git commit -m "feat: cantools decode core (strict=False) + module mapping"
```

---

## Task 3: Meaning derivation

Builds a human-readable meaning for a signal: override file → DBC comment → VAL_ named value → humanized name suffix.

**Files:**
- Create: `tscan/meaning.py`
- Test: `tests/test_meaning.py`

- [ ] **Step 1: Write the failing test**

`tests/test_meaning.py`:
```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /d/AI/Projects/TeslaDiagScripts && python -m pytest tests/test_meaning.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tscan.meaning'`.

- [ ] **Step 3: Write minimal implementation**

`tscan/meaning.py`:
```python
"""Human-readable meaning for a Tesla CAN signal/fault."""
import re

_CODE = re.compile(r"^[A-Z0-9]+_([wfu])(\d{3})_(.*)$")


def humanize(signal_name):
    """Strip the '<MODULE>_<class><nnn>_' prefix and de-underscore the rest."""
    m = _CODE.match(signal_name)
    suffix = m.group(3) if m else signal_name
    return suffix.replace("_", " ").strip()


def describe(signal_name, named_value=None, comment=None, overrides=None):
    """Priority: override file -> DBC comment -> enum named value -> humanized name."""
    overrides = overrides or {}
    if signal_name in overrides:
        return overrides[signal_name]
    if comment:
        return comment
    human = humanize(signal_name)
    if named_value is not None and not isinstance(named_value, (int, float)):
        return f"{human} = {named_value}"
    return human
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /d/AI/Projects/TeslaDiagScripts && python -m pytest tests/test_meaning.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add tscan/meaning.py tests/test_meaning.py
git commit -m "feat: signal meaning derivation (override/comment/enum/name)"
```

---

## Task 4: Fault selection

Selects the "fault namespace" from decoded frames: coded `_<wfu>NNN_` signals that are active, plus a watch-list of state enums in a fault state.

**Files:**
- Create: `tscan/faults.py`
- Test: `tests/test_faults.py`

- [ ] **Step 1: Write the failing test (real FAULT state + synthetic coded fault)**

`tests/test_faults.py`:
```python
from tscan.faults import active_faults


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /d/AI/Projects/TeslaDiagScripts && python -m pytest tests/test_faults.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'tscan.faults'`.

- [ ] **Step 3: Write minimal implementation**

`tscan/faults.py`:
```python
"""Select active fault/alert codes from decoded frames."""
import re
from dataclasses import dataclass
from tscan.core import module_for
from tscan.meaning import describe

_CODE = re.compile(r"_([wfu])(\d{3})_")
_CLASS = {"w": "warning", "f": "fault", "u": "status"}

# state enums whose listed values count as an active fault
DEFAULT_STATE_WATCH = {
    "BMS_state": {"FAULT", "WELD"},
    "BMS_contactorState": {"WELD", "CLEANING"},
}


@dataclass
class Fault:
    code: str          # e.g. "f071" or "" for state-enum hits
    klass: str         # "fault" / "warning" / "status" / "state"
    module: str
    signal: str
    meaning: str
    can_id: int
    evidence: str      # hex of the frame that set it


def active_faults(decoder, frames, overrides=None, state_watch=None):
    """Return a de-duplicated list of Fault for every active coded signal or
    watched state-enum across the frames (latest frame per ID wins)."""
    state_watch = state_watch or DEFAULT_STATE_WATCH
    latest = {}
    for _t, can_id, data in frames:
        latest[can_id] = data

    out, seen = [], set()
    for can_id, data in latest.items():
        dec = decoder.decode(can_id, data)
        if not dec:
            continue
        evidence = data.hex().upper()
        for sig, val in dec.items():
            m = _CODE.search(sig)
            if m and _is_active(val):
                key = (sig,)
                if key in seen:
                    continue
                seen.add(key)
                out.append(Fault(
                    code=f"{m.group(1)}{m.group(2)}", klass=_CLASS[m.group(1)],
                    module=module_for(sig), signal=sig,
                    meaning=describe(sig, overrides=overrides),
                    can_id=can_id, evidence=evidence,
                ))
            elif sig in state_watch and str(val) in state_watch[sig]:
                key = (sig,)
                if key in seen:
                    continue
                seen.add(key)
                out.append(Fault(
                    code="", klass="state", module=module_for(sig), signal=sig,
                    meaning=describe(sig, named_value=val, overrides=overrides),
                    can_id=can_id, evidence=evidence,
                ))
    return out


def _is_active(val):
    """A coded fault/alert signal is active when its bit/value is non-zero."""
    try:
        return int(val) != 0
    except (TypeError, ValueError):
        return False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /d/AI/Projects/TeslaDiagScripts && python -m pytest tests/test_faults.py -v`
Expected: PASS (3 tests). If `test_coded_fault_bit_detected` fails because the alertMatrix2 bit layout differs, inspect with:
`python -c "from tscan.core import Decoder; d=Decoder('data/tesla_models.dbc'); print(d.decode(0x021, bytes([0x40,0,0,0,0,0,0,0])))"`
and adjust the test's byte to the bit the DBC actually assigns to `f071` (do not change production code to fit a wrong assumption).

- [ ] **Step 5: Commit**

```bash
git add tscan/faults.py tests/test_faults.py
git commit -m "feat: active fault selection (coded signals + state watchlist)"
```

---

## Task 5: `faults` CLI + capture writer/live + dispatcher

Wires a readable `faults` command over a capture file, plus the capture writer and the live serial capture ported from the reference script.

**Files:**
- Create: `tesla_scan.py`
- Modify: `tscan/capture.py` (add `CaptureWriter`, `capture_live`)
- Test: `tests/test_capture.py` (add writer round-trip test)

- [ ] **Step 1: Write the failing writer round-trip test**

Add to `tests/test_capture.py`:
```python
def test_writer_roundtrip(tmp_path):
    from tscan.capture import CaptureWriter, parse_capture_file
    p = tmp_path / "cap.csv"
    with CaptureWriter(str(p), meta={"adapter": "X", "bus": "CAN3"}) as w:
        w.write(0, 0x219, bytes([0x00, 0x80, 0x7F, 0x00, 0x82, 0x02, 0x00, 0x04]))
    meta, frames = parse_capture_file(str(p))
    assert meta["bus"] == "CAN3"
    assert frames[0][1] == 0x219
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /d/AI/Projects/TeslaDiagScripts && python -m pytest tests/test_capture.py::test_writer_roundtrip -v`
Expected: FAIL — `ImportError: cannot import name 'CaptureWriter'`.

- [ ] **Step 3: Add `CaptureWriter` and `capture_live` to `tscan/capture.py`**

Append to `tscan/capture.py`:
```python
class CaptureWriter:
    """Write raw frames in the capture file format."""

    def __init__(self, path, meta=None):
        self.path = path
        self.meta = meta or {}
        self.fh = None

    def __enter__(self):
        self.fh = open(self.path, "w", encoding="utf-8")
        self.fh.write("# tesla_scan capture v1\n")
        if self.meta:
            kv = " ".join(f"{k}={v}" for k, v in self.meta.items())
            self.fh.write(f"# {kv}\n")
        self.fh.write("t_ms,can_id,data_hex\n")
        return self

    def write(self, t_ms, can_id, data):
        self.fh.write(f"{t_ms},{can_id:03X},{data.hex().upper()}\n")

    def __exit__(self, *exc):
        if self.fh:
            self.fh.close()


def capture_live(port, seconds, ids=None, meta=None, out_path=None, baud=115200):
    """Live serial capture using the proven STN/ELM monitor. Ports the logic from
    tesla_iso_capture.py (open_retry/Dev/setup_stn_filters/parse_line). Writes
    frames to out_path. Returns out_path. Requires hardware; not unit-tested."""
    import time
    import serial  # pyserial

    # --- minimal serial driver (mirrors reference script) ---
    s = None
    last = None
    for _ in range(6):
        try:
            s = serial.Serial(port, baud, timeout=1.0); break
        except Exception as e:
            last = e; time.sleep(0.9)
    if s is None:
        raise last

    def cmd(c, read_for=1.2):
        s.reset_input_buffer(); s.write((c + "\r").encode()); time.sleep(0.08)
        buf, t0 = b"", time.time()
        while time.time() - t0 < read_for:
            n = s.in_waiting
            if n:
                buf += s.read(n)
                if b">" in buf:
                    break
            else:
                time.sleep(0.03)
        return buf.decode(errors="replace").replace("\r", " ").replace(">", "").strip()

    for c in ("ATWS", "ATE0", "ATL0", "ATS1", "ATH1", "ATCAF0", "ATSP6"):
        cmd(c)
    adapter = cmd("STI")[:40]
    meta = dict(meta or {})
    meta.setdefault("adapter", adapter)
    meta.setdefault("port", port)

    start = "ATMA"
    if ids:
        cmd("STFAC")
        for cid in ids:
            cmd(f"STFAP {cid},7FF")
        start = "STM"

    out_path = out_path or f"capture_{int(time.time())}.csv"
    s.reset_input_buffer(); s.write((start + "\r").encode())
    t0, part = time.time(), ""
    with CaptureWriter(out_path, meta) as w:
        try:
            while time.time() - t0 < seconds:
                n = s.in_waiting
                if n:
                    part += s.read(n).decode(errors="replace")
                    lines = part.split("\r"); part = lines.pop()
                    for ln in lines:
                        fr = _parse_monitor_line(ln)
                        if fr:
                            w.write(int((time.time() - t0) * 1000), fr[0], fr[1])
                else:
                    time.sleep(0.02)
        except KeyboardInterrupt:
            pass
    s.write(b"\r"); time.sleep(0.2); s.close()
    return out_path


def _parse_monitor_line(ln):
    """Parse an ATH1+ATS1 monitor line 'ID b0 b1 ...' -> (can_id:int, data:bytes)."""
    u = ln.strip().upper()
    if not u or any(k in u for k in ("OK", "STOPPED", "BUFFER", "SEARCHING",
                                     "STM", "ATMA", "?", "ERROR", "NO DATA")):
        return None
    toks = u.split()
    if len(toks) < 2:
        return None
    cid = toks[0]
    if len(cid) not in (3, 4) or any(c not in "0123456789ABCDEF" for c in cid):
        return None
    try:
        data = bytes(int(t, 16) for t in toks[1:] if len(t) == 2)
    except ValueError:
        return None
    return (int(cid, 16), data)
```

- [ ] **Step 4: Run writer test to verify it passes**

Run: `cd /d/AI/Projects/TeslaDiagScripts && python -m pytest tests/test_capture.py -v`
Expected: PASS (all capture tests).

- [ ] **Step 5: Write `tesla_scan.py` dispatcher (`faults` + `capture`)**

`tesla_scan.py`:
```python
#!/usr/bin/env python3
"""Tesla deep-scan suite CLI. Read-only. Subcommands: capture, faults."""
import argparse
import json
import os
import sys

from tscan.core import Decoder
from tscan.capture import parse_capture_file, capture_live
from tscan.faults import active_faults

REPO = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DBC = os.path.join(REPO, "data", "tesla_models.dbc")
DEFAULT_OVERRIDES = os.path.join(REPO, "data", "descriptions.json")


def _load_overrides(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def cmd_faults(args):
    decoder = Decoder(args.dbc)
    overrides = _load_overrides(args.descriptions)
    _meta, frames = parse_capture_file(args.capture)
    faults = active_faults(decoder, frames, overrides=overrides)
    if not faults:
        print("No active fault/alert codes in this capture.")
        return
    print(f"{len(faults)} active code(s):\n")
    for f in sorted(faults, key=lambda x: (x.module, x.signal)):
        tag = f.code or f.klass
        print(f"  [{f.module}]  {tag}  {f.signal}")
        print(f"        meaning : {f.meaning}")
        print(f"        evidence: 0x{f.can_id:03X} = {f.evidence}\n")


def cmd_capture(args):
    ids = args.ids.split(",") if args.ids else None
    out = capture_live(args.port, args.secs, ids=ids, out_path=args.out)
    print(f"Capture written to {out}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Tesla deep-scan suite (read-only)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("capture", help="record raw CAN frames to file")
    c.add_argument("--port", required=True)
    c.add_argument("--secs", type=int, default=60)
    c.add_argument("--ids", help="comma-separated 11-bit hex IDs to pass-filter")
    c.add_argument("--out", help="output capture file path")
    c.set_defaults(func=cmd_capture)

    f = sub.add_parser("faults", help="list active fault/alert codes from a capture")
    f.add_argument("capture", help="capture file path")
    f.add_argument("--dbc", default=DEFAULT_DBC)
    f.add_argument("--descriptions", default=DEFAULT_OVERRIDES)
    f.set_defaults(func=cmd_faults)

    args = ap.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Verify the CLI end-to-end against the fixture**

Run: `cd /d/AI/Projects/TeslaDiagScripts && python tesla_scan.py faults tests/fixtures/sample_0219.csv`
Expected: prints one code under `[Battery Management]` for `BMS_state` with meaning containing `FAULT` and evidence `0x219 = 00807F0082020004`.

- [ ] **Step 7: Seed `data/descriptions.json` with the known drive-iso code**

`data/descriptions.json`:
```json
{
  "BMS_f027_SW_Drive_Iso": "Drive-unit isolation fault (SW_Drive_Iso) — HV isolation low in a drive-unit/heater circuit; 'Unable to start vehicle'. DBC mislabels this slot 'Unused_27'.",
  "BMS_w172_SW_Drive_Iso_Warning": "Drive-unit isolation warning — isolation degrading in a drive-unit/heater HV circuit."
}
```

- [ ] **Step 8: Commit**

```bash
git add tesla_scan.py tscan/capture.py tests/test_capture.py data/descriptions.json
git commit -m "feat: faults + capture CLI, live STN capture, drive-iso descriptions"
```

---

## Task 6: README usage + full test run

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Run the full test suite**

Run: `cd /d/AI/Projects/TeslaDiagScripts && python -m pytest -v`
Expected: all tests PASS.

- [ ] **Step 2: Add a Usage section to `README.md`**

Append under the existing "What it does" section:
````markdown
## Usage (Phase 0/1)

```bash
pip install -r requirements.txt

# capture raw frames (read-only; one app may hold the adapter at a time)
python tesla_scan.py capture --port COM5 --secs 60 --out captures/run1.csv
# slow/targeted: only specific IDs
python tesla_scan.py capture --port COM5 --secs 150 --ids 219,021,061 --out captures/iso.csv

# decode active fault/alert codes from a capture
python tesla_scan.py faults captures/run1.csv
```

Curate human descriptions in `data/descriptions.json` (override key = exact signal name).
````

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: Phase 0/1 usage in README"
```

---

## Self-Review

- **Spec coverage (Phase 0/1 scope):** capture file format (§5) → Tasks 1,5; decode core + module map (§4.2) → Tasks 2,3; meaning (§4.6) → Task 3; fault selection (§4.3) → Task 4; `faults` CLI + `capture` (§3,§4.1) → Task 5; read-only posture (§2) → no write services anywhere. Deferred by scope: `dump` (§4.4), `trend`+SQLite (§4.5,§6), actions (§10) — separate plans, called out in header.
- **Placeholders:** none — every code step is complete and runnable.
- **Type consistency:** `parse_capture_file` returns `(meta, frames)` with `frames=[(t_ms,can_id:int,data:bytes)]`, consumed identically in `active_faults`, `cmd_faults`, and `capture_live`/`CaptureWriter`. `Decoder.decode` returns dict-or-None, checked everywhere. `Fault` fields used in `cmd_faults` (`module/code/klass/signal/meaning/can_id/evidence`) match the dataclass.
- **Known risk flagged inline:** Task 4 Step 4 includes the procedure to confirm the `f071` bit position empirically rather than assume.

---

## Notes for the implementer
- Run all commands from the repo root `D:\AI\Projects\TeslaDiagScripts` (paths above use the Git-Bash `/d/AI/...` form; adjust for PowerShell as needed).
- `capture_live` needs the physical OBDLink on COM5 and the car awake; it is validated manually, not in CI. Everything else is covered by `pytest`.
- Keep all services read-only. No `0x14/0x27/0x2E/0x31/0x11` anywhere in this plan.
