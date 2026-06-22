# Decode Correction Overlay Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an MIT correction overlay + length/trust-aware `DecodeEngine` so mislabeled/short frames decode correctly and `faults` stops over-reporting, with coverage growing one evidence-backed overlay entry at a time.

**Architecture:** A hand-authored `data/overlay.json` of per-message corrections is loaded by `tscan/overlay.py` into an in-memory cantools overlay database plus a side table of `length`/`trust`/`replace_signals` flags. A `DecodeEngine` composes the overlay over the existing base-DBC `Decoder`: it truncates padding to the true wire length, decodes via overlay (replace) or base (+optional merge), and exposes a `trust(can_id)` level. `faults` consumes that level to skip `analog`/`unknown` frames; `dump` uses the engine but ignores trust.

**Tech Stack:** Python 3, cantools 41.4.0 (`BaseConversion.factory`, hand-built `Signal`/`Message`/`Database`), pytest.

---

## Scope notes

**Spec:** `docs/superpowers/specs/2026-06-22-decode-correction-overlay-design.md`

**Reference sources (development only — NEVER committed; see `.gitignore`):**
- `D:\Dev\Projects\Tesla-ODB2\PT.dbc` — authoritative S/X powertrain DBC (Amond/SMT).
  Author/verify overlay definitions against it. e.g. `0x3F8` (BO_ 1016) =
  `RCCM_THCHvacDuctSensors2` — four duct temps, **not** DCDC faults.
- `D:\Dev\Projects\Tesla-ODB2\BMS-debug.dbc` — BMS debug/DTC + iso resistance defs
  (`BMS_rawIsolationResistance`, mux 126, ×20 kOhm). For battery-bus frames reachable
  only via PCAN; reference for later iso work.
- talas9 (online) — secondary cross-check.

We **derive our own MIT overlay** from these; we never copy or commit the files. See
the **boundary gate** in Task 4 before committing `data/overlay.json` publicly.

**Deferred (reserved in schema, implemented when first needed — flagged for user):**
- `enum_fix` — no first-batch frame needs value-name remapping (lights use `trust:"unknown"` instead).
- Overlay-level **multiplexing** (`is_muxer`/`mux_id`) — genuine mux already works through the base DBC (`0x322`); no overlay frame needs it yet.
- `trend.py` adopting the engine — out of scope; `trend` keeps the base `Decoder` for now.

**Backward compatibility:** `active_faults` / `dump_signals` keep their positional first argument (an object with `.decode(can_id, data)`). A plain `Decoder` has no `.trust`, so existing tests that pass the `decoder` fixture behave exactly as before. The trust gate only activates when an object exposing `.trust` (i.e. `DecodeEngine`) is passed.

---

## Task 1: Overlay loader

**Files:**
- Create: `tscan/overlay.py`
- Test: `tests/test_overlay.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_overlay.py
import json
from tscan.overlay import load_overlay


def test_load_overlay_parses_entries_and_builds_db(tmp_path):
    p = tmp_path / "overlay.json"
    p.write_text(json.dumps({
        "version": 1,
        "messages": {
            "3F8": {
                "name": "DCDC_status", "length": 8, "trust": "analog",
                "replace_signals": True,
                "signals": [
                    {"name": "DCDC_rawWord0", "start": 0, "length": 16,
                     "endian": "little", "scale": 1, "offset": 0, "unit": ""}
                ],
            },
            "212": {"length": 5, "trust": "unknown"},
        },
    }), encoding="utf-8")

    ov = load_overlay(str(p))
    assert ov.entry(0x3F8)["replace_signals"] is True
    assert ov.entry(0x212)["length"] == 5
    assert ov.trust(0x3F8) == "analog"
    assert ov.trust(0x212) == "unknown"
    assert ov.trust(0x999) == "faults"            # untagged default
    # message with signals is decodable via the overlay db
    dec = ov.db.decode_message(0x3F8, bytes([0xFB, 0x02, 0, 0, 0, 0, 0, 0]),
                               decode_choices=True, allow_truncated=True)
    assert dec["DCDC_rawWord0"] == 0x02FB         # 763, little-endian


def test_load_overlay_missing_file_is_empty(tmp_path):
    ov = load_overlay(str(tmp_path / "nope.json"))
    assert ov.entry(0x219) is None
    assert ov.trust(0x219) == "faults"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_overlay.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tscan.overlay'`

- [ ] **Step 3: Write minimal implementation**

```python
# tscan/overlay.py
"""Hand-authored correction overlay (data/overlay.json) over the base DBC.

We author definitions in JSON; cantools still does the bit extraction. Each
message entry may carry: length (true wire length), trust (faults|analog|
unknown), name, replace_signals, and signals (compact defs). See the design
spec for the schema and rationale.
"""
import json

from cantools.database.conversion import BaseConversion
from cantools.database.can.signal import Signal
from cantools.database.can.message import Message
from cantools.database.can.database import Database


def _build_signal(d):
    byte_order = "big_endian" if str(d.get("endian", "little")).startswith("big") \
        else "little_endian"
    choices = {int(k): v for k, v in d.get("values", {}).items()} or None
    conv = BaseConversion.factory(scale=d.get("scale", 1), offset=d.get("offset", 0),
                                  choices=choices)
    return Signal(name=d["name"], start=d["start"], length=d["length"],
                  byte_order=byte_order, is_signed=bool(d.get("signed", False)),
                  conversion=conv, unit=d.get("unit", ""))


class Overlay:
    def __init__(self, entries, db):
        self.entries = entries        # {frame_id:int -> entry dict}
        self.db = db                  # cantools Database of overlay messages w/ signals

    def entry(self, can_id):
        return self.entries.get(can_id)

    def trust(self, can_id):
        e = self.entries.get(can_id)
        return (e or {}).get("trust", "faults")


def load_overlay(path):
    """Load overlay.json -> Overlay. Missing/invalid file yields an empty overlay
    (so the suite works with no overlay present)."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        return Overlay({}, Database(messages=[], strict=False))
    entries, msgs = {}, []
    for hid, e in raw.get("messages", {}).items():
        fid = int(hid, 16)
        entries[fid] = e
        if e.get("signals"):
            sigs = [_build_signal(s) for s in e["signals"]]
            msgs.append(Message(frame_id=fid, name=e.get("name", f"OVL_{hid}"),
                                length=e.get("length", 8), signals=sigs, strict=False))
    return Overlay(entries, Database(messages=msgs, strict=False))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_overlay.py -v`
Expected: PASS (both tests)

- [ ] **Step 5: Commit**

```bash
git add tscan/overlay.py tests/test_overlay.py
git commit -m "feat: overlay loader (data/overlay.json -> cantools overlay db + flags)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: DecodeEngine (compose overlay over base DBC)

**Files:**
- Modify: `tscan/overlay.py` (append `DecodeEngine`)
- Test: `tests/test_overlay.py` (extend)

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_overlay.py
import os
from tscan.core import Decoder
from tscan.overlay import DecodeEngine

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DBC = os.path.join(REPO, "data", "tesla_models.dbc")


def _engine_with(tmp_path, messages):
    import json
    p = tmp_path / "overlay.json"
    p.write_text(json.dumps({"version": 1, "messages": messages}), encoding="utf-8")
    return DecodeEngine(Decoder(DBC), load_overlay(str(p)))


def test_engine_replace_signals_overrides_dbc(tmp_path):
    eng = _engine_with(tmp_path, {
        "3F8": {"length": 8, "trust": "analog", "replace_signals": True,
                "signals": [{"name": "DCDC_rawWord0", "start": 0, "length": 16,
                             "endian": "little"}]},
    })
    dec = eng.decode(0x3F8, bytes([0xFB, 0x02, 0, 0, 0, 0, 0, 0]))
    assert dec == {"DCDC_rawWord0": 0x02FB}        # DBC's fake fault bits are gone
    assert eng.trust(0x3F8) == "analog"


def test_engine_truncates_padding_to_true_length(tmp_path):
    # overlay says true length 5; a padded 8-byte frame must not read bytes 5-7
    eng = _engine_with(tmp_path, {"212": {"length": 5, "trust": "unknown"}})
    full = eng.decode(0x212, bytes([0xD8, 0x09, 0x12, 0x1E, 0x00, 0xFF, 0xFF, 0xFF]))
    short = eng.decode(0x212, bytes([0xD8, 0x09, 0x12, 0x1E, 0x00]))
    assert full == short                            # padding ignored
    assert eng.trust(0x212) == "unknown"


def test_engine_falls_through_to_dbc_for_untouched_ids(tmp_path):
    eng = _engine_with(tmp_path, {})
    real_0219 = bytes([0x00, 0x80, 0x7F, 0x00, 0x82, 0x02, 0x00, 0x04])
    dec = eng.decode(0x219, real_0219)
    assert "BMS_isolationResistance" in dec
    assert eng.trust(0x219) == "faults"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_overlay.py -k engine -v`
Expected: FAIL with `ImportError: cannot import name 'DecodeEngine'`

- [ ] **Step 3: Write minimal implementation**

```python
# append to tscan/overlay.py
class DecodeEngine:
    """Compose the overlay over the base DBC decoder.

    For each frame: truncate padding beyond the overlay's declared true length,
    decode via the overlay (when replace_signals) or the base DBC (optionally
    merging overlay signals on top), and expose the trust level for `faults`.
    Drop-in for `Decoder` (same `.decode(can_id, data)`), plus `.trust(can_id)`.
    """

    def __init__(self, base, overlay):
        self.base = base              # tscan.core.Decoder
        self.overlay = overlay        # Overlay

    def trust(self, can_id):
        return self.overlay.trust(can_id)

    def _overlay_decode(self, can_id, data):
        try:
            return self.overlay.db.decode_message(
                can_id, data, decode_choices=True, allow_truncated=True)
        except Exception:
            return None

    def decode(self, can_id, data):
        e = self.overlay.entry(can_id)
        length = (e or {}).get("length")
        if length is not None:
            data = data[:length]      # ignore padding beyond the true wire length
        if e and e.get("signals") and e.get("replace_signals"):
            return self._overlay_decode(can_id, data)
        dec = self.base.decode(can_id, data)
        if e and e.get("signals"):    # merge overlay signals onto the base result
            merged = self._overlay_decode(can_id, data)
            if merged:
                dec = {**(dec or {}), **merged}
        return dec
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_overlay.py -v`
Expected: PASS (all overlay tests)

- [ ] **Step 5: Commit**

```bash
git add tscan/overlay.py tests/test_overlay.py
git commit -m "feat: DecodeEngine composes overlay over base DBC (length-guard + trust)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: Trust gate in `faults`

**Files:**
- Modify: `tscan/faults.py` (`active_faults`)
- Test: `tests/test_faults.py` (extend)

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_faults.py
class _FakeEngine:
    """Minimal engine: returns one coded-fault signal, with a settable trust."""
    def __init__(self, trust_level):
        self._trust = trust_level

    def decode(self, can_id, data):
        return {"BMS_f071_someFault": 1}

    def trust(self, can_id):
        return self._trust


def test_active_faults_skips_untrusted_frames():
    frames = [(0, 0x123, b"\x01")]
    assert active_faults(_FakeEngine("analog"), frames) == []
    assert active_faults(_FakeEngine("unknown"), frames) == []
    flagged = active_faults(_FakeEngine("faults"), frames)
    assert [f.signal for f in flagged] == ["BMS_f071_someFault"]


def test_active_faults_without_trust_attr_behaves_as_before(decoder):
    # a plain Decoder has no .trust -> all frames classified (legacy behavior)
    frames = [(0, 0x219, bytes([0x00, 0x80, 0x7F, 0x00, 0x82, 0x02, 0x00, 0x04]))]
    faults = active_faults(decoder, frames)
    assert any(f.signal == "BMS_state" for f in faults)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_faults.py -k trust -v`
Expected: FAIL — `test_active_faults_skips_untrusted_frames` fails because analog/unknown frames are still classified.

- [ ] **Step 3: Write minimal implementation**

In `tscan/faults.py`, change `active_faults` to gate on trust. Replace the function's loop preamble so it reads:

```python
def active_faults(engine, frames, overrides=None):
    """Return a de-duplicated list of active Fault across the frames (latest frame
    per ID wins). Frames whose engine trust is 'analog'/'unknown' are skipped; a
    decoder without a .trust method (plain Decoder) classifies every frame."""
    latest = {}
    for _t, can_id, data in frames:
        latest[can_id] = data

    trust_of = getattr(engine, "trust", None)
    out, seen = [], set()
    for can_id, data in latest.items():
        if trust_of and trust_of(can_id) in ("analog", "unknown"):
            continue
        dec = engine.decode(can_id, data)
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

(Only the signature/docstring, the `trust_of = ...` line, and the `if trust_of ...: continue` guard are new; the rest is unchanged from the current implementation.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_faults.py -v`
Expected: PASS (new trust tests + all existing faults tests)

- [ ] **Step 5: Commit**

```bash
git add tscan/faults.py tests/test_faults.py
git commit -m "feat: faults trust gate (skip analog/unknown frames)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: First overlay batch + integration regression

**Files:**
- Create: `data/overlay.json`
- Modify: `tests/conftest.py` (add `engine` fixture)
- Test: `tests/test_overlay.py` (add integration regression)

- [ ] **Step 1: Write the failing test**

First add the fixture to `tests/conftest.py`:

```python
# append to tests/conftest.py
@pytest.fixture
def engine(decoder):
    from tscan.overlay import load_overlay, DecodeEngine
    overlay_path = os.path.join(REPO, "data", "overlay.json")
    return DecodeEngine(decoder, load_overlay(overlay_path))
```

Then the regression test:

```python
# append to tests/test_overlay.py
from tscan.faults import active_faults
from tscan.dump import dump_signals

# top payload observed for 0x3F8 in the 2026-06-21 drive capture
REAL_3F8 = bytes([0xFB, 0x02, 0xFA, 0x02, 0xF7, 0x02, 0xFD, 0x02])


def test_overlay_batch_kills_dcdc_false_faults(engine):
    faults = active_faults(engine, [(0, 0x3F8, REAL_3F8)])
    assert faults == []                              # was 8 bogus DCDC "faults"


def test_overlay_batch_3f8_decodes_hvac_duct_temps(engine):
    grouped = dump_signals(engine, [(0, 0x3F8, REAL_3F8)])
    flat = {n: v for vals in grouped.values() for n, v in vals}
    assert "RCCM_LeftVentDuctSnsRaw_DegC" in flat
    # raw word 0x02FB=763 -> 763*0.1 - 40 = 36.3 degC (plausible duct temp)
    assert 30 <= flat["RCCM_LeftVentDuctSnsRaw_DegC"] <= 45
    assert {"RCCM_RightVentDuctSnsRaw_DegC", "RCCM_LeftFloorDuctSnsRaw_DegC",
            "RCCM_RightFloorDuctSnsRaw_DegC"} <= set(flat)


def test_overlay_batch_suppresses_light_frames_from_faults(engine):
    frames = [(0, 0x212, bytes([0xD8, 0x09, 0x12, 0xFF, 0x00])),
              (0, 0x232, bytes([0x6A, 0x27, 0xE9, 0x9C]))]
    assert active_faults(engine, frames) == []       # was many bogus light "FAULTs"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_overlay.py -k batch -v`
Expected: FAIL — `data/overlay.json` does not exist, so the engine has no entries and the DBC's bogus signals still classify.

- [ ] **Step 3: Write the overlay file**

```json
{
  "version": 1,
  "messages": {
    "3F8": {
      "name": "RCCM_THCHvacDuctSensors2",
      "comment": "Community DBC mislabels as DCDC_alertMatrix1 (boolean fault bits); the captured bus actually carries cabin-HVAC duct temps. Definition verified against Amond/SMT PT.dbc (BO_ 1016, NOT redistributed) and confirmed by byte-variance probe of the 2026-06-21 capture (4x u16 LE, ~20-50 C). See Task 4 boundary gate before public commit.",
      "length": 8,
      "trust": "analog",
      "replace_signals": true,
      "signals": [
        {"name": "RCCM_LeftVentDuctSnsRaw_DegC",  "start": 0,  "length": 16, "endian": "little", "scale": 0.1, "offset": -40, "unit": "degC"},
        {"name": "RCCM_RightVentDuctSnsRaw_DegC", "start": 16, "length": 16, "endian": "little", "scale": 0.1, "offset": -40, "unit": "degC"},
        {"name": "RCCM_LeftFloorDuctSnsRaw_DegC", "start": 32, "length": 16, "endian": "little", "scale": 0.1, "offset": -40, "unit": "degC"},
        {"name": "RCCM_RightFloorDuctSnsRaw_DegC","start": 48, "length": 16, "endian": "little", "scale": 0.1, "offset": -40, "unit": "degC"}
      ]
    },
    "212": {
      "length": 5,
      "trust": "unknown",
      "comment": "BCFRONT_lightStatus: wire is 5B not 8B (all 3912 frames in 2026-06-21 capture). DBC 8B light enums decode unreliable (byte3=FF -> spurious FAULT). Suppress from faults until characterized."
    },
    "232": {
      "length": 4,
      "trust": "unknown",
      "comment": "BCREAR_lightStatus: wire is 4B not 8B (all 3905 frames). Same issue as 0x212."
    }
  }
}
```

- [ ] **Step 3b: Add the RCCM module label** so `dump` groups the duct temps sensibly

In `tscan/core.py`, add to the `MODULE_PREFIXES` dict (in the thermal area):

```python
    "RCCM": "Cabin HVAC (RCCM)",
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_overlay.py -v`
Expected: PASS (batch regression + earlier overlay tests)

> **⚠ BOUNDARY GATE — do before Step 5.** `data/overlay.json` gets committed to the
> **public** repo. The `0x3F8` definition's names/scale are *verified against* Amond's
> PT.dbc (shared for development, not for redistribution). Confirm with the user that
> Amond is OK with PT.dbc-**derived** definitions (signal names + scale/offset, not the
> file) shipping in the committed overlay **with attribution**. If **yes** → commit as
> written. If **no/unsure** → replace `RCCM_*` names with neutral derived names
> (e.g. `CabinDuctTempLeftVent_DegC`), keep `scale 0.1 / offset -40` (justified by the
> empirical ~20–50 °C range), and note "independently derived" in the comment. **Do not
> commit until this is resolved.**

- [ ] **Step 5: Commit**

```bash
git add data/overlay.json tscan/core.py tests/conftest.py tests/test_overlay.py
git commit -m "feat: first overlay batch (0x3F8 HVAC duct temps, 0x212/0x232 length+trust)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 5: Wire the engine into the CLI

**Files:**
- Modify: `tesla_scan.py` (`cmd_faults`, `cmd_dump`, arg parsers, constants)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli_overlay.py  (new)
import subprocess
import sys
import os

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _write_capture(tmp_path):
    p = tmp_path / "cap.csv"
    p.write_text(
        "# tesla_scan capture v1\n"
        "t_ms,can_id,data_hex\n"
        "0,3F8,FB02FA02F702FD02\n",
        encoding="utf-8")
    return str(p)


def test_cli_faults_uses_overlay(tmp_path):
    cap = _write_capture(tmp_path)
    out = subprocess.run([sys.executable, "tesla_scan.py", "faults", cap],
                         cwd=REPO, capture_output=True, text=True)
    assert out.returncode == 0
    assert "DCDC_alertMatrix1" not in out.stdout       # bogus matrix faults gone
    assert "DCDC_w00" not in out.stdout


def test_cli_dump_shows_overlay_words(tmp_path):
    cap = _write_capture(tmp_path)
    out = subprocess.run([sys.executable, "tesla_scan.py", "dump", cap, "--grep", "rawWord"],
                         cwd=REPO, capture_output=True, text=True)
    assert out.returncode == 0
    assert "DCDC_rawWord0" in out.stdout
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_cli_overlay.py -v`
Expected: FAIL — `cmd_dump`/`cmd_faults` still build a plain `Decoder`, so `DCDC_w00x` appears and `rawWord` does not.

- [ ] **Step 3: Edit `tesla_scan.py`**

Add the overlay default near the other constants (after `DEFAULT_OVERRIDES`):

```python
DEFAULT_OVERLAY = os.path.join(REPO, "data", "overlay.json")
```

Add the import at the top with the others:

```python
from tscan.overlay import load_overlay, DecodeEngine
```

In `cmd_faults`, replace `decoder = Decoder(args.dbc)` with:

```python
    engine = DecodeEngine(Decoder(args.dbc), load_overlay(args.overlay))
```

and change the `active_faults(...)` call's first argument from `decoder` to `engine`.

In `cmd_dump`, replace `decoder = Decoder(args.dbc)` with:

```python
    engine = DecodeEngine(Decoder(args.dbc), load_overlay(args.overlay))
```

and change the `dump_signals(...)` call's first argument from `decoder` to `engine`.

Add `--overlay` to both subparsers. In the `faults` parser block:

```python
    f.add_argument("--overlay", default=DEFAULT_OVERLAY)
```

In the `dump` parser block:

```python
    dp.add_argument("--overlay", default=DEFAULT_OVERLAY)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_cli_overlay.py -v`
Expected: PASS (both)

Then full suite: `python -m pytest -q`
Expected: PASS (all prior + new)

- [ ] **Step 5: Commit**

```bash
git add tesla_scan.py tests/test_cli_overlay.py
git commit -m "feat: wire DecodeEngine + --overlay into faults/dump CLI

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 6: `profile_frame` tool (graduate the throwaway probe)

**Files:**
- Create: `tools/profile_frame.py`
- Test: `tests/test_profile_frame.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_profile_frame.py
import os
from tools.profile_frame import profile
from tscan.core import Decoder

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DBC = os.path.join(REPO, "data", "tesla_models.dbc")


def test_profile_reports_length_and_variance():
    frames = [
        (0, 0x3F8, bytes([0xFB, 0x02, 0xFA, 0x02, 0xF7, 0x02, 0xFD, 0x02])),
        (1, 0x3F8, bytes([0x00, 0x03, 0xFA, 0x02, 0xFA, 0x02, 0xFB, 0x02])),
        (2, 0x111, bytes([0x01])),                      # different id ignored
    ]
    r = profile(frames, 0x3F8, Decoder(DBC))
    assert r["count"] == 2
    assert r["lengths"] == {8: 2}
    assert r["distinct_payloads"] == 2
    assert len(r["byte_variance"]) == 8
    assert r["byte_variance"][0] == 2                   # byte0 varies (FB vs 00)
    assert r["dbc_name"] == "DCDC_alertMatrix1"


def test_profile_unknown_id_returns_empty():
    r = profile([(0, 0x111, b"\x01")], 0x7FF, Decoder(DBC))
    assert r["count"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_profile_frame.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'tools.profile_frame'`

- [ ] **Step 3: Write the tool**

```python
# tools/__init__.py   (empty file so tests can import tools.profile_frame)
```

```python
# tools/profile_frame.py
"""Characterize a CAN id's on-wire structure vs the DBC: length distribution,
distinct payloads, per-byte value variance, and DBC length/mux expectations.

Usage:
    python tools/profile_frame.py <capture.csv> <hexid> [<hexid> ...]
"""
import sys
from collections import Counter


def profile(frames, can_id, decoder):
    """frames: list[(t_ms, can_id, data)]. Returns a dict of structural stats."""
    payloads = [d for _t, cid, d in frames if cid == can_id]
    out = {"can_id": can_id, "count": len(payloads), "lengths": {},
           "distinct_payloads": 0, "byte_variance": [],
           "dbc_name": None, "dbc_length": None, "dbc_multiplexed": None}
    try:
        msg = decoder.db.get_message_by_frame_id(can_id)
        out["dbc_name"] = msg.name
        out["dbc_length"] = msg.length
        out["dbc_multiplexed"] = msg.is_multiplexed()
    except (KeyError, AttributeError):
        pass
    if not payloads:
        return out
    out["lengths"] = dict(Counter(len(p) for p in payloads))
    out["distinct_payloads"] = len(Counter(p.hex().upper() for p in payloads))
    maxlen = max(len(p) for p in payloads)
    out["byte_variance"] = [len(Counter(p[i] for p in payloads if len(p) > i))
                            for i in range(maxlen)]
    return out


def _main(argv):
    from tscan.core import Decoder
    from tscan.capture import parse_capture_file
    import os
    if len(argv) < 2:
        print(__doc__)
        return 2
    cap, ids = argv[0], [int(x, 16) for x in argv[1:]]
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    dec = Decoder(os.path.join(repo, "data", "tesla_models.dbc"))
    _meta, frames = parse_capture_file(cap)
    for cid in ids:
        r = profile(frames, cid, dec)
        print(f"\n0x{cid:03X}  dbc={r['dbc_name']} dbc_len={r['dbc_length']} "
              f"muxed={r['dbc_multiplexed']}")
        print(f"  count={r['count']} lengths={r['lengths']} "
              f"distinct={r['distinct_payloads']}")
        print(f"  byte-variance={r['byte_variance']}")
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_profile_frame.py -v`
Expected: PASS (both)

- [ ] **Step 5: Commit**

```bash
git add tools/__init__.py tools/profile_frame.py tests/test_profile_frame.py
git commit -m "feat: tools/profile_frame.py (repeatable frame characterizer)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 7: README + full-suite verification

**Files:**
- Modify: `README.md` (note the overlay + corrected decoding)

- [ ] **Step 1: Run the full suite**

Run: `python -m pytest -q`
Expected: PASS (all tests across the suite)

- [ ] **Step 2: Update the README**

In `README.md`, under the `dump`/`faults` descriptions, add a short paragraph (place it after the `faults` bullet):

```markdown
- **decode overlay** — `data/overlay.json` is a hand-authored, MIT-licensed set of
  per-message corrections over the community DBC: it fixes mislabeled frames (e.g.
  `0x3F8`, which the DBC calls `DCDC_alertMatrix1` but is actually cabin-HVAC duct
  temperatures), declares true wire lengths for short frames (`0x212`/`0x232`), and tags each frame's
  `trust` (`faults`/`analog`/`unknown`) so `faults` only flags trustworthy frames.
  This removes the bulk of the old false positives; coverage grows one evidence-backed
  entry at a time. Characterize a new frame with `python tools/profile_frame.py
  <capture.csv> <hexid>`.
```

Also update the "Known limitations" `faults over-reports` bullet to note the overlay now suppresses the analog/short-frame offenders.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: document decode overlay + profile_frame tool

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Done criteria

- `python -m pytest -q` passes (overlay loader, DecodeEngine, trust gate, batch regression, CLI, profile tool, plus all pre-existing tests).
- `python tesla_scan.py faults <drive capture>` no longer reports the `DCDC_w00x` faults or the all-lights `FAULT` block.
- `python tesla_scan.py dump <drive capture> --grep DuctSns` shows the four RCCM duct temps.
- `data/overlay.json` documents each entry's evidence; `tools/profile_frame.py` reproduces the characterization that justified it.
