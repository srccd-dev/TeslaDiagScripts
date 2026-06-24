# tests/test_cli_overlay.py
import subprocess
import sys
import os

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _write_capture(tmp_path):
    p = tmp_path / "cap.csv"
    p.write_text(
        "# tesla_scan capture v1\n"
        "t_ms,can_id,data_hex\n"
        "0,3F8,FB02FA02F702FD02\n",
        encoding="utf-8")
    return str(p)


def test_cli_faults_uses_overlay(tmp_path):
    cap = _write_capture(tmp_path)
    out = subprocess.run([sys.executable, "tesla_scan.py", "faults", cap],
                         cwd=REPO, capture_output=True, text=True)
    assert out.returncode == 0
    assert "DCDC_alertMatrix1" not in out.stdout       # bogus matrix faults gone
    assert "DCDC_w00" not in out.stdout


def test_cli_dump_shows_overlay_signals(tmp_path):
    cap = _write_capture(tmp_path)
    out = subprocess.run([sys.executable, "tesla_scan.py", "dump", cap, "--grep", "DuctSns"],
                         cwd=REPO, capture_output=True, text=True)
    assert out.returncode == 0
    assert "RCCM_LeftVentDuctSnsRaw_DegC" in out.stdout
