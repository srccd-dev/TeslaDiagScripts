"""Raw CAN capture file format (read + write) and live serial capture.

File format (CSV):
    # tesla_scan capture v1
    # adapter=... port=... protocol=... bus=... start=...
    t_ms,can_id,data_hex
    0,219,00807F0082020004
"""
import re


def parse_capture_file(path):
    """Return (meta: dict, frames: list[(t_ms:int, can_id:int, data:bytes)])."""
    meta, frames = {}, []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            if line.startswith("#"):
                _merge_spaced_meta(line, meta)
                continue
            if line.startswith("t_ms"):
                continue
            parts = line.split(",")
            if len(parts) != 3:
                continue
            t_ms = int(parts[0])
            can_id = int(parts[1], 16)
            data = bytes.fromhex(parts[2])
            frames.append((t_ms, can_id, data))
    return meta, frames


def _merge_spaced_meta(line, meta):
    """Parse a '# key=value key=value' line where values may contain spaces, e.g.
    'adapter=STN1155 v5.6.19 port=COM5'. Each value runs until the next 'key='."""
    body = line.lstrip("#").strip()
    keys = list(re.finditer(r"(\w+)=", body))
    for i, m in enumerate(keys):
        k = m.group(1)
        start = m.end()
        end = keys[i + 1].start() if i + 1 < len(keys) else len(body)
        meta[k] = body[start:end].strip()
