# TeslaDiagScripts

*Searching the muddy waters of Tesla diagnostics.*

A set of open tools for reading and explaining the **deep diagnostic data**
inside Tesla vehicles — the sensor values, ECU/BMS state, and fault codes that
sit below the surface layer most consumer apps expose. The goal is to
**contribute to the community's work toward building diagnostic capabilities for
Tesla owners**: helping owners understand what their car is actually reporting,
localize a failing sensor, and track developing issues before they become a
no-start in the driveway.

This grew out of a real diagnosis of a 2016 Model X P90D and is decoded against
the community-maintained S/X DBC. It builds on a lot of prior community
effort — ScanMyTesla, the DBC reverse-engineering community, the Tesla Motors
Club / Tesla Owners Online diagnostic-port threads, the OpenVehicles project,
[Tessie's alert directory](https://stats.tessie.com/alerts), and the `cantools`
library. Contributions, corrections, and shared captures are welcome.

## Status

A single CLI, `tesla_scan.py`, with subcommands over a shared `cantools`-backed
decode core. Implemented and unit-tested (15 tests, no vehicle required):

- **`capture`** — record raw CAN frames to a timestamped, re-decodable log
  (full-bus, or hardware-filtered to specific IDs on a genuine STN adapter).
- **`faults`** — list active fault/alert codes with plain-language meaning, the
  module reporting them, and a link out to [Tessie's alert page](https://stats.tessie.com/alerts)
  for each code's authoritative description (we link, never scrape).
- **`dump`** — decode every signal the DBC knows in a capture, grouped by
  module/ECU (`--module`, `--grep` filters). ~2,000 signals came out of a 45 s
  capture on a 2016 Model X — vs the ~300 typical surface apps show.

Planned / not yet built:

- **`trend`** — store captures in SQLite and diff against a baseline to flag new
  faults and signal drift over time (design in `docs/superpowers/specs/`).
- **actions / `clear-dtc`** — an opt-in command-sending capability, developed on
  a separate experimental branch (see Safety & scope). Not on `main`.

See `docs/superpowers/specs/` and `docs/superpowers/plans/` for design + plan.

## Usage

```bash
pip install -r requirements.txt

# capture raw frames (read-only; one app may hold the adapter at a time)
python tesla_scan.py capture --port COM5 --secs 60 --out captures/run1.csv
# slow/targeted: hardware-filter to specific 11-bit IDs (low frame loss)
python tesla_scan.py capture --port COM5 --secs 150 --ids 219,021,061 --out captures/iso.csv

# active fault/alert codes from a capture
python tesla_scan.py faults captures/run1.csv

# every decoded signal, grouped by module (optionally filtered)
python tesla_scan.py dump captures/run1.csv
python tesla_scan.py dump captures/run1.csv --module "Battery"
python tesla_scan.py dump captures/run1.csv --grep isolation
```

Curate human descriptions in `data/descriptions.json` (override key = exact
signal name). Captures live under `captures/` and are git-ignored — no vehicle
data is committed.

## Known limitations (read before trusting output)

Early, honest work — corrections welcome:

- **`faults` over-reports.** It flags every non-zero `_w/f/u###_` bit plus a
  small state watch-list. That includes chronic version/config flags and
  permission/status bits (e.g. `noChargeAllowed` when parked), so the raw list
  mixes real faults with noise. Each code links out to Tessie for triage in the
  meantime; the planned fix is a **relevance layer ranking by Tessie fleet
  incidence rate** (a `<0.01%` code is genuinely rare/significant; a multi-percent
  one is fleet noise).
- **Module map is partial.** Unmapped name prefixes show as `Unknown (PREFIX)`.
- **DBC vs firmware gaps.** The community DBC lags some firmware: e.g.
  `BMS_f027` is the real-world drive-unit isolation fault (`SW_Drive_Iso`) but is
  labeled `Unused_27` in the DBC. The DBC also contains a malformed message
  (`BCCEN_udsResponse`), so it is loaded `strict=False`.
- **Frame-length mismatches.** Some frames arrive shorter than the DBC declares;
  decoding tolerates truncation, but a guard is on the roadmap.
- **Single bus.** Only the CAN pair the OBD adapter bridges is visible; signals
  on other buses won't appear.

## Safety & scope

- **The published suite is read-only.** `capture`, `faults`, `dump`, and
  `trend` are passive monitors — they never send commands that change vehicle
  state.
- Any command-sending capability (e.g. clearing stored DTCs) is developed in
  isolation on a **separate experimental branch**, is strictly opt-in, requires
  explicit per-action confirmation, logs every request/response, and never
  attempts security-access bypass.
- High-voltage systems are dangerous. This software is provided as-is, for
  research and owner self-diagnosis, with **no warranty**. Not affiliated with
  or endorsed by Tesla, Inc. Use at your own risk.

## License

MIT — see `LICENSE`.
