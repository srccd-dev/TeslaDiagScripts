# Richer Fault Classification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade the `faults` command in place from naive non-zero flagging to a state-aware classifier (code class, self-test state, severity tier, two detection paths), fixing the `d`-code false-positive bug.

**Architecture:** A pure `classify(signal, value)` helper in `tscan/faults.py` returns a `Classification` (or `None` if not an active fault), driving an enriched `Fault` dataclass and a rewritten `active_faults()`. `cmd_faults` in `tesla_scan.py` renders severity-ranked, labeled output. All against the existing fixtures.

**Tech Stack:** Python 3, `cantools` (via the existing `Decoder`), `pytest`.

**Spec:** `docs/superpowers/specs/2026-06-20-richer-fault-classification-design.md` (§5 model, §5.1 two paths, §5.2 active fix, §5.3 severity).

---

## File Structure
- **`tscan/faults.py`** — `is_fault_value`, `_CODE`/`_CLASS`/`_SEVERITY`, `classify`, `Classification`, enriched `Fault`, rewritten `active_faults`. (Replaces the old `_CODE`/`_CLASS`/`DEFAULT_STATE_WATCH`/`_is_active`.)
- **`tesla_scan.py`** — `cmd_faults`: severity-sorted, labeled output + summary header.
- **`tests/test_faults.py`** — extend with classify unit tests + integration + regressions.
- **`README.md`** — note the richer output.

No other callers break: `tscan/trend.py` uses `Fault.code/module/signal/meaning` only (unchanged fields).

---

## Task 1: `is_fault_value()` — conservative enum-fault match (Path 2)

**Files:** Modify `tscan/faults.py` · Test `tests/test_faults.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_faults.py`:
```python
from tscan.faults import is_fault_value


def test_is_fault_value_conservative():
    assert is_fault_value("FAULT")
    assert is_fault_value("FAILED")
    assert is_fault_value("WELD")
    assert is_fault_value("SOPT_TEST_FAILED")
    # SNA and negations are NOT faults
    assert not is_fault_value("FAULT_SNA")
    assert not is_fault_value("NO_FAULT")
    assert not is_fault_value("NOT_TESTED_DTC")
    assert not is_fault_value("PASSED_DTC")
    assert not is_fault_value("STANDBY")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /d/AI/Projects/TeslaDiagScripts && python -m pytest tests/test_faults.py::test_is_fault_value_conservative -v`
Expected: FAIL — `ImportError: cannot import name 'is_fault_value'`.

- [ ] **Step 3: Add `is_fault_value` to `tscan/faults.py`**

Add near the top of `tscan/faults.py` (after the imports):
```python
_FAULT_TOKENS = ("FAULT", "FAILED", "WELD", "ERROR")


def is_fault_value(named):
    """A decoded enum value indicates a fault when it contains a fault token but is
    not an SNA/negation. Conservative on purpose: FAULT_SNA and NO_FAULT are NOT
    faults, only e.g. FAULT / FAILED / WELD / SOPT_TEST_FAILED."""
    s = str(named).upper()
    if "SNA" in s or s.startswith("NO_") or "NOT_" in s:
        return False
    return any(tok in s for tok in _FAULT_TOKENS)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /d/AI/Projects/TeslaDiagScripts && python -m pytest tests/test_faults.py::test_is_fault_value_conservative -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tscan/faults.py tests/test_faults.py
git commit -m "feat: conservative enum-fault value matcher (is_fault_value)"
```

---

## Task 2: `classify()` — the state-aware core (fixes the bug)

**Files:** Modify `tscan/faults.py` · Test `tests/test_faults.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_faults.py`:
```python
from tscan.faults import classify


class _Named(str):
    """str subclass with a .name attribute, mimicking cantools NamedSignalValue."""
    @property
    def name(self):
        return str(self)


def test_classify_coded_classes():
    assert classify("BMS_f071_x", 1).klass == "fault"
    assert classify("BMS_f071_x", 1).severity == "CRITICAL"
    assert classify("BMS_w158_x", 1).klass == "warning"
    assert classify("BMS_w158_x", 1).severity == "WARNING"
    assert classify("X_a094_y", 1).severity == "WARNING"      # alert -> WARNING
    assert classify("X_u008_y", 1).severity == "STATUS"
    assert classify("BMS_f071_x", 0) is None                  # not active


def test_classify_selftest_is_state_aware():
    # THE BUG FIX: PASSED is good, FAILED is the fault
    assert classify("BMS_d002_x", _Named("PASSED_DTC")) is None
    assert classify("BMS_d002_x", _Named("NOT_TESTED_DTC")) is None
    c = classify("BMS_d002_x", _Named("FAILED_DTC"))
    assert c.klass == "selftest" and c.state == "FAILED_DTC" and c.severity == "CRITICAL"


def test_classify_enum_fault_path():
    assert classify("BMS_state", _Named("FAULT")).severity == "CRITICAL"
    assert classify("BMS_state", _Named("FAULT")).klass == "state"
    assert classify("BMS_state", _Named("STANDBY")) is None
    assert classify("DI_x", _Named("FAULT_SNA")) is None      # SNA excluded
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /d/AI/Projects/TeslaDiagScripts && python -m pytest tests/test_faults.py -k classify -v`
Expected: FAIL — `ImportError: cannot import name 'classify'`.

- [ ] **Step 3: Replace the old `_CODE`/`_CLASS` and add `classify` in `tscan/faults.py`**

Replace the existing lines:
```python
_CODE = re.compile(r"_([wfu])(\d{3})_")
_CLASS = {"w": "warning", "f": "fault", "u": "status"}

# state enums whose listed values count as an active fault
DEFAULT_STATE_WATCH = {
    "BMS_state": {"FAULT", "WELD"},
    "BMS_contactorState": {"WELD", "CLEANING"},
}
```
with:
```python
_CODE = re.compile(r"_([awfud])(\d{3})_")
_CLASS = {"f": "fault", "w": "warning", "a": "alert", "d": "selftest", "u": "status"}
_SEVERITY = {"fault": "CRITICAL", "selftest": "CRITICAL", "state": "CRITICAL",
             "warning": "WARNING", "alert": "WARNING", "status": "STATUS"}


@dataclass
class Classification:
    code: str
    klass: str
    state: object       # str for self-tests, else None
    severity: str


def _nonzero(value):
    try:
        return int(value) != 0
    except (TypeError, ValueError):
        return False


def classify(signal_name, value):
    """Classify one decoded signal -> Classification if it is an ACTIVE fault, else
    None. State-aware: self-test PASSED/NOT_TESTED and benign enums are not faults."""
    named = str(value) if hasattr(value, "name") else None
    m = _CODE.search(signal_name)
    if m:
        klass = _CLASS[m.group(1)]
        code = f"{m.group(1)}{m.group(2)}"
        if klass == "selftest":
            if not (named and "FAILED" in named.upper()):
                return None
            return Classification(code, klass, named, _SEVERITY[klass])
        if not _nonzero(value):
            return None
        return Classification(code, klass, None, _SEVERITY[klass])
    if named is not None and is_fault_value(named):
        return Classification("", "state", named, _SEVERITY["state"])
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /d/AI/Projects/TeslaDiagScripts && python -m pytest tests/test_faults.py -k classify -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add tscan/faults.py tests/test_faults.py
git commit -m "feat: state-aware classify() (fixes d-code PASSED false positive)"
```

---

## Task 3: Enrich `Fault` + rewrite `active_faults()`

**Files:** Modify `tscan/faults.py` · Test `tests/test_faults.py`

- [ ] **Step 1: Write the failing test (integration against real fixtures)**

Add to `tests/test_faults.py`:
```python
def test_active_faults_enriched_fields(decoder):
    # real captured 0x219: BMS_state = FAULT -> enum-fault, CRITICAL
    frames = [(0, 0x219, bytes([0x00, 0x80, 0x7F, 0x00, 0x82, 0x02, 0x00, 0x04]))]
    faults = active_faults(decoder, frames)
    bms = next(f for f in faults if f.signal == "BMS_state")
    assert bms.klass == "state"
    assert bms.severity == "CRITICAL"
    assert "FAULT" in bms.meaning


def test_active_faults_coded_severity(decoder):
    # alertMatrix2 0x021 byte0 0x40 -> BMS_f071 fault, CRITICAL
    faults = active_faults(decoder, [(0, 0x021, bytes([0x40, 0, 0, 0, 0, 0, 0, 0]))])
    f = next(f for f in faults if f.code == "f071")
    assert f.klass == "fault" and f.severity == "CRITICAL"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /d/AI/Projects/TeslaDiagScripts && python -m pytest tests/test_faults.py::test_active_faults_enriched_fields -v`
Expected: FAIL — `AttributeError: 'Fault' object has no attribute 'severity'`.

- [ ] **Step 3: Replace the `Fault` dataclass, `active_faults`, and delete `_is_active`**

Replace the existing `@dataclass class Fault: ...` through the end of `_is_active` with:
```python
@dataclass
class Fault:
    code: str           # e.g. "f071" / "d002"; "" for enum-faults
    klass: str          # fault / warning / alert / selftest / status / state
    state: object       # self-test state string, else None
    severity: str       # CRITICAL / WARNING / STATUS
    module: str
    signal: str
    meaning: str
    can_id: int
    evidence: str       # hex of the frame that set it


def active_faults(decoder, frames, overrides=None):
    """Return a de-duplicated list of active Fault across the frames (latest frame
    per ID wins). Uses classify(): coded signals + enum-faults, state-aware."""
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
            c = classify(sig, val)
            if c is None or sig in seen:
                continue
            seen.add(sig)
            out.append(Fault(
                code=c.code, klass=c.klass, state=c.state, severity=c.severity,
                module=module_for(sig), signal=sig,
                meaning=describe(sig, named_value=(val if c.state else None),
                                 overrides=overrides),
                can_id=can_id, evidence=evidence))
    return out
```

- [ ] **Step 4: Run the whole faults test file (old tests must still pass)**

Run: `cd /d/AI/Projects/TeslaDiagScripts && python -m pytest tests/test_faults.py -v`
Expected: PASS — including the pre-existing `test_state_watchlist_flags_bms_fault`, `test_coded_fault_bit_detected`, `test_no_faults_when_clean` (they access `.signal`/`.code`/`.module`/`.meaning`, all still present).

- [ ] **Step 5: Commit**

```bash
git add tscan/faults.py tests/test_faults.py
git commit -m "feat: enriched Fault (state/severity) + two-path active_faults"
```

---

## Task 4: `cmd_faults` — severity-ranked, labeled output

**Files:** Modify `tesla_scan.py` · Test `tests/test_faults.py`

- [ ] **Step 1: Write the failing test (CLI via main + capsys)**

Add to `tests/test_faults.py`:
```python
import os
from tests.conftest import FIXTURES


def test_cmd_faults_severity_output(capsys):
    import tesla_scan
    tesla_scan.main(["faults", os.path.join(FIXTURES, "sample_0219.csv")])
    out = capsys.readouterr().out
    assert "CRITICAL" in out          # BMS_state=FAULT is CRITICAL
    assert "BMS_state" in out
    assert "active code" in out       # summary header present
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /d/AI/Projects/TeslaDiagScripts && python -m pytest tests/test_faults.py::test_cmd_faults_severity_output -v`
Expected: FAIL — output lacks "CRITICAL" (old format prints `[module] tag signal`).

- [ ] **Step 3: Replace `cmd_faults` in `tesla_scan.py`**

Replace the existing `def cmd_faults(args): ...` with:
```python
_SEV_ORDER = {"CRITICAL": 0, "WARNING": 1, "STATUS": 2}


def cmd_faults(args):
    decoder = Decoder(args.dbc)
    overrides = _load_overrides(args.descriptions)
    _meta, frames = parse_capture_file(args.capture)
    faults = active_faults(decoder, frames, overrides=overrides)
    if not faults:
        print("No active fault/alert codes in this capture.")
        return
    counts = {}
    for f in faults:
        counts[f.severity] = counts.get(f.severity, 0) + 1
    summary = ", ".join(f"{counts.get(s, 0)} {s}" for s in ("CRITICAL", "WARNING", "STATUS"))
    print(f"{len(faults)} active code(s) - {summary}:\n")
    for f in sorted(faults, key=lambda x: (_SEV_ORDER.get(x.severity, 9), x.module, x.signal)):
        label = f.code or f.klass
        state = f" {f.state}" if f.state else ""
        print(f"  [{f.severity}] {f.klass} {label}{state}  {f.signal}  ({f.module})")
        print(f"        meaning : {f.meaning}")
        print(f"        evidence: 0x{f.can_id:03X} = {f.evidence}")
        print(f"        tessie  : {tessie_link(f.signal)}\n")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /d/AI/Projects/TeslaDiagScripts && python -m pytest tests/test_faults.py::test_cmd_faults_severity_output -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tesla_scan.py tests/test_faults.py
git commit -m "feat: severity-ranked labeled faults output"
```

---

## Task 5: Full suite + README

**Files:** Modify `README.md`

- [ ] **Step 1: Run the entire suite (nothing else regressed)**

Run: `cd /d/AI/Projects/TeslaDiagScripts && python -m pytest -q`
Expected: all tests PASS (trend/dump/core/capture/meaning + the new faults tests).

- [ ] **Step 2: Update the `faults` bullet in `README.md`**

Replace the existing `faults` bullet under "## Status":
```markdown
- **`faults`** — classify active fault/alert codes by **class** (`fault`/`warning`/
  `alert`/`selftest`/`status`) and **severity** (CRITICAL/WARNING/STATUS), state-aware
  (a self-test reading `PASSED` is not flagged; `FAILED` is), with plain-language
  meaning, module, and a Tessie link. Surfaces pre-fault `warning`s and failing
  self-tests as early signals.
```

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: richer faults classification in README"
```

---

## Self-Review

- **Spec coverage:** §4 bug fix → Task 2 (`classify` self-test path) + Task 2 regression test; §5 model (klass/state/active/severity) → Tasks 2,3; §5.1 two paths → Task 2 (coded + enum); §5.1 conservative match → Task 1; §5.2 active logic → Task 2; §5.3 severity → Task 2 (`_SEVERITY`) + Task 4 ordering; §6 output → Task 4; §7 files → all in `faults.py`/`tesla_scan.py`; §8 tests → Tasks 1–4; §9 early-warning (warning/selftest surfaced) → falls out of the class split. Full coverage.
- **Placeholders:** none — every step has complete, runnable code.
- **Type consistency:** `classify()` returns `Classification(code, klass, state, severity)` or `None`; `Fault` adds `state, severity` and is built by keyword in `active_faults`; `cmd_faults` reads `severity/klass/code/state/signal/module/meaning/can_id/evidence` — all defined on `Fault`. `is_fault_value` used only inside `classify`. `_SEVERITY` keys match every `klass` value (`fault/warning/alert/selftest/status/state`).
- **No-break check:** `tscan/trend.py` builds rows from `f.code/module/signal/meaning` only — still present; full suite run in Task 5 Step 1 confirms.

---

## Notes for the implementer
- Run all commands from repo root `D:\AI\Projects\TeslaDiagScripts`.
- The pre-existing faults tests are intentionally left passing (they exercise the same behavior through the new path) — do not delete them; if one needs an assertion tweak for a renamed field, adjust minimally rather than removing coverage.
- Read-only posture unchanged: classification only reads decoded frames.
