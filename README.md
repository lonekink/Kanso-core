# THERMAL.MON: Debian workstation monitor

A local-only browser dashboard for Linux hardware temperatures, fans, GPU/CPU telemetry and storage sensors. It uses Python's standard library; no Flask, npm, cloud service or third-party Python package is required.

## What it reads

- CPU and motherboard temperatures published through `/sys/class/hwmon`; optional `sensors` fallback.
- AMD GPU sensors published by the `amdgpu` kernel driver under `/sys/class/drm` and hwmon.
- Fan tachometer RPM where exposed through hwmon.
- NVMe controller temperatures from sysfs; optional SATA/SAS SMART readings via `smartctl`.
- CPU utilization from `/proc/stat`.
- Power only when the kernel publishes a usable power sensor. On AMD systems CPU package power is not universally exposed, so N/A is expected on some systems.
- Optional `rocm-smi` GPU utilization, if installed and compatible with the GPU/ROCm version.

It does not control fans, change clocks/voltages, or automatically shut down the computer. Alert thresholds are browser-side warnings and reset when the page is reloaded. History is held in RAM and resets when the server restarts.

## Install on Debian

From the project directory:

```bash
sudo apt update
sudo apt install lm-sensors smartmontools nvme-cli
```

Optional AMD GPU tooling (package availability varies by Debian release and GPU support):
```bash
sudo apt install rocm-smi
```
If that package is not available, skip it; sysfs GPU temperature sensors can still work.

Detect motherboard/CPU sensors:
```bash
sudo sensors-detect
sensors
```
Review prompts from `sensors-detect`; do not force-load modules you do not understand. Reboot if it recommends a module and you choose to enable it.

Run the app as your normal user:
```bash
python3 app.py
```
Open **http://127.0.0.1:8765** in a browser on the same machine.

Stop with `Ctrl+C`.

## Optional environment settings

```bash
THERMAL_PORT=8765 python3 app.py
THERMAL_INTERVAL=2 python3 app.py
THERMAL_HISTORY_SECONDS=3600 python3 app.py
```

The browser polls every 2 seconds. The server-side history retention is controlled by `THERMAL_HISTORY_SECONDS` (default one hour). The UI range selector can display up to one hour.

## Start automatically at login (systemd user service)

Run this from the project directory. It creates a user service using the current absolute path:

```bash
mkdir -p ~/.config/systemd/user
cat > ~/.config/systemd/user/thermal-monitor.service <<EOF
[Unit]
Description=THERMAL.MON local hardware monitor
After=default.target

[Service]
Type=simple
WorkingDirectory=$PWD
ExecStart=/usr/bin/python3 $PWD/app.py
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now thermal-monitor.service
```

Check status/logs:
```bash
systemctl --user status thermal-monitor.service
journalctl --user -u thermal-monitor.service -f
```
Stop or disable:
```bash
systemctl --user disable --now thermal-monitor.service
```

This is a user service and normally runs after you log in. To run it without an interactive login, enable lingering with `loginctl enable-linger "$USER"` (optional; not required for desktop login startup).

## Security

The server binds to `127.0.0.1` by default, so it is accessible only from the local machine. Do not change `THERMAL_HOST` to `0.0.0.0` unless you intentionally want to expose the dashboard to the network and have configured suitable access controls. The dashboard has no authentication.

## Troubleshooting

### No CPU / motherboard temperatures
```bash
sensors
find /sys/class/hwmon -maxdepth 2 -type f -name 'temp*_input' -print
```
The UI can only show temperatures exposed by the kernel and supported drivers. AMD CPU package temperature is often available through `k10temp`.

### No AMD GPU data
Confirm the GPU is bound to the `amdgpu` driver:
```bash
lspci -nnk | grep -A3 -Ei 'VGA|Display'
ls -l /sys/class/drm/card*/device/hwmon/
```
For many GPUs the kernel exports temperatures under `temp*_input`. Some sensors (hotspot, memory junction, fan speed, power) depend on the specific GPU, firmware, kernel and driver.

### Fan readings missing
Some motherboards expose fan RPM only after the appropriate sensor driver is loaded; some liquid-cooling controllers do not expose data to Linux at all.

### Storage readings missing
```bash
sudo smartctl -a /dev/sda
sudo nvme smart-log /dev/nvme0
```
Use the actual device path. Some SMART reads require elevated privileges. Do not run the whole dashboard as root just to get one inaccessible reading; use a narrow, reviewed permissions rule if needed.

### Port already in use
```bash
THERMAL_PORT=8766 python3 app.py
```
Then open `http://127.0.0.1:8766`.

## Project files

- `app.py`: local HTTP server and sensor collection.
- `static/index.html`: dashboard structure.
- `static/style.css`: industrial dark styling.
- `static/app.js`: polling, graph rendering and browser alerts.
