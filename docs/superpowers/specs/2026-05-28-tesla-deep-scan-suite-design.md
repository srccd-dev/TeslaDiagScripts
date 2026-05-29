# Tesla Deep-Scan Suite — Design Spec

**Date:** 2026-05-28
**Status:** Draft for review
**Repo:** srccd-dev/TeslaDiagScripts
**Author:** Dr. Michael Neumann + The Amazing Claude Code

---

## 1. Purpose

A reusable CLI tool that pulls and explains Tesla CAN data. The vehicle's
DBC (`tesla_models.dbc`) defines **610 messages / 12,520 signals / 3,845 value
tables** — roughly 40× the decoded depth SMT shows. Apps tes-LAX and ScanMyTesla
provide the industry standards with over 300 decoded readings. This suite closes that gap:
read every signal the DBC knows, surface active fault codes with plain-language
meaning, and track signal drift over time so developing issues can be caught and
faulty sensors localized.

Target vehicle for v1: **2016 Tesla Model X P90D**, decoded against the
S/X-architecture `tesla_models.dbc`. The reachable bus is the one the OBD
adapter bridges (the same bus SMT uses — carries BMS/powertrain broadcast).

## 2. Scope

### In scope (Track 2 — this build)
- Capture raw CAN frames to file (decoupled from decode).
- Decode all DBC-known signals via `cantools`, grouped by module/ECU.
- Surface active fault/alert codes with derived plain-language meaning.
- Track signals over time in SQLite and diff against a baseline.

### Out of scope (explicit non-goals)
- **Fixing the current car fault** (Track 1 — the `tesla_iso_capture.py` work).
  Track 2 is not gated on the fault being solved, and vice versa.
- **The scan suite is read-only.** `capture`/`faults`/`dump`/`trend` are passive
  monitors and never send state-changing commands. A conservative, opt-in
  command capability (DTC clear only) is specified in §10 but is **walled off on
  a separate experimental git branch — not part of the suite on `main`**.
- **No security-access bypass, no write DIDs, no routines, no resets,
  no comm-control** anywhere in this project. Same safety posture as all prior
  scripts.
- **Surface "driver" metrics** (0–60 times, range estimates, etc.). Not the point.
- **Model 3/Y decode.** This targets the S/X DBC. Other DBCs are a future concern.
- **Real-time GUI / dashboard.** Output is readable CLI text (+ optional export).
- **Multi-bus capture in one pass.** v1 captures the single reachable bus;
  the format records which adapter/bus produced the frames so future
  Ethernet/other-port adapters slot in without redesign.

## 3. Architecture

A single CLI `tesla_scan.py` with four subcommands over a shared decode core:

```
tesla_scan.py capture   # write raw CAN frames to a timestamped log
tesla_scan.py faults    # Phase 1: active fault/alert codes, explained
tesla_scan.py dump      # Phase 2: every decoded signal, grouped by module
tesla_scan.py trend     # Phase 3: diff a capture vs a baseline (SQLite)
```

**Decoupling principle:** `capture` only ever writes raw frames. `faults`,
`dump`, and `trend` read either a saved capture file **or** a live stream. This
makes the decoder unit-testable without the car, lets us re-decode past captures
as the decoder improves, and gives `trend` a history to work from.

### Module layout
```
tesla_scan.py        # thin CLI dispatcher (argparse subcommands)
tscan/
  __init__.py
  capture.py         # STN/ELM raw-frame capture -> file (live source too)
  core.py            # cantools-backed decode engine + module mapping
  faults.py          # Phase 1 logic
  dump.py            # Phase 2 logic
  trend.py           # Phase 3 logic + SQLite store
  meaning.py         # fault-meaning derivation (+ descriptions.json override)
data/
  tesla_models.dbc   # vendored copy (version-pinned); --dbc overrides
  descriptions.json  # optional human long-descriptions, grown over time
tests/
  test_core.py       # decode against canned frames
  test_faults.py     # fault detection against saved/synthetic captures
  fixtures/          # small raw-capture samples + expected decodes
```

## 4. Components

### 4.1 `capture.py`
- Reuses the proven STN/ELM monitor from `tesla_full_probe.py` and the genuine
  STN1155 hardware pass-filter (`STFAP`) logic from `tesla_iso_capture.py`.
- Modes: full-bus capture, or `--ids 219,020,...` targeted (low-drop) capture.
- Dwell `--secs`; default sized so slow frames (e.g. `0x219` @ 30 s cycle) are
  caught (≥150 s when targeting slow frames).
- Output: capture file (see §5) + a sidecar/meta header recording adapter
  identity, port, protocol, bus label, and start time.
- Read-only: monitor only; never sends vehicle-state commands.

### 4.2 `core.py` (decode engine)
- Loads the DBC once via `cantools.database.load_file()`.
- `decode_frame(can_id, data) -> {signal: DecodedSignal}` where
  `DecodedSignal = (raw_value, scaled_value, unit, named_state_or_None)`.
  Named state comes from the DBC VAL_ table (e.g. `contactorState=2 -> "CLOSED"`).
- Unknown IDs are flagged (counted/listed), never silently dropped.
- **Module mapping:** signal/message name prefix → ECU
  (`BMS_`→BMS, `DI_`/`DIS_`→drive inverter, `GTW_`→gateway, `TAS_`→air susp,
  `CP_`→charge port, `UI_`, `VCFRONT_`, etc.). Mapping table lives in `core.py`,
  extensible. Lets output localize a reading to a physical box.

### 4.3 `faults.py` (Phase 1)
- Consumes decoded signals and selects the **fault namespace**:
  1. Coded fault/alert signals: names matching `_(w|f|u)\d{3}_` (e.g.
     `BMS_w142_SW_Isolation_Degradatio`) whose decoded bit/value is active.
  2. The alert/fault-matrix messages (`BMS_alertMatrix1-6`, equivalents).
  3. State enums in a fault state, via a configurable watch-list
     (e.g. `BMS_contactorState in {WELD, CLEANING}`, `BMS_state in {FAULT, WELD}`).
- For each active item prints: **code · module · plain meaning · raw evidence**
  (the frame ID + bytes that set it).
- Output style: sectioned, readable, `tesla_full_probe.py` aesthetic.

### 4.4 `dump.py` (Phase 2)
- Decodes **all** signals seen, grouped by module, readable text.
- Filters: `--module BMS`, `--grep <regex>`, `--changed` (only signals that
  varied across the capture). Optional `--json` / `--csv`.

### 4.5 `trend.py` (Phase 3)
- Ingests a capture into SQLite (see §6), then diffs against a stored baseline:
  - **New faults** that weren't active in the baseline.
  - **Enum state changes** (e.g. a sensor that flipped state).
  - **Numeric drift** beyond a per-signal threshold (default + overrides).
- Commands: `trend ingest <capture>`, `trend baseline set <capture_id>`,
  `trend diff <capture_id>`, `trend history <signal>`.

### 4.6 `meaning.py`
Builds the human-readable meaning for a fault, in priority order:
1. `descriptions.json` override (long, car-screen-style text we curate over time).
2. DBC CM_ comment for the signal, if present (33 exist).
3. DBC VAL_ named state, if the signal is an enum.
4. Parsed signal-name suffix (e.g. `SW_Isolation_Degradatio` →
   "SW Isolation Degradation"), with the code class decoded
   (`w`=warning, `f`=fault, `u`=?/status — confirmed against DBC usage).

## 5. Capture file format

CSV, one frame per line, with a `#`-prefixed metadata header:

```
# tesla_scan capture v1
# adapter=STN1155 v5.6.19  port=COM5  protocol=ISO15765-11/500  bus=CAN3?  start=2026-05-28T20:00:00
t_ms,can_id,data_hex
28,219,0A1B2C3D4E5F6071
30,102,00FF...
```
- `t_ms`: ms since capture start. `can_id`: 11-bit hex (no `0x`).
  `data_hex`: raw payload bytes, no separators.
- Plain text, greppable, re-decodable, diffable. (candump-style import can be
  added later if useful.)

## 6. SQLite schema (Phase 3)

```sql
CREATE TABLE captures (
  id INTEGER PRIMARY KEY,
  started_at TEXT, file TEXT, adapter TEXT, bus TEXT, notes TEXT,
  is_baseline INTEGER DEFAULT 0
);
CREATE TABLE signal_samples (        -- aggregated per signal per capture
  capture_id INTEGER REFERENCES captures(id),
  signal TEXT, module TEXT, unit TEXT,
  v_min REAL, v_max REAL, v_last REAL, named_state TEXT, n INTEGER
);
CREATE TABLE faults (
  capture_id INTEGER REFERENCES captures(id),
  code TEXT, module TEXT, signal TEXT, meaning TEXT, active INTEGER
);
CREATE TABLE drift_thresholds (       -- per-signal numeric drift config
  signal TEXT PRIMARY KEY, abs_delta REAL, pct_delta REAL
);
CREATE INDEX idx_samples_signal ON signal_samples(signal);
```
Baseline = one capture flagged `is_baseline=1`. `trend diff` compares a target
capture's `signal_samples`/`faults` against the baseline's, applying
`drift_thresholds` (falling back to defaults).

## 7. Dependencies & config
- **Python deps:** `cantools` (decode), `pyserial` (capture). `sqlite3` is stdlib.
- **DBC:** vendored at `data/tesla_models.dbc`; `--dbc PATH` overrides.
- **Config:** capture dir, DBC path, default dwell — CLI flags with sensible
  defaults; no config file required for v1.

## 8. Testing
- `core.py`: unit tests decode canned frames (e.g. synthetic `0x219` with a
  known iso byte → assert kOhm + `contactorState` named state; a known
  `alertMatrix` byte → assert the right `w###` bit decodes active).
- `faults.py`/`dump.py`: run against small saved capture fixtures with expected
  output. No car required — fixtures live in `tests/fixtures/`.
- `trend.py`: ingest two fixture captures, assert diff detects an injected new
  fault and an injected drift.

## 9. Build order (phases)
0. **Foundation:** `capture.py` + `core.py` + vendored DBC + tests for core.
1. **`faults`** — the highest-value first deliverable.
2. **`dump`** — full decoded signal inventory.
3. **`trend`** — SQLite store + baseline diff.
Stop and validate after each phase; each reuses the prior. The **actions module
(§10) is out-of-band**: built on its own `experimental/actions` branch after the
read suite is working, never merged into `main` without explicit sign-off.

## 10. Actions module — DTC clear (SEPARATE EXPERIMENTAL BRANCH)

This capability is **not** part of the read-only suite on `main`. It lives on an
experimental branch (e.g. `experimental/actions`) and is built/validated in
isolation. Rationale: keep the published, community-facing suite provably
passive while developing command-sending separately.

### Posture
- New subcommand `tesla_scan.py action clear-dtc --module <addr>`, never invoked
  by any read path.
- **Allowlist (the only services it may send):**
  - `0x14` ClearDiagnosticInformation
  - `0x10` DiagnosticSessionControl (only the session level `0x14` requires)
  - `0x3E` TesterPresent (keepalive)
- **Hard denylist (never sent, even with flags):** `0x27` SecurityAccess /
  seed-key attempts, `0x2E` WriteDataByIdentifier, `0x31` RoutineControl,
  `0x11` ECUReset, `0x28` CommunicationControl, `0x34/0x36/0x37` transfer.
- **Per-action gate:** prints the exact bytes it will send and the target, then
  requires an explicit interactive confirmation (no silent execution, no
  "assume yes" flag in v1). Logs request + response + decoded NRC.
- **NRC honesty:** on `0x33 securityAccessDenied` it STOPS and reports — it does
  not attempt to satisfy security access.

### Realism caveats (documented in `--help` and output)
- The `alertMatrix` `w/f` bits we read are **live condition flags, not latched
  DTCs** — clearing does nothing while the condition is active; they re-assert.
- DTC clear only meaningfully helps for **stored** DTCs and only "sticks" if the
  underlying root cause is resolved.
- Tesla `0x14` frequently requires a session/security level we will not
  auto-attempt; a `0x33` response is an expected, accepted stopping point.

### Testing
- Dry-run mode prints the planned request without opening the port (unit-test
  the gating + byte construction without a car).
- NRC decode tested against canned negative responses.

## 11. Open questions / future
- Long-form fault descriptions: seed `descriptions.json` from the car-screen
  text we already have; grow as encountered.
- Multi-bus / Ethernet port: format already records `bus`; capture backend can
  gain new adapters later.
- candump/SavvyCAN import: add if we want to decode externally-captured logs.
