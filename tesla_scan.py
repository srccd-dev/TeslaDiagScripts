#!/usr/bin/env python3
"""Tesla deep-scan suite CLI. Read-only. Subcommands: capture, faults."""
import argparse
import json
import os

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
