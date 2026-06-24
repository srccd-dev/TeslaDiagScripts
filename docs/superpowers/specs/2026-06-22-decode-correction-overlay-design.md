# Decode Correction Overlay (mux- & length-aware) — Design Spec

**Date:** 2026-06-22
**Status:** Draft for review
**Repo:** srccd-dev/TeslaDiagScripts
**Pillar:** Decode correctness & coverage — foundation under early-warning

---

## 1. Purpose

Make signal decoding **correct first, broad second**. Today `faults` over-reports
massively (a 9.5-min drive capture flagged 156 "active" codes, 34 CRITICAL — nearly
all artifacts) because the community DBC mislabels and mis-sizes a number of frames.
This adds a small, hand-authored, **MIT-licensed correction overlay** plus a
**length-aware decode layer**, so the values we already capture translate correctly,
and the overlay can grow over time to *add* coverage.

The guiding principle (agreed with the user): **correctness-focused growth** — fix
the false positives we have evidence for, then expand signal coverage incrementally
as we author more overlay entries. No big-bang re-definition.

## 2. Background — what the drive capture proved

A 2026-06-21 drive capture (960k frames, 234 unique IDs) plus a byte-variance probe
showed the "matrix decode" problem is really **three** distinct problems:

1. **Mislabeled frames (analog, not faults).** `0x3F8` ("DCDC_alertMatrix1", 11
   supposed fault bits) is on the wire as **4 × 16-bit little-endian analog values**
   (odd bytes pinned at 0x02/0x03, even bytes ranging widely). Reading ADC bytes as
   boolean faults is why `heaterShortFault` and `heaterOpenFault` "fired" together.
2. **Frame-length mismatch.** `0x212` BCFRONT_lightStatus: DBC declares 8 bytes, the
   wire delivers **5** (all 3,912 frames). `0x232`: DBC says 8, wire is **4**. The
   light "FAULTs" come from interpreting short frames against an 8-byte layout.
3. **Genuine multiplexing mostly already works.** `0x322` BMS_dtcMatrix1 *is* muxed
   (`BMS_dtcIndex`); cantools handles it, and every frame is index 0 / all-zero — so
   `NOT_TESTED` was correct, the bus simply only ever sends the empty page. `0x3E2`
   is all-zero too. These are genuinely clear, not broken.

**Implications that shaped the design:**
- cantools already does mux + `allow_truncated` + enum decoding. The gap is not "mux
  support"; it is **wrong/over-long message definitions** and **no length-guard** on
  what gets emitted.
- The talas9 repo (richer, mux-aware compact-JSON) is useful **as reference only**: it
  has *no* license (can't be vendored into an MIT repo) and contains **no** matrix
  messages at all — confirming Tesla doesn't model them as flat signal frames.
- We can only capture the **gateway-rebroadcast subset** on the OBD port (~234 IDs);
  the raw BMS bus (`0x219` et al.) is not on this wire. So the overlay's target set is
  bounded and known. See `project_obd_bus_topology`, `project_faults_matrix_decode`.

## 3. Scope

### In scope
- A hand-authored **`data/overlay.json`** (MIT) of per-message corrections.
- A **`tscan/overlay.py`** loader that builds an in-memory overlay DB from cantools
  `Message`/`Signal` objects (we author JSON; cantools still does the bit work).
- A **`DecodeEngine`** that composes overlay + base DBC, with a **global length-guard**.
- `dump` and `faults` consume `DecodeEngine`; `faults` gains a **`trust`** gate.
- First overlay batch (evidence-backed): `0x3F8` (analog), `0x212` / `0x232`
  (length + trust), plus a triage pass over the worst offenders in the 156-fault list.
- A kept, repeatable **`tools/profile_frame.py`** (graduated from the throwaway probe).

### Out of scope (named so they're not silently dropped)
- **General talas9 / compact-JSON importer** — user chose derive-our-own.
- **Re-defining all ~2,000 DBC signals** — the DBC stays the base.
- **Cracking the community-invented matrices** — several are mislabeled analog or
  genuinely empty; we correct or suppress, we don't reverse-engineer them now.
- **Relevance ranking by Tessie fleet incidence** — still the later refinement.

## 4. Overlay schema (`data/overlay.json`)

Keyed by uppercase hex CAN ID. All per-message fields optional; most entries are tiny
(a `length` + `trust`). Every entry carries a `comment` citing its evidence.

```json
{
  "version": 1,
  "messages": {
    "3F8": {
      "name": "DCDC_status",
      "comment": "DBC mislabels as alertMatrix1. Wire is 4x u16 LE analog (odd bytes pinned 0x02/03). Evidence: byte-variance probe 2026-06-21.",
      "length": 8,
      "trust": "analog",
      "replace_signals": true,
      "signals": [
        {"name": "DCDC_rawWord0", "start": 0, "length": 16, "endian": "little", "scale": 1, "offset": 0, "unit": ""}
      ]
    },
    "212": {
      "length": 5,
      "trust": "unknown",
      "comment": "BCFRONT_lightStatus: wire is 5B not 8B; DBC 8B light enums unreliable."
    },
    "232": { "length": 4, "trust": "unknown",
             "comment": "BCREAR_lightStatus: wire is 4B not 8B." }
  }
}
```

| Field | Meaning |
|---|---|
| `length` | True wire length. Drives the length-guard; signals beyond it (or beyond the received bytes) are never emitted. |
| `trust` | `faults` = coded signals are real faults; `analog` = carries values, **never** classified as faults; `unknown` = values shown in `dump`, excluded from `faults`. Absent ⇒ default DBC behavior. |
| `name` | Rename the message. |
| `replace_signals` | `true` ⇒ ignore DBC signals for this ID, use overlay `signals`. Omitted/`false` ⇒ overlay signals add to / override same-named DBC signals. |
| `signals` | Compact defs: `name, start, length, endian, scale, offset, unit`, optional `values` (enum map), optional `mux_id` / `is_muxer`. |
| `enum_fix` | Light-touch value-name corrections, e.g. `{"BCFRONT_fogLightLeftStatus": {"3": "SNA"}}`, without redefining the signal. |

## 5. Decode pipeline

`tscan/overlay.py`:
- `load_overlay(path) -> Overlay` — parse JSON; build a cantools overlay DB by
  constructing `Signal`/`Message` objects for any message with `signals`; store
  `length`, `trust`, `enum_fix`, `replace_signals` flags in a side table keyed by ID.

`DecodeEngine` (new; wraps the existing `Decoder` for the base DBC). For each frame:
1. **Base decode:** if the overlay entry has `signals` **and** `replace_signals: true`,
   decode with the overlay message; otherwise decode with the base DBC message (if any).
2. **Overlay merge:** if the entry has `signals` **without** `replace_signals`, decode
   those overlay signals as well and merge them in (overlay wins on name collision).
   An entry with only `length`/`trust`/`enum_fix` (no `signals`) keeps the DBC's signals.
3. **Length-guard (always):** effective length = `min(received_bytes, overlay.length if
   set else dbc.length)`. Drop any signal whose `start+length` exceeds the **effective
   length**. (Kills truncation garbage even for frames with no overlay entry.)
4. **`enum_fix`** applied to the decoded mapping.
5. Return `(decoded_dict, trust)` so consumers know whether faults are allowed.

`Decoder` is unchanged (base DBC loader). `DecodeEngine` composes it, so existing
behavior is preserved where no overlay entry exists.

## 6. How `dump` and `faults` consume it

- **`dump`** uses `DecodeEngine`, shows overlay-corrected values for everything,
  **ignores `trust`** (dump makes no judgment — its contract). You see the real
  `0x3F8` analog words instead of fake fault bits.
- **`faults`** uses `DecodeEngine` but **only classifies signals from frames whose
  `trust` is `faults` or which carry no `trust` tag** (the default DBC behavior for the
  ~230 frames we never touch). Frames tagged `analog` or `unknown` are skipped entirely
  — removing the analog/`0x3F8` and light false positives while keeping genuine faults
  (e.g. `f014`).

## 7. Validation strategy

Because we are *deriving* definitions, each must be defensible:
- Every overlay entry's `comment` cites its evidence (byte-variance probe, talas9
  cross-reference, physical plausibility).
- **Plausibility check** for analog signals (decoded values land in a sane physical
  range across the capture).
- Where talas9 overlaps, cross-check **values** as a reference sanity test — never
  copying its file.
- `tools/profile_frame.py` makes characterizing any future frame repeatable (length
  distribution, byte-variance, distinct payloads, DBC mux/length vs reality).

## 8. Testing

`tests/test_overlay.py` + extend `tests/test_faults.py` — synthetic frames, no
hardware, **no vehicle data committed**:
- Overlay loader parses the schema and builds cantools messages (incl. a `replace_signals`
  message and a `mux_id` signal).
- `DecodeEngine`: overlay precedence over DBC; length-guard drops an over-read signal;
  `enum_fix` applied; non-overlay IDs fall through to the DBC unchanged.
- `faults`: `analog` / `unknown` / suppressed frames produce **zero** faults; a
  known-good `trust:"faults"` coded frame still flags.
- **Regression:** synthetic `0x3F8` (4× u16 analog), `0x212` (5B), `0x232` (4B) frames
  yield **no** faults after overlay.

## 9. File structure

- **`data/overlay.json`** — the correction overlay (new).
- **`tscan/overlay.py`** — `load_overlay`, `Overlay`, `DecodeEngine` (new).
- **`tscan/faults.py`** — `active_faults` consumes `DecodeEngine` + `trust` gate.
- **`tscan/dump.py`** — `dump_signals` consumes `DecodeEngine`.
- **`tesla_scan.py`** — wire `DecodeEngine` (load overlay alongside the DBC) into
  `cmd_faults` / `cmd_dump`.
- **`tools/profile_frame.py`** — repeatable frame characterizer (new).
- **`tests/test_overlay.py`** — new; `tests/test_faults.py` — extended.

## 10. Early-warning tie-in

Correct decoding is the floor under every other pillar: a trustworthy `faults` list
(no analog-as-fault, no truncation noise) is what makes the categorical early-warning
from the richer-fault-classification work actually actionable, and gives isolation-watch
clean inputs. Coverage then grows one evidence-backed overlay entry at a time.
