#!/usr/bin/env python3
"""Tesla deep-scan suite CLI. Read-only. Subcommands: capture, faults."""
import argparse
import json
import os

from tscan.core import Decoder
from tscan.capture import parse_capture_file, capture_live, capture_pcan, CaptureEmpty
from tscan.faults import active_faults
from tscan.dump import dump_signals
from tscan.meaning import tessie_link
from tscan.trend import TrendStore
from tscan.overlay import load_overlay, DecodeEngine
from tscan.hvac import ac_health, format_ac_health

REPO = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DBC = os.path.join(REPO, "data", "tesla_models.dbc")
DEFAULT_OVERRIDES = os.path.join(REPO, "data", "descriptions.json")
DEFAULT_OVERLAY = os.path.join(REPO, "data", "overlay.json")


def _load_overrides(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


_SEV_ORDER = {"CRITICAL": 0, "WARNING": 1, "STATUS": 2}


def cmd_faults(args):
    engine = DecodeEngine(Decoder(args.dbc), load_overlay(args.overlay))
    overrides = _load_overrides(args.descriptions)
    _meta, frames = parse_capture_file(args.capture)
    faults = active_faults(engine, frames, overrides=overrides)
    if not faults:
        print("No active fault/alert codes in this capture.")
        return
    counts = {}
    for f in faults:
        counts[f.severity] = counts.get(f.severity, 0) + 1
    summary = ", ".join(f"{counts.get(s, 0)} {s}" for s in ("CRITICAL", "WARNING", "STATUS"))
    print(f"{len(faults)} active code(s) - {summary}:\n")
    for f in sorted(faults, key=lambda x: (_SEV_ORDER.get(x.severity, 9), x.module, x.signal)):
        code = f" {f.code}" if f.code else ""   # empty for enum-faults (klass already shown)
        state = f" {f.state}" if f.state else ""
        print(f"  [{f.severity}] {f.klass}{code}{state}  {f.signal}  ({f.module})")
        print(f"        meaning : {f.meaning}")
        print(f"        evidence: 0x{f.can_id:03X} = {f.evidence}")
        print(f"        tessie  : {tessie_link(f.signal)}\n")


def cmd_dump(args):
    engine = DecodeEngine(Decoder(args.dbc), load_overlay(args.overlay))
    _meta, frames = parse_capture_file(args.capture)
    # Derived A/C-health summary: only on an unfiltered dump, and only when the
    # capture actually carries the HVAC signals it needs.
    if not args.module and not args.grep:
        metrics = ac_health(engine, frames)
        if metrics:
            print(format_ac_health(metrics) + "\n")
    grouped = dump_signals(engine, frames, module=args.module, grep=args.grep)
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
    try:
        if args.pcan:
            out = capture_pcan(args.secs, channel=args.channel, bitrate=args.bitrate,
                               ids=ids, out_path=args.out)
        else:
            if not args.port:
                raise SystemExit("--port is required for the STN/ELM serial capture "
                                 "(or pass --pcan for a PEAK PCAN interface)")
            out = capture_live(args.port, args.secs, ids=ids, out_path=args.out)
    except CaptureEmpty as e:
        raise SystemExit(f"Capture aborted: {e}")
    print(f"Capture written to {out}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Tesla deep-scan suite (read-only)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("capture", help="record raw CAN frames to file")
    c.add_argument("--port", help="serial port for STN/ELM adapter, e.g. COM5")
    c.add_argument("--pcan", action="store_true",
                   help="use a PEAK PCAN interface (drop-free) instead of STN/ELM")
    c.add_argument("--channel", default="PCAN_USBBUS1", help="PCAN channel")
    c.add_argument("--bitrate", type=int, default=500000, help="PCAN bitrate")
    c.add_argument("--secs", type=int, default=60)
    c.add_argument("--ids", help="comma-separated 11-bit hex IDs to pass-filter")
    c.add_argument("--out", help="output capture file path")
    c.set_defaults(func=cmd_capture)

    f = sub.add_parser("faults", help="list active fault/alert codes from a capture")
    f.add_argument("capture", help="capture file path")
    f.add_argument("--dbc", default=DEFAULT_DBC)
    f.add_argument("--descriptions", default=DEFAULT_OVERRIDES)
    f.add_argument("--overlay", default=DEFAULT_OVERLAY)
    f.set_defaults(func=cmd_faults)

    dp = sub.add_parser("dump", help="decode every signal in a capture, by module")
    dp.add_argument("capture", help="capture file path")
    dp.add_argument("--module", help="case-insensitive module label filter")
    dp.add_argument("--grep", help="case-insensitive regex on signal name")
    dp.add_argument("--dbc", default=DEFAULT_DBC)
    dp.add_argument("--overlay", default=DEFAULT_OVERLAY)
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
