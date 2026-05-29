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
and the `cantools` library. Contributions, corrections, and shared captures are
welcome.

## What it does (planned, phased)

A single CLI, `tesla_scan.py`, with subcommands over a shared `cantools`-backed
decode core:

- `capture` — record raw CAN frames to a timestamped, re-decodable log.
- `faults` — list active fault/alert codes with plain-language meaning + the
  module reporting them.
- `dump` — decode every signal the DBC knows, grouped by module/ECU.
- `trend` — store captures in SQLite and diff against a baseline to flag new
  faults and signal drift over time.

See `docs/superpowers/specs/` for the design.

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
