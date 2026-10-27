#!/usr/bin/env python3
"""Local-only hardware monitor for Debian. Uses only Python's standard library."""
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
import json, os, re, shutil, subprocess, time, threading
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
PORT = int(os.environ.get("THERMAL_PORT", "8765"))
HOST = os.environ.get("THERMAL_HOST", "127.0.0.1")
INTERVAL = max(1, int(os.environ.get("THERMAL_INTERVAL", "2")))
HISTORY_SECONDS = max(60, int(os.environ.get("THERMAL_HISTORY_SECONDS", "3600")))
history = []
history_lock = threading.Lock()

def run_cmd(args, timeout=2):
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
        if p.returncode == 0:
            return p.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None

def read_text(path):
    try:
        return Path(path).read_text().strip()
    except (OSError, UnicodeError):
        return None

def number(path, divisor=1.0):
    value = read_text(path)
    if value is None:
        return None
    try:
        return round(float(value) / divisor, 2)
    except ValueError:
        return None

def label_from_hwmon(path, fallback):
    name = read_text(Path(path) / "name")
    return name or fallback

def scan_hwmon():
    sensors = []
    root = Path("/sys/class/hwmon")
    if not root.exists():
        return sensors
    for hwmon in sorted(root.glob("hwmon*")):
        chip = label_from_hwmon(hwmon, hwmon.name)
        for f in sorted(hwmon.glob("temp*_input")):
            raw = number(f)
            if raw is None:
                continue
            # Linux hwmon temperature inputs are conventionally millidegrees C.
            temp = raw / 1000.0
            if temp < -40 or temp > 250:
                continue
            stem = f.name[:-6]  # temp1
            label = read_text(hwmon / f"{stem}_label")
            label = label or stem.replace("temp", "Sensor ")
            sensors.append({
                "id": f"{hwmon.name}:{stem}", "name": f"{chip} · {label}",
                "temperature": round(temp, 1), "source": "hwmon",
                "chip": chip, "sensor": label
            })
    return sensors

def cpu_info(hw_sensors):
    cpu_sensors = [s for s in hw_sensors if any(k in (s["chip"]+" "+s["sensor"]).lower()
                   for k in ("k10temp", "tctl", "tdie", "cpu", "core"))]
    # Prefer AMD Tctl/Tdie/package-like readings, but preserve all discoverable CPU sensors.
    cpu_temp = None
    preferred = [s for s in cpu_sensors if any(k in (s["chip"]+" "+s["sensor"]).lower()
                for k in ("tctl", "tdie", "package", "cpu"))]
    candidates = preferred or cpu_sensors
    if candidates:
        cpu_temp = max(candidates, key=lambda x: x["temperature"])["temperature"]
    # lm-sensors fallback, useful on systems where readings are not exposed in hwmon.
    if cpu_temp is None and shutil.which("sensors"):
        output = run_cmd(["sensors", "-A"])
        if output:
            vals = []
            for line in output.splitlines():
                if re.search(r"(Tctl|Tdie|Package id 0|CPU Temperature)", line, re.I):
                    m = re.search(r"([+-]?\d+(?:\.\d+)?)\s*°?C", line)
                    if m: vals.append(float(m.group(1)))
            if vals: cpu_temp = round(max(vals), 1)
    return cpu_temp

def read_amd_gpu():
    """Use AMD GPU hwmon values; report power/utilization only when exposed."""
    gpus = []
    drm = Path("/sys/class/drm")
    if not drm.exists():
        return gpus
    seen = set()
    for card in sorted(drm.glob("card[0-9]*")):
        if not re.fullmatch(r"card\d+", card.name):
            continue
        device = card / "device"
        vendor = read_text(device / "vendor")
        if vendor and vendor.lower() != "0x1002":
            continue
        real = None
        try: real = str(device.resolve())
        except OSError: real = str(device)
        if real in seen: continue
        seen.add(real)
        name = read_text(device / "product_name") or read_text(device / "uevent") or card.name
        # Keep product_name if present, otherwise avoid presenting the whole uevent as a model.
        if name and "\n" in name:
            match = re.search(r"DRIVER=(\S+)", name)
            name = f"AMD GPU ({match.group(1) if match else 'amdgpu'})"
        hwmons = list((device / "hwmon").glob("hwmon*"))
        temps, fans, power = [], [], []
        for hw in hwmons:
            chip = read_text(hw / "name") or "amdgpu"
            for f in hw.glob("temp*_input"):
                v = number(f, 1000)
                if v is not None and -40 <= v <= 250:
                    stem = f.name[:-6]
                    lab = read_text(hw / f"{stem}_label") or stem
                    temps.append({"name": lab, "temperature": round(v, 1), "chip": chip})
            for f in hw.glob("fan*_input"):
                v = number(f)
                if v is not None and v >= 0:
                    fans.append({"name": f.name.replace("_input", ""), "rpm": round(v)})
            for f in hw.glob("power*_average"):
                v = number(f, 1_000_000)
                if v is not None and v >= 0:
                    power.append(round(v, 2))
            for f in hw.glob("power*_input"):
                v = number(f, 1_000_000)
                if v is not None and v >= 0:
                    power.append(round(v, 2))
        # Optional ROCm utility. Do not depend on it.
        util = None
        if shutil.which("rocm-smi"):
            out = run_cmd(["rocm-smi", "--showuse"], timeout=3)
            if out:
                m = re.search(r"GPU use\s*:\s*(\d+)\s*%", out, re.I)
                if m: util = int(m.group(1))
        gpus.append({
            "name": name or "AMD GPU",
            "temperatures": temps, "fans": fans,
            "power_w": power[0] if power else None, "utilization_percent": util,
            "detected": bool(temps or fans or power or vendor == "0x1002"),
            "note": None if temps else "GPU found, but no temperature sensor is exposed through sysfs."
        })
    return gpus

def storage_info():
    drives = []
    # NVMe sysfs temperatures
    for hw in sorted(Path("/sys/class/nvme").glob("nvme*")):
        if not re.fullmatch(r"nvme\d+", hw.name): continue
        model = read_text(hw / "model") or hw.name
        temp = None
        # Device hwmon is commonly linked from the controller.
        for hm in (hw / "device" / "hwmon").glob("hwmon*"):
            for f in hm.glob("temp*_input"):
                temp = number(f, 1000)
                if temp is not None: break
            if temp is not None: break
        if temp is None:
            for f in (hw / "device").glob("hwmon*/temp*_input"):
                temp = number(f, 1000)
                if temp is not None: break
        drives.append({"name": model.strip(), "temperature": round(temp, 1) if temp is not None else None,
                       "type": "NVMe", "source": "sysfs"})
    # SATA/SAS SMART fallback: smartctl needs permissions for some devices.
    if shutil.which("smartctl"):
        lsblk = run_cmd(["lsblk", "-dnpo", "NAME,TYPE,MODEL"])
        if lsblk:
            for line in lsblk.splitlines():
                parts = line.split(None, 2)
                if len(parts) < 2 or parts[1] != "disk": continue
                path = parts[0]
                if path.startswith("/dev/nvme"): continue
                out = run_cmd(["smartctl", "-A", path], timeout=3)
                if not out: continue
                m = re.search(r"(?:Temperature_Celsius|Current Drive Temperature|Temperature:)\s*(?:.*?)(\d{2,3})\s*(?:$|[^\d])", out, re.M | re.I)
                if m:
                    drives.append({"name": parts[2] if len(parts)>2 else path,
                                   "temperature": float(m.group(1)), "type": "Disk", "source": "smartctl"})
    return drives

def sample():
    hw = scan_hwmon()
    cpu_temp = cpu_info(hw)
    gpus = read_amd_gpu()
    drives = storage_info()
    fans = []
    for hwmon in Path("/sys/class/hwmon").glob("hwmon*"):
        chip = label_from_hwmon(hwmon, hwmon.name)
        for f in hwmon.glob("fan*_input"):
            v = number(f)
            if v is not None and v >= 0:
                stem = f.name[:-6]
                label = read_text(hwmon / f"{stem}_label") or stem
                fans.append({"name": f"{chip} · {label}", "rpm": round(v)})
    temps = [{"name": s["name"], "temperature": s["temperature"], "source": s["source"]} for s in hw]
    return {
        "timestamp": time.time(),
        "cpu": {"temperature": cpu_temp, "utilization_percent": cpu_utilization()},
        "gpus": gpus,
        "sensors": temps,
        "fans": fans,
        "storage": drives,
        "power": {"cpu_w": read_cpu_power(), "gpu_w": next((g["power_w"] for g in gpus if g["power_w"] is not None), None)},
        "system": {"hostname": os.uname().nodename, "interval_seconds": INTERVAL},
        "capabilities": {
            "lm_sensors": bool(shutil.which("sensors")),
            "rocm_smi": bool(shutil.which("rocm-smi")),
            "smartctl": bool(shutil.which("smartctl")),
            "nvme_cli": bool(shutil.which("nvme"))
        }
    }

def cpu_utilization():
    # /proc/stat delta-based utilization.
    try:
        line = read_text("/proc/stat").splitlines()[0].split()
        vals = list(map(int, line[1:]))
        idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
        total = sum(vals)
        if not hasattr(cpu_utilization, "prev"):
            cpu_utilization.prev = (idle, total)
            return None
        pi, pt = cpu_utilization.prev
        cpu_utilization.prev = (idle, total)
        dt, di = total-pt, idle-pi
        return round(100 * (dt-di) / dt, 1) if dt > 0 else None
    except Exception:
        return None

def read_cpu_power():
    # Read Linux powercap energy counters if available; report average watts over sampling interval.
    root = Path("/sys/class/powercap")
    current = {}
    for p in root.glob("intel-rapl:*"):
        e = number(p / "energy_uj", 1_000_000)
        if e is not None:
            current[str(p)] = e
    # AMD systems may expose energy through hwmon; look for power1_average.
    if not current:
        vals = []
        for hm in Path("/sys/class/hwmon").glob("hwmon*"):
            name = (read_text(hm / "name") or "").lower()
            if "k10temp" in name: continue
            for f in hm.glob("power*_average"):
                v = number(f, 1_000_000)
                if v is not None and 0 < v < 2000: vals.append(v)
        return round(vals[0], 2) if vals else None
    now = time.time()
    previous = getattr(read_cpu_power, "prev", None)
    read_cpu_power.prev = (now, current)
    if not previous: return None
    then, old = previous
    watts = []
    for key, val in current.items():
        if key in old and val >= old[key] and now > then:
            watts.append((val-old[key]) / (now-then))
    return round(sum(watts), 2) if watts else None

def get_payload():
    data = sample()
    with history_lock:
        history.append({
            "timestamp": data["timestamp"],
            "cpu_temperature": data["cpu"]["temperature"],
            "cpu_utilization_percent": data["cpu"]["utilization_percent"],
            "gpu_temperature": max((t["temperature"] for g in data["gpus"] for t in g["temperatures"]), default=None),
            "gpu_w": data["power"]["gpu_w"],
            "cpu_w": data["power"]["cpu_w"]
        })
        cutoff = data["timestamp"] - HISTORY_SECONDS
        while history and history[0]["timestamp"] < cutoff: history.pop(0)
        data["history"] = list(history)
    return data

class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC), **kwargs)
    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/metrics":
            payload = json.dumps(get_payload()).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        elif path == "/api/health":
            payload = json.dumps({"ok": True}).encode()
            self.send_response(200); self.send_header("Content-Type","application/json")
            self.send_header("Content-Length",str(len(payload))); self.end_headers(); self.wfile.write(payload)
        else:
            if path == "/": self.path = "/index.html"
            super().do_GET()
    def log_message(self, fmt, *args):
        print("[%s] %s" % (self.log_date_time_string(), fmt % args))

if __name__ == "__main__":
    print(f"Thermal Workstation listening at http://{HOST}:{PORT}")
    print("Local-only server. Press Ctrl+C to stop.")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
