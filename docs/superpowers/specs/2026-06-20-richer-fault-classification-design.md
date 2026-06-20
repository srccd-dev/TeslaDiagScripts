# Richer Fault Classification — Design Spec

**Date:** 2026-06-20
**Status:** Draft for review
**Repo:** srccd-dev/TeslaDiagScripts
**Pillar:** Early-warning detection (categorical half) — first of two

---

## 1. Purpose

Upgrade the `faults` command **in place** from naive "any non-zero bit" flagging to a
proper, **state-aware fault classifier**. This is the categorical half of the
early-warning vision: surface a developing problem (a `warning` before the `fault`,
a self-test flipping to `FAILED`) **from CAN + DBC alone — no Tesla Toolbox required**,
because the people who reach for this software are precisely the ones without a
Toolbox subscription.

It also fixes a real false-positive bug in the current `faults` logic (below).

## 2. Scope

### In scope
- Classify each detected fault by: **code class** (`a/w/f/d/u`), **self-test state**,
  **active-vs-not** (state-aware), and **severity tier**.
- Detect faults via **two paths**: coded signals (`_[awfud]###_`) **and** state-enum
  signals whose decoded value indicates a fault (`FAULT/FAILED/WELD/ERROR`).
- Fix the `d`-code / enum false-positive bug.
- **Severity-ranked, class-labeled** `faults` output.

### Out of scope (roadmap, named so they're not silently dropped)
- **Curated severity/audience overlay** (which codes are chronic noise vs critical,
  `[service]`/`[customer]` tiers) — that's the *relevance* layer, the natural next refinement.
- **Isolation-watch** — the continuous-signal early-warning pillar (separate spec next).
- **teslalogs firmware-extracted signal maps** — to close the DBC-vs-firmware gap
  (e.g. `f027` = `Unused_27` in our DBC but `SW_Drive_Iso` in firmware); separate effort.

## 3. Background — repo review findings

Reviewed comma.ai/opendbc, MatthewKuKanich/CAN_Commander, openvehicles/CAN-RE-Tool,
and NetherlandsForensicInstitute/teslalogs. **None add fault-code coverage beyond our
DBC** — they are ADAS/driving DBCs or binary-log parsers, none with alertMatrix/
faultMatrix or `PASSED/FAILED/NOT_TESTED` value tables. Our `tesla_models.dbc` is the
richest fault source. Two refinements did surface and are folded into §5:
self-test states beyond `d`-codes (`DI_soptState`), and faults that appear as a state
signal's *value* rather than via `w/f/d` naming.

## 4. The bug this fixes

`faults` currently flags **any non-zero** `_w/f/u###_` value. For `d`-codes (DTC
self-tests), the decoded value is an enum: `0=NOT_TESTED, 1=PASSED, 2=FAILED, 3=UNABLE`.
So `BMS_d002_Internal_Iso_Failure = PASSED_DTC` (value 1) is **falsely flagged as
active**, when `PASSED` is *good*. State-aware classification removes this entire class
of false positive.

## 5. Classification model

Each detected code becomes a `Fault` with these fields (existing fields unchanged,
new fields added):

| Field | Values | Source |
|---|---|---|
| `klass` | `fault` / `warning` / `alert` / `selftest` / `status` | name letter `f/w/a/d/u`, or `state` for enum-faults |
| `state` | `PASSED` / `FAILED` / `NOT_TESTED` / `UNABLE` / `None` | DBC value table (self-tests) |
| `active` | bool | **state-aware** (§5.2) |
| `severity` | `CRITICAL` / `WARNING` / `STATUS` | §5.3 |
| `code` | e.g. `f027`, `d002`, `a094`, or `""` for enum-faults | name |
| `module`, `signal`, `meaning`, `can_id`, `evidence`, `tessie` | unchanged | existing |

### 5.1 Detection — two paths

**Path 1 — coded signals.** Regex `_([awfud])(\d{3})_` on the signal name.
Class from letter: `f`→fault, `w`→warning, `a`→alert, `d`→selftest, `u`→status.

**Path 2 — state-enum faults.** Any decoded signal whose **named value** indicates a
fault → `klass=state`, `active=True`. This replaces the current hardcoded
`DEFAULT_STATE_WATCH` and generically catches `BMS_state=FAULT`, `DI_state=FAULT`,
`DI_soptState=SOPT_TEST_FAILED`, `BMS_contactorState=WELD`, etc.

The match must be **conservative** — naive substring matching would wrongly flag
`FAULT_SNA` ("fault state not available") and `NO_FAULT`:

```python
_FAULT_TOKENS = ("FAULT", "FAILED", "WELD", "ERROR")

def is_fault_value(named):
    s = str(named).upper()
    if "SNA" in s or s.startswith("NO_") or "NOT_" in s:
        return False                       # SNA / negations are not faults
    return any(tok in s for tok in _FAULT_TOKENS)
```
So `SOPT_TEST_FAILED`/`WELD`/`FAULT` match; `FAULT_SNA`, `NO_FAULT`, `NOT_TESTED`,
`UNAVAILABLE`, `STANDBY`, `PASSED`, `IN_PROGRESS` do **not**.

### 5.2 Active logic (the fix)

```python
def is_active(klass, value, state):
    if klass == "selftest":          # d-codes
        return state is not None and "FAILED" in state
    if klass == "state":             # enum-fault path
        return True                  # only constructed when value already in FAULT_VALUES
    return _nonzero(value)           # f / w / a / u : bit or value set
```

### 5.3 Severity mapping

| Severity | Triggers |
|---|---|
| **CRITICAL** | active `fault` (f) · `selftest` FAILED (d) · `state` enum-fault |
| **WARNING** | active `warning` (w) · active `alert` (a) |
| **STATUS** | active `status` (u) |

Non-active items (e.g. `selftest PASSED`) are **not emitted** at all.

## 6. Output

`faults` prints **sorted CRITICAL → WARNING → STATUS**, each line labeled with severity,
class, module, and (for self-tests) the state, keeping the existing meaning + evidence
+ Tessie link:

```
[CRITICAL] fault f027  BMS_f027_Unused_27   (Battery Management)
           meaning : Drive-unit isolation fault ...
           evidence: 0x020 = 00000004
           tessie  : https://www.google.com/search?q=site%3Astats.tessie.com%20BMS_f027
[CRITICAL] state  BMS_state = FAULT   (Battery Management)
[WARNING]  warning w158  BMS_w158_SW_Low_Isolation_Wrn   (Battery Management)
```
A summary header counts by severity (e.g. `2 CRITICAL, 1 WARNING, 0 STATUS`).

## 7. File structure

All in **`tscan/faults.py`** (per the chosen "upgrade in place"):
- `FAULT_VALUES` set + `classify(signal, value, named_state) -> (klass, state, active, severity)`.
- Enriched `Fault` dataclass (`+klass, +state, +severity`).
- Rewritten `active_faults()` using both detection paths + `classify()`.
- `tesla_scan.py` `cmd_faults`: severity-sorted, labeled output + summary header.

No new module (the chosen integration). The classification helper is kept clean/pure
so the future isolation-watch pillar can reuse the severity logic.

## 8. Testing

`tests/test_faults.py` (extend), against the real fixtures we already have
(`0x219`, `0x020` f027, `0x021` f071, GTW faultMatrix):
- **`d` FAILED → CRITICAL active**; **`d` PASSED → NOT flagged** (the bug regression).
- `f` active → CRITICAL; `w` active → WARNING; `a` active → WARNING; `u` active → STATUS.
- **state-enum:** `BMS_state=FAULT` → CRITICAL; a benign enum (`STANDBY`) → not flagged;
  `BMS_contactorState=WELD` → CRITICAL.
- **enum edge cases (the conservative-match regression):** `FAULT_SNA` → not flagged;
  `NO_FAULT` → not flagged; `SOPT_TEST_FAILED` → flagged.
- Output ordering: CRITICAL before WARNING before STATUS.

## 9. Early-warning tie-in

By separating `warning`/`alert` from `fault` and emitting self-test `FAILED` distinctly,
the output now **shows the pre-fault signals** (a `w` before its `f`, a `d` flipping to
`FAILED`) — the categorical early-warning the Toolbox-less owner needs, and the
foundation the isolation-watch pillar builds on.
