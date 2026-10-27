"""Tests for corsair-fans: pure functions and --help. Run: python3 -m pytest tests -q (no hardware needed)."""
import importlib.util, os, subprocess, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "corsair_fans.py")

def load(env=None):
    old = dict(os.environ)
    os.environ.update(env or {})
    try:
        spec = importlib.util.spec_from_file_location("corsair_fans_t", SCRIPT)
        m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
        return m
    finally:
        os.environ.clear(); os.environ.update(old)

def test_help_exits_zero_and_explains():
    r = subprocess.run([sys.executable, SCRIPT, "--help"], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0 and "Commander Core" in r.stdout

def test_duty_ramp():
    m = load()
    assert m.duty(30, 45, 65, 60) == 60 and m.duty(45, 45, 65, 60) == 60
    assert m.duty(55, 45, 65, 60) == 80 and m.duty(65, 45, 65, 60) == 100 and m.duty(99, 45, 65, 60) == 100

def test_case_fans_full_above_hard_thresholds():
    m = load()
    assert m.case_duty(41, 40, 40, 55, 60) < 100
    assert m.case_duty(56, 40, 40, 55, 60) == 100
    assert m.case_duty(30, 53, 40, 55, 60) == 100

def test_config_from_environment(tmp_path):
    m = load({"CPU_FANS": "fan1 fan2", "FAN_FLOOR_PCT": "30", "CORSAIR_FANS_CONF": str(tmp_path / "none")})
    assert m.ZONES["cpu"][0] == ["fan1", "fan2"] and m.ZONES["cpu"][3] == 30 and m.ZONES["gpu"][3] == 30

def test_liq_retries_and_reports_last_error():
    m = load()
    class R:  returncode = 1; stderr = "boom\nlast line"
    calls = []
    ok = m.liq("initialize", tries=3, run=lambda *a, **k: calls.append(1) or R(), sleep=lambda s: None)
    assert ok is False and len(calls) == 3 and m.LAST_ERR == "last line"
