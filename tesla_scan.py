#!/usr/bin/env python3
"""Tesla deep-scan suite CLI. Read-only. Subcommands: capture, faults."""
import argparse
import json
import os

from tscan.core import Decoder
from tscan.capture import parse_capture_file, capture_live
from tscan.faults import active_faults
from tscan.dump import dump_signals
from tscan.meaning import tessie_link
from tscan.trend import TrendStore

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
        print(f"        evidence: 0x{f.can_id:03X} = {f.evidence}")
        print(f"        tessie  : {tessie_link(f.signal)}\n")


def cmd_dump(args):
    decoder = Decoder(args.dbc)
    _meta, frames = parse_capture_file(args.capture)
    grouped = dump_signals(decoder, frames, module=args.module, grep=args.grep)
    if not grouped:
        print("No signals matched.")
        return
    total = sum(len(v) for v in grouped.values())
    print(f"{total} signals across {len(grouped)} module(s):\n")
    for mod in sorted(grouped):
        print(f"=== {mod} ===")
        for sig, val in grouped[mod]:
            print(f"  {sig:42s} = {val}")
        print()


def cmd_trend(args):
    store = TrendStore(args.db)
    overrides = _load_overrides(args.descriptions)
    if args.action == "ingest":
        cid = store.ingest(Decoder(args.dbc), args.capture, overrides=overrides)
        print(f"Ingested as capture id {cid}")
    elif args.action == "baseline":
        store.set_baseline(args.capture_id)
        print(f"Baseline set to capture id {args.capture_id}")
    elif args.action == "diff":
        d = store.diff(args.capture_id)
        print(f"NEW FAULTS ({len(d['new_faults'])}):")
        for f in d["new_faults"]:
            print(f"  + {f['signal']}")
        print(f"STATE CHANGES ({len(d['state_changes'])}):")
        for c in d["state_changes"]:
            print(f"  ~ {c['signal']}: {c['from']} -> {c['to']}")
        print(f"DRIFTS ({len(d['drifts'])}):")
        for dr in d["drifts"]:
            print(f"  Δ {dr['signal']}: {dr['from']} -> {dr['to']}")
    elif args.action == "history":
        for h in store.history(args.signal):
            print(f"  cap {h['capture_id']} ({h['started_at']}): "
                  f"{h['v_last']} {h['named_state'] or ''}")
    store.close()


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

    dp = sub.add_parser("dump", help="decode every signal in a capture, by module")
    dp.add_argument("capture", help="capture file path")
    dp.add_argument("--module", help="case-insensitive module label filter")
    dp.add_argument("--grep", help="case-insensitive regex on signal name")
    dp.add_argument("--dbc", default=DEFAULT_DBC)
    dp.set_defaults(func=cmd_dump)

    tr = sub.add_parser("trend", help="SQLite trend store: ingest/baseline/diff/history")
    tr.add_argument("--db", default="tesla_trend.sqlite")
    tr.add_argument("--dbc", default=DEFAULT_DBC)
    tr.add_argument("--descriptions", default=DEFAULT_OVERRIDES)
    tr.set_defaults(func=cmd_trend)
    tract = tr.add_subparsers(dest="action", required=True)
    p_ing = tract.add_parser("ingest"); p_ing.add_argument("capture")
    p_base = tract.add_parser("baseline"); p_base.add_argument("capture_id", type=int)
    p_diff = tract.add_parser("diff"); p_diff.add_argument("capture_id", type=int)
    p_hist = tract.add_parser("history"); p_hist.add_argument("signal")

    args = ap.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
