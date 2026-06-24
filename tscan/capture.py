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


# ELM327 CAN protocols to try on a Tesla diagnostic bus, in order. Tesla almost
# always answers on 6 (ISO 15765-4, 11-bit / 500k); the rest are fallbacks.
_CAN_PROTOCOLS = ("6", "7", "8", "9")


def _elm_cmd(s, text, settle=0.4, read_bytes=4096):
    """Send an AT/ST command, wait a fixed `settle`, return the decoded reply.

    Mirrors the proven tesla_obd_capture.py timing (write -> fixed sleep -> bulk
    read). This connected reliably where our earlier '>'-prompt-polling read did
    not — the OBDLink in raw mode doesn't always emit a clean prompt to poll on."""
    import time
    s.reset_input_buffer()
    s.write((text + "\r").encode())
    time.sleep(settle)
    return s.read(read_bytes).decode(errors="replace")


def _elm_connect(s):
    """Full-reset the adapter and CONFIRM the ELM327 banner before anything else.

    A bare CR halts any monitor mode left running, then ATZ resets; some clones
    answer only on the second reset, so we retry once — this double-ATZ is the
    'fails first, connects on retry' behaviour. Crucially the banner check proves
    the Bluetooth link can both write AND read, so a dead/wedged link is caught
    HERE at connect, not silently mid-drive. Returns the banner line."""
    import time
    s.write(b"\r")
    time.sleep(0.3)
    s.reset_input_buffer()
    resp = _elm_cmd(s, "ATZ", settle=2.0)
    if "ELM327" not in resp.upper():
        resp = _elm_cmd(s, "ATZ", settle=2.0)   # some clones answer only on 2nd reset
    if "ELM327" not in resp.upper():
        raise CaptureEmpty(
            f"Adapter did not return an ELM327 banner (got {resp.strip()!r}). "
            f"The OBDLink isn't responding - reset it (unplug/replug) and re-pair "
            f"Bluetooth, then retry, or use --pcan.")
    lines = [ln.strip() for ln in resp.splitlines() if ln.strip()]
    banner = next((ln for ln in lines if "ELM327" in ln.upper()), lines[-1] if lines else "")
    return banner[:40]


def _detect_protocol(s, probe_secs=3.0):
    """Try each CAN protocol briefly; keep the first that carries live frames.

    Mirrors tesla_obd_capture.py's auto-detect. Returns as soon as a real frame
    is seen (so the common case — Tesla on protocol 6 — is fast). Raises
    CaptureEmpty if every protocol is silent (car asleep / port unpowered)."""
    import time
    for proto in _CAN_PROTOCOLS:
        _elm_cmd(s, "ATSP" + proto)
        s.reset_input_buffer()
        s.write(b"ATMA\r")
        t0, part, seen = time.time(), "", False
        while time.time() - t0 < probe_secs and not seen:
            n = s.in_waiting
            if n:
                part += s.read(n).decode(errors="replace")
                lines = part.split("\r")
                part = lines.pop()
                for ln in lines:
                    if _parse_monitor_line(ln):
                        seen = True
                        break
            else:
                time.sleep(0.02)
        s.write(b"\r")            # any byte halts ATMA
        time.sleep(0.2)
        s.reset_input_buffer()
        if seen:
            return proto
    raise CaptureEmpty(
        "Connected to the adapter but saw no CAN frames on any protocol. The bus "
        "looks idle (car asleep) or the diagnostic port is unpowered - wake the "
        "vehicle and retry.")


def capture_live(port, seconds, ids=None, meta=None, out_path=None, baud=115200,
                 liveness_secs=5):
    """Live serial capture via an STN/ELM adapter (e.g. OBDLink LX).

    Connect/init/auto-detect logic is ported from the empirically-reliable
    tesla_obd_capture.py: full ATZ reset with banner verification, no
    write_timeout (a short one fired spuriously on healthy-but-slow BT writes),
    fixed-settle commands, and CAN-protocol auto-detection. On top of that we
    keep the suite's improvements: stream to the capture file with a per-frame
    flush (a stop/crash/sleep loses nothing), an optional --ids hardware filter,
    and a liveness abort. Writes frames to out_path; returns out_path.
    Requires hardware; not unit-tested."""
    import time
    import serial  # pyserial

    s = None
    last = None
    for _ in range(6):
        try:
            # No write_timeout: the proven tesla_obd_capture.py blocks on writes
            # and connects reliably; our earlier 5s write_timeout aborted healthy
            # BT writes with "Write timeout". A dead link is instead caught by the
            # banner check (_elm_connect) and the liveness check below.
            s = serial.Serial(port, baud, timeout=1.0)
            break
        except Exception as e:  # transient BT semaphore timeouts -> retry
            last = e
            time.sleep(0.9)
    if s is None:
        raise CaptureEmpty(
            f"Could not open {port} after retries ({last}). Reset the OBDLink "
            f"(unplug/replug) and re-pair Bluetooth, then retry — or use --pcan.")

    out_path = out_path or f"capture_{int(time.time())}.csv"
    try:
        banner = _elm_connect(s)              # reset + verify ELM327 banner
        for c in ("ATE0", "ATL0", "ATS1", "ATH1", "ATCAF0"):
            _elm_cmd(s, c)                     # echo off, LF off, spaces on, headers on, raw
        meta = dict(meta or {})
        meta.setdefault("adapter", banner)
        meta.setdefault("port", port)

        proto = _detect_protocol(s)           # pick the protocol carrying live traffic
        meta.setdefault("protocol", f"ATSP{proto}")
        _elm_cmd(s, "ATSP" + proto)

        start = "ATMA"
        if ids:
            _elm_cmd(s, "STFAC")
            for cid in ids:
                _elm_cmd(s, f"STFAP {cid},7FF")
            start = "STM"

        def _arm():
            s.reset_input_buffer()
            s.write((start + "\r").encode())

        _arm()
        t0 = time.time()
        part, n, live_checked, last_rx = "", 0, False, time.time()
        STALL = 2.0   # seconds of silence -> re-arm (also re-arm immediately on BUFFER FULL)
        with CaptureWriter(out_path, meta) as w:
            try:
                while time.time() - t0 < seconds:
                    navail = s.in_waiting
                    halt = False
                    if navail:
                        part += s.read(navail).decode(errors="replace")
                        lines = part.split("\r")
                        part = lines.pop()
                        for ln in lines:
                            if "BUFFER FULL" in ln.upper() or "STOPPED" in ln.upper():
                                halt = True   # adapter buffer overran on a busy bus
                                continue
                            fr = _parse_monitor_line(ln)
                            if fr:
                                w.write(int((time.time() - t0) * 1000), fr[0], fr[1])
                                n += 1
                                last_rx = time.time()
                    else:
                        time.sleep(0.02)
                    # On a full-rate bus the ELM monitor buffer-fills in a fraction of
                    # a second and stops; re-arm so a drive keeps flowing (rolling
                    # bursts) instead of dying after the first burst. Filtered captures
                    # that fit under the BT ceiling never hit this.
                    if halt or (time.time() - last_rx) > STALL:
                        _arm()
                        last_rx = time.time()
                        part = ""
                    if not live_checked and (time.time() - t0) >= liveness_secs:
                        live_checked = True
                        _assert_live(n, liveness_secs)   # abort loudly on a dead link
            except KeyboardInterrupt:
                pass
    except serial.SerialException as e:
        # port error mid-capture — fail fast and clearly
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
