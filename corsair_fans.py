#!/usr/bin/env python3
"""corsair-fans: temperature-driven fan zones for a Corsair Commander Core, as a user service (no root needed).

Two zones, set through liquidctl:
  cpu  fans (default fan1 fan2 fan6, radiator/CPU cooling) follow the CPU temperature (AMD Tctl or Intel package);
  case fans (default fan3 fan4 fan5) follow max(hottest NVIDIA GPU, RAM), RAM from the spd5118 hwmon sensors.
Each zone ramps linearly from FLOOR percent at its low temperature to 100 % at its high temperature. Any failed read counts as
hot (99 C), so a broken sensor means loud fans, not silent ones. The pump is never touched. A target is re-applied every
5 s only when it changes by at least 3 %, or every 60 s as a keep-alive so the hub stays in software mode. If liquidctl fails
3 times in a row an ALERT line is written (at most once a day), because losing fan control silently is dangerous.

Configuration: environment, then the KEY=VALUE file $CORSAIR_FANS_CONF (default ~/.config/corsair-fans/corsair-fans.conf).
See etc/corsair-fans.conf.example for every key and its default."""
import glob, os, re, subprocess, sys, time

HOME = os.path.expanduser("~")
CONF_FILE = os.environ.get("CORSAIR_FANS_CONF", os.path.join(os.environ.get("XDG_CONFIG_HOME", HOME + "/.config"), "corsair-fans/corsair-fans.conf"))

def _file_conf(path=CONF_FILE):
    res = {}
    try:
        for ln in open(path):
            a, sep, b = ln.split("#")[0].partition("=")
            a = a.strip()
            if sep and re.fullmatch(r"[A-Z][A-Z0-9_]*", a): res[a] = b.strip().strip("\"'")
    except OSError: pass
    return res

_FC = _file_conf()
def cfg(key, default):
    return os.environ.get(key, _FC.get(key, default))

def cfg_num(key, default):
    try: return float(cfg(key, str(default)))
    except ValueError: return float(default)

LIQ = cfg("LIQUIDCTL", "liquidctl")
MATCH = cfg("LIQUIDCTL_MATCH", "Commander")
FLOOR = cfg_num("FAN_FLOOR_PCT", 60)
# zone: (fans, low temperature C, high temperature C, floor percent)
ZONES = {
    "cpu": (cfg("CPU_FANS", "fan1 fan2 fan6").split(), cfg_num("CPU_LOW_C", 45), cfg_num("CPU_HIGH_C", 65), FLOOR),
    "gpu": (cfg("CASE_FANS", "fan3 fan4 fan5").split(), cfg_num("CASE_LOW_C", 40), cfg_num("CASE_HIGH_C", 55), FLOOR),
}
RAM_LOW, RAM_HIGH = cfg_num("RAM_LOW_C", 45), cfg_num("RAM_HIGH_C", 55)
CASE_FULL_GPU_C, CASE_FULL_RAM_C = cfg_num("CASE_FULL_GPU_C", 55), cfg_num("CASE_FULL_RAM_C", 52)  # case fans go to 100 % above these
STATE_DIR = cfg("CORSAIR_FANS_STATE_DIR", HOME + "/.local/state/corsair-fans")
ALERT_FILE = cfg("ALERT_FILE", STATE_DIR + "/alerts.log")
HANDOFF_LOCK = cfg("GPU_HANDOFF_LOCK", "")  # optional lock file held while a GPU moves between host and VM
INTERVAL = cfg_num("INTERVAL_S", 5)

def cpu_t():
    """AMD Tctl, else Intel 'Package id 0', from hwmon; 99.0 (hot) if unreadable."""
    for l in glob.glob("/sys/class/hwmon/hwmon*/temp*_label"):
        try:
            if open(l).read().strip() in ("Tctl", "Package id 0"):
                return int(open(l.replace("_label", "_input")).read()) / 1000
        except (OSError, ValueError): pass
    return 99.0

def _gpu_busy():
    """True while the handoff lock (a card moving between host and VM) is held. Host nvidia-smi queries are skipped then."""
    import fcntl
    if not HANDOFF_LOCK or not os.path.exists(HANDOFF_LOCK): return False
    try:
        with open(HANDOFF_LOCK) as f:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(f, fcntl.LOCK_UN)
        return False
    except OSError:
        return True

_LAST_GPU = [99.0]
def gpu_t():
    # With no card on the nvidia driver, nvidia-smi only makes the kernel retry the module load and spins udevd; keep the last value.
    if _gpu_busy() or not glob.glob("/sys/bus/pci/drivers/nvidia/0000:*"): return _LAST_GPU[0]
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=temperature.gpu", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=10).stdout.split()
        _LAST_GPU[0] = max(float(x) for x in out) if out else 99.0
        return _LAST_GPU[0]
    except Exception:
        return 99.0

def ram_t():
    try:
        v = [int(open(d + "/temp1_input").read()) / 1000 for d in glob.glob("/sys/class/hwmon/hwmon*")
             if open(d + "/name").read().strip() == "spd5118"]
        return max(v) if v else 99.0
    except Exception:
        return 99.0

def duty(t, lo, hi, floor):
    """Fan percent for temperature t: floor at or below lo, 100 at or above hi, linear between."""
    if t <= lo: return floor
    if t >= hi: return 100
    return round(floor + (100 - floor) * (t - lo) / (hi - lo))

LAST_ERR = ""

def liq(*a, tries=3, pause=1.0, run=subprocess.run, sleep=time.sleep):
    """Run liquidctl; keep stderr (last line in LAST_ERR), retry up to `tries`, never raise."""
    global LAST_ERR
    for i in range(tries):
        try:
            r = run([LIQ, "--match", MATCH, *a], capture_output=True, text=True, timeout=15)
            if r.returncode == 0:
                return True
            lines = (r.stderr or "").strip().splitlines()
            LAST_ERR = lines[-1] if lines else "rc=%s" % r.returncode
        except subprocess.TimeoutExpired:
            LAST_ERR = "timeout after 15s"
        except Exception as e:
            LAST_ERR = repr(e)
        if i < tries - 1:
            sleep(pause)
    return False

def alert(msg):
    os.makedirs(os.path.dirname(ALERT_FILE), exist_ok=True)
    with open(ALERT_FILE, "a") as f:
        f.write(time.strftime("%Y-%m-%d %H:%M:%S") + " ALERT " + msg + "\n")

def case_duty(gpu_c, ram_c, lo, hi, floor):
    """Case-fan percent: the larger of the GPU ramp and the RAM ramp, full speed above the hard thresholds."""
    d = max(duty(gpu_c, lo, hi, floor), duty(ram_c, RAM_LOW, RAM_HIGH, floor))
    if gpu_c > CASE_FULL_GPU_C or ram_c > CASE_FULL_RAM_C: d = 100
    return d

def main():
    if len(sys.argv) > 1 and sys.argv[1] in ("-h", "--help"):
        print(__doc__); return 0
    liq("initialize")
    last, stamp, fails, alerted = {}, 0.0, 0, 0.0
    while True:
        temps = {"cpu": cpu_t(), "gpu": gpu_t()}
        keep = time.time() - stamp > 60
        for z, (fans, lo, hi, floor) in ZONES.items():
            d = duty(temps[z], lo, hi, floor) if z == "cpu" else case_duty(temps["gpu"], ram_t(), lo, hi, floor)
            if keep or abs(d - last.get(z, -99)) >= 3:
                ok = all(liq("set", f, "speed", str(d)) for f in fans)
                if ok: last[z] = d; fails = 0
                else:
                    fails += 1
                    if fails >= 3 and time.time() - alerted > 86400:
                        alert("corsair-fans cannot set fans (liquidctl failing: " + LAST_ERR + "); fans not under software control")
                        alerted = time.time(); liq("initialize")
        if keep: stamp = time.time()
        time.sleep(INTERVAL)

if __name__ == "__main__":
    sys.exit(main())
