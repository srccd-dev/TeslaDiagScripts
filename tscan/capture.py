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


class CaptureWriter:
    """Write raw frames in the capture file format."""

    def __init__(self, path, meta=None):
        self.path = path
        self.meta = meta or {}
        self.fh = None

    def __enter__(self):
        self.fh = open(self.path, "w", encoding="utf-8")
        self.fh.write("# tesla_scan capture v1\n")
        if self.meta:
            kv = " ".join(f"{k}={v}" for k, v in self.meta.items())
            self.fh.write(f"# {kv}\n")
        self.fh.write("t_ms,can_id,data_hex\n")
        return self

    def write(self, t_ms, can_id, data):
        self.fh.write(f"{t_ms},{can_id:03X},{data.hex().upper()}\n")

    def __exit__(self, *exc):
        if self.fh:
            self.fh.close()


def capture_live(port, seconds, ids=None, meta=None, out_path=None, baud=115200):
    """Live serial capture using the proven STN/ELM monitor. Ports the logic from
    tesla_iso_capture.py. Writes frames to out_path. Returns out_path.
    Requires hardware; not unit-tested."""
    import time
    import serial  # pyserial

    s = None
    last = None
    for _ in range(6):
        try:
            s = serial.Serial(port, baud, timeout=1.0)
            break
        except Exception as e:  # transient BT semaphore timeouts -> retry
            last = e
            time.sleep(0.9)
    if s is None:
        raise last

    def cmd(c, read_for=1.2):
        s.reset_input_buffer()
        s.write((c + "\r").encode())
        time.sleep(0.08)
        buf, t0 = b"", time.time()
        while time.time() - t0 < read_for:
            n = s.in_waiting
            if n:
                buf += s.read(n)
                if b">" in buf:
                    break
            else:
                time.sleep(0.03)
        return buf.decode(errors="replace").replace("\r", " ").replace(">", "").strip()

    for c in ("ATWS", "ATE0", "ATL0", "ATS1", "ATH1", "ATCAF0", "ATSP6"):
        cmd(c)
    adapter = cmd("STI")[:40]
    meta = dict(meta or {})
    meta.setdefault("adapter", adapter)
    meta.setdefault("port", port)

    start = "ATMA"
    if ids:
        cmd("STFAC")
        for cid in ids:
            cmd(f"STFAP {cid},7FF")
        start = "STM"

    out_path = out_path or f"capture_{int(time.time())}.csv"
    s.reset_input_buffer()
    s.write((start + "\r").encode())
    t0, part = time.time(), ""
    with CaptureWriter(out_path, meta) as w:
        try:
            while time.time() - t0 < seconds:
                n = s.in_waiting
                if n:
                    part += s.read(n).decode(errors="replace")
                    lines = part.split("\r")
                    part = lines.pop()
                    for ln in lines:
                        fr = _parse_monitor_line(ln)
                        if fr:
                            w.write(int((time.time() - t0) * 1000), fr[0], fr[1])
                else:
                    time.sleep(0.02)
        except KeyboardInterrupt:
            pass
    s.write(b"\r")
    time.sleep(0.2)
    s.close()
    return out_path


def _parse_monitor_line(ln):
    """Parse an ATH1+ATS1 monitor line 'ID b0 b1 ...' -> (can_id:int, data:bytes)."""
    u = ln.strip().upper()
    if not u or any(k in u for k in ("OK", "STOPPED", "BUFFER", "SEARCHING",
                                     "STM", "ATMA", "?", "ERROR", "NO DATA")):
        return None
    toks = u.split()
    if len(toks) < 2:
        return None
    cid = toks[0]
    if len(cid) not in (3, 4) or any(c not in "0123456789ABCDEF" for c in cid):
        return None
    try:
        data = bytes(int(t, 16) for t in toks[1:] if len(t) == 2)
    except ValueError:
        return None
    return (int(cid, 16), data)
