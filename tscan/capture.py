"""Raw CAN capture file format (read + write) and live serial capture.

File format (CSV):
    # tesla_scan capture v1
    # adapter=... port=... protocol=... bus=... start=...
    t_ms,can_id,data_hex
    0,219,00807F0082020004
"""
import re


class CaptureEmpty(RuntimeError):
    """Raised when a live capture receives no frames within the liveness window —
    so a dead adapter/link aborts loudly instead of silently 'running' for a whole
    drive."""


def _assert_live(n_frames, liveness_secs):
    """Abort if no frames have arrived once the liveness window has elapsed."""
    if n_frames == 0:
        raise CaptureEmpty(
            f"No CAN frames received in {liveness_secs:.0f}s - the link looks dead "
            f"(adapter off / wrong port / Bluetooth dropped). Aborting so a drive "
            f"isn't wasted. Reset the adapter, or use --pcan (wired) for moving captures.")


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
        self.fh.flush()   # durable per frame: a stop/crash never loses the capture

    def __exit__(self, *exc):
        if self.fh:
            self.fh.close()


def capture_live(port, seconds, ids=None, meta=None, out_path=None, baud=115200,
                 liveness_secs=5):
    """Live serial capture using the proven STN/ELM monitor. Ports the logic from
    tesla_iso_capture.py. Writes frames to out_path. Returns out_path.
    Requires hardware; not unit-tested."""
    import time
    import serial  # pyserial

    s = None
    last = None
    for _ in range(6):
        try:
            # write_timeout: a wedged BT link can open but block writes forever;
            # cap it so we fail fast (raises) instead of hanging a whole drive.
            s = serial.Serial(port, baud, timeout=1.0, write_timeout=2.0)
            break
        except Exception as e:  # transient BT semaphore timeouts -> retry
            last = e
            time.sleep(0.9)
    if s is None:
        raise CaptureEmpty(
            f"Could not open {port} after retries ({last}). Reset the OBDLink "
            f"(unplug/replug) and re-pair Bluetooth, then retry — or use --pcan.")

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

    out_path = out_path or f"capture_{int(time.time())}.csv"
    try:
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

        s.reset_input_buffer()
        s.write((start + "\r").encode())
        t0, part, n, live_checked = time.time(), "", 0, False
        with CaptureWriter(out_path, meta) as w:
            try:
                while time.time() - t0 < seconds:
                    navail = s.in_waiting
                    if navail:
                        part += s.read(navail).decode(errors="replace")
                        lines = part.split("\r")
                        part = lines.pop()
                        for ln in lines:
                            fr = _parse_monitor_line(ln)
                            if fr:
                                w.write(int((time.time() - t0) * 1000), fr[0], fr[1])
                                n += 1
                    else:
                        time.sleep(0.02)
                    if not live_checked and (time.time() - t0) >= liveness_secs:
                        live_checked = True
                        _assert_live(n, liveness_secs)   # abort loudly on a dead link
            except KeyboardInterrupt:
                pass
    except serial.SerialException as e:
        # wedged BT link (write timed out / port error) — fail fast and clearly
        raise CaptureEmpty(
            f"Adapter stopped responding ({e}). The Bluetooth link is likely wedged "
            f"- reset the OBDLink (unplug/replug) and re-pair, then retry, or use --pcan.")
    finally:
        try:
            s.write(b"\r")
            time.sleep(0.2)
            s.close()
        except Exception:
            pass
    return out_path


def capture_pcan(seconds, channel="PCAN_USBBUS1", bitrate=500000, ids=None,
                 out_path=None, meta=None, bus=None, max_frames=None, liveness_secs=5):
    """Live capture via a PEAK PCAN interface (python-can). Hardware-buffered and
    drop-free — unlike the STN/ELM path, so slow frames (0x219, alertMatrix) and
    rare frames aren't lost. Writes the same capture-file format as the rest of
    the suite, so faults/dump/trend work on PCAN captures unchanged.

    `bus`/`max_frames` are injectable for testing without hardware. In normal use
    they're left None: a real PCAN bus is created via the installed PCAN-Basic
    driver, and capture runs for `seconds`."""
    import time
    own_bus = bus is None
    if own_bus:
        import can  # python-can; talks to the installed PCAN-Basic driver
        bus = can.Bus(interface="pcan", channel=channel, bitrate=bitrate)
    meta = dict(meta or {})
    meta.setdefault("adapter", f"PCAN {channel}")
    meta.setdefault("protocol", f"CAN-{bitrate}")
    id_set = set(int(x, 16) for x in ids) if ids else None
    out_path = out_path or f"capture_pcan_{int(time.time())}.csv"
    t0, n, live_checked = time.time(), 0, False
    try:
        with CaptureWriter(out_path, meta) as w:
            while time.time() - t0 < seconds:
                msg = bus.recv(timeout=0.5)
                if msg is not None and (id_set is None or msg.arbitration_id in id_set):
                    w.write(int((time.time() - t0) * 1000),
                            msg.arbitration_id, bytes(msg.data))
                    n += 1
                    if max_frames is not None and n >= max_frames:
                        break
                if not live_checked and (time.time() - t0) >= liveness_secs:
                    live_checked = True
                    _assert_live(n, liveness_secs)   # abort loudly on a dead link
                if msg is None and max_frames is not None:   # test/drain: source dry
                    break
    finally:
        if own_bus:
            bus.shutdown()
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
