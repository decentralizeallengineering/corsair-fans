# corsair-fans

Temperature-driven fan control for a Corsair Commander Core on Linux, as a small user service on top of liquidctl.

The Commander's fans otherwise sit at a fixed hardware speed. This script gives you two zones: the radiator/CPU fans follow the
CPU temperature, and the case fans follow the hotter of the NVIDIA GPUs and the RAM. Speeds ramp linearly from a floor (60 %)
to 100 %. A failed temperature read counts as hot, the pump is never touched, and if liquidctl keeps failing an ALERT line
is written so you notice that fan control was lost. Python 3 standard library only; no root needed once liquidctl can reach the device
(a udev rule for the hidraw device).

## Install

```
pip install --user liquidctl          # or a venv; set LIQUIDCTL=/path/to/liquidctl if it is not on PATH
install -m 755 corsair_fans.py ~/.local/bin/
mkdir -p ~/.config/systemd/user ~/.config/corsair-fans
cp systemd/corsair-fans.service ~/.config/systemd/user/
cp etc/corsair-fans.conf.example ~/.config/corsair-fans/corsair-fans.conf
```

## Configure

Environment variables or `~/.config/corsair-fans/corsair-fans.conf` (plain `KEY=VALUE`): which fan channels belong to which zone
(`CPU_FANS`, `CASE_FANS`), the floor speed, the low/high temperatures of each ramp and the alert file. All keys are in
`etc/corsair-fans.conf.example`.

## Example

```
systemctl --user enable --now corsair-fans.service
journalctl --user -u corsair-fans.service -f
python3 corsair_fans.py --help
```

## Tests

`python3 -m pytest tests -q` (pure functions, no hardware).

## License

Apache-2.0, see `LICENSE` and `NOTICE`.
